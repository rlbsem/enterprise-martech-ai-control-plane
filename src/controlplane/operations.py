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
    with db.connect(control_dsn) as c, db.connect(remote_dsn) as r:
        for connection in (c, r):
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        jobs = c.execute('SELECT id,status,mutation,uncertain,remote_result FROM cp.jobs ORDER BY id').fetchall()
        receipts = r.execute('SELECT key,hash,receipt FROM remote.effects ORDER BY key').fetchall()
    return {**compare_receipts(jobs, receipts), 'observed_at': datetime.now(UTC).isoformat(),
            'scope': 'quiesced databases; no changes or replay performed'}


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
