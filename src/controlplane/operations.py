"""Read-only recovery comparison and bounded operational measurements.

Receipt identity is a business fact. Recovery never creates an effect or invents a new key.
"""

import json
import time
from datetime import UTC, datetime

from . import db
from .policy import digest


def compare_receipts(jobs, receipts):
    local = {str(j['id']): j for j in jobs}
    remote = {str(r['key']): r for r in receipts}
    findings = []
    for key, receipt in sorted(remote.items()):
        # SQL snapshots include the independently persisted business body, not just
        # the sender's asserted hash. Minimal legacy in-memory comparisons remain compatible.
        if 'body' in receipt and digest(receipt['body']) != receipt['hash']:
            findings.append({'key': key, 'reason': 'receipt_body_hash_conflict'})
    for key, receipt in sorted(remote.items()):
        job = local.get(key)
        if not job:
            findings.append({'key': key, 'reason': 'remote_ahead_missing_intent'})
        elif not job['mutation'] or digest(job['mutation']) != receipt['hash']:
            findings.append({'key': key, 'reason': 'remote_identity_conflict'})
        elif job['status'] != 'succeeded' and not job['uncertain']:
            findings.append({'key': key, 'reason': 'remote_ahead_not_reconcilable'})
        elif job['status'] in ('dead', 'blocked'):
            findings.append({'key': key, 'reason': 'terminal_intent_requires_review'})
    for key, job in sorted(local.items()):
        if job['status'] == 'succeeded' and key not in remote:
            findings.append({'key': key, 'reason': 'acknowledged_receipt_missing'})
        if job['status'] == 'succeeded' and key in remote:
            result = job.get('remote_result') or {}
            if str(result.get('receipt')) != str(remote[key]['receipt']):
                findings.append({'key': key, 'reason': 'receipt_identity_changed'})
    return {'safe_to_resume': not findings, 'local_intents': len(local), 'remote_effects': len(remote),
            'findings': findings, 'snapshot_hash': digest({'jobs': jobs, 'receipts': receipts})}


def recovery_snapshot(control_dsn, remote_dsn):
    # Both services MUST already be quiesced. Two databases have no shared snapshot clock.
    if isinstance(remote_dsn, dict):
        return destination_snapshot(control_dsn, remote_dsn)
    with db.connect(control_dsn) as c, db.connect(remote_dsn) as r:
        for connection in (c, r):
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        if c.execute('SELECT 1 FROM cp.destinations LIMIT 1').fetchone():
            raise ValueError('all_destination_readers_required')
        jobs = c.execute('SELECT id,status,mutation,uncertain,remote_result FROM cp.jobs ORDER BY id').fetchall()
        receipts = r.execute('SELECT key,hash,body,receipt FROM remote.effects ORDER BY key').fetchall()
    return {**compare_receipts(jobs, receipts), 'observed_at': datetime.now(UTC).isoformat(),
            'scope': 'quiesced databases; no changes or replay performed'}


def destination_snapshot(control_dsn, readers):
    """Exact configured scope plus the preserved legacy ledger; all writers must be closed."""
    with db.connect(control_dsn) as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        expected = {r['id'] for r in c.execute('SELECT id FROM cp.destinations')} | {'__legacy__'}
        if set(readers) != expected:
            raise ValueError('all_destination_readers_required')
        jobs = c.execute('''SELECT j.id,j.status,j.mutation,j.uncertain,j.remote_result,
            coalesce(r.destination,'__legacy__') AS destination FROM cp.jobs j
            LEFT JOIN cp.job_routes r USING(id) ORDER BY j.id''').fetchall()
    lanes = {}
    for name in sorted(readers):
        with db.connect(readers[name]) as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            receipts = c.execute('SELECT key,hash,body,receipt FROM remote.effects ORDER BY key').fetchall()
        lanes[name] = compare_receipts([j for j in jobs if j['destination'] == name], receipts)
    return {'safe_to_resume': all(lane['safe_to_resume'] for lane in lanes.values()),
            'destinations': lanes, 'observed_at': datetime.now(UTC).isoformat(),
            'scope': 'quiesced databases; exact destination scope including legacy; read only'}


def runtime_acceptance_snapshot(control_dsn, remote_dsn):
    """Deployment acceptance is stricter than a closed recovery comparison."""
    result = recovery_snapshot(control_dsn, remote_dsn)
    with db.connect(control_dsn) as c:
        gate = c.execute('SELECT mode,epoch,session_id FROM cp.recovery_gate WHERE singleton').fetchone()
    result['execution_gate'] = {**gate, 'session_id': str(gate['session_id']) if gate['session_id'] else None}
    if gate['mode'] != 'open':
        result['safe_to_resume'] = False
        result.setdefault('findings', []).append({'reason': 'execution_gate_closed'})
    return result


def queue_metrics(dsn=None):
    with db.connect(dsn) as c:
        row = c.execute("""SELECT count(*) FILTER (WHERE status IN ('ready','retry','leased')) AS backlog,
            count(*) FILTER (WHERE uncertain AND status <> 'succeeded') AS uncertain,
            count(*) FILTER (WHERE status='dead') AS dead,
            coalesce(max(extract(epoch FROM clock_timestamp()-available_at)) FILTER
              (WHERE status IN ('ready','retry') AND available_at<=clock_timestamp()),0) AS eligible_age
            FROM cp.jobs""").fetchone()
    return {'Backlog': row['backlog'], 'Uncertain': row['uncertain'], 'Dead': row['dead'],
            'EligibleAgeSeconds': float(row['eligible_age'])}


METRIC_UNITS = {'Backlog': 'Count', 'Uncertain': 'Count', 'Dead': 'Count', 'EligibleAgeSeconds': 'Seconds',
                'WorkerPoll': 'Count', 'RuntimeError': 'Count', 'ApiError': 'Count', 'Retry': 'Count',
                'DependencyFailure': 'Count', 'Reconciled': 'Count'}


def emit(metrics, service, environment):
    """EMF uses only environment/service dimensions; no customer IDs, payloads or credentials."""
    if service not in {'api', 'worker', 'remote', 'observer', 'migration', 'verifier'}:
        raise ValueError('Unknown service')
    if not metrics or set(metrics) - METRIC_UNITS.keys():
        raise ValueError('Unknown metric')
    record = {'_aws': {'Timestamp': int(time.time() * 1000), 'CloudWatchMetrics': [{
        'Namespace': 'EnterpriseControl', 'Dimensions': [['Environment', 'Service']],
        'Metrics': [{'Name': k, 'Unit': METRIC_UNITS[k]} for k in metrics]}]},
        'Environment': environment, 'Service': service, **metrics}
    print(json.dumps(record), flush=True)
    return record
