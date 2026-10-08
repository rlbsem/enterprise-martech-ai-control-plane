"""Deterministic enterprise admission, fault schedule and multi-process drain; measured locally."""

import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx

from controlplane import db
from controlplane.operations import recovery_snapshot
from controlplane.policy import digest


def main():
    count = int(os.environ.get('CONTROL_WORKLOAD_ENTITIES', '1000'))
    workers = int(os.environ.get('CONTROL_WORKLOAD_WORKERS', '4'))
    if not 20 <= count <= 10000 or not 2 <= workers <= 8:
        raise ValueError('Bound workload to 20..10000 entities and 2..8 workers')
    with db.connect() as c:
        if c.execute('SELECT count(*) AS n FROM cp.customers').fetchone()['n']:
            raise RuntimeError('Use an empty, disposable workload database')
    tokens = json.loads(os.environ['API_TOKENS'])
    folder = Path('work/workload')
    folder.mkdir(parents=True, exist_ok=True)
    elapsed = []
    expected_dead = 0
    schedule = []
    modes = {0: '429', 1: '500', 2: 'timeout_before', 3: 'timeout_after', 4: '422'}
    start = time.monotonic()

    def entity(i):
        latencies = []
        subject = f'enterprise-account-{i:06}'
        with httpx.Client(base_url=os.environ['CONTROL_URL'], trust_env=False, timeout=20) as client:
            def post(route, body, role):
                tick = time.monotonic()
                response = client.post(route, json=body, headers={'Authorization': 'Bearer '+tokens[role]})
                latencies.append(time.monotonic()-tick)
                response.raise_for_status()
                return response.json()
            for sequence, stage in [(1, 'Lead'), (2, 'Opportunity')]:
                created = post('/events', {'event_id': f'{subject}-{sequence}', 'subject': subject,
                    'sequence': sequence, 'attributes': {'email': f'{subject}@example.test', 'lifecycle': stage,
                                                        'owner': f'commercial-unit-{i % 12}'}}, 'crm')
            body = {'request_id': str(uuid5(NAMESPACE_URL, f'enterprise-control/workload/v1/{i}')),
                    'customer_id': created['customer_id'], 'expected_revision': 2, 'action': 'sync_profile',
                    'rationale': 'Multi-product commercial account state synchronization'}
            proposal = post('/proposals', body, 'agent')
            if i % 10 == 0:
                assert post('/proposals', body, 'agent')['id'] == proposal['id']
            mode = modes.get(i % 20)
            if mode:
                response = httpx.post(os.environ['DOWNSTREAM_URL']+'/failure-plans',
                    headers={'Authorization': 'Bearer '+os.environ['INJECTOR_TOKEN']},
                    json={'key': proposal['id'], 'modes': [mode]}, trust_env=False, timeout=10)
                response.raise_for_status()
            return latencies, mode

    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, (latencies, mode) in enumerate(pool.map(entity, range(count))):
            elapsed.extend(latencies)
            schedule.append({'entity': i, 'mode': mode})
            expected_dead += mode == '422'
    admission_seconds = time.monotonic()-start
    processes, logs = [], []
    drain_start = time.monotonic()
    try:
        for i in range(workers):
            log = (folder / f'worker-{i}.log').open('w')
            logs.append(log)
            # Application credentials only. Workload harness retains its separate read authority.
            env = {k: v for k, v in os.environ.items() if k not in ['CONTROL_ADMIN_DSN', 'TEST_ADMIN_DSN',
                   'API_TOKENS', 'INJECTOR_TOKEN', 'REMOTE_DSN', 'APP_DB_PASSWORD', 'REMOTE_DB_PASSWORD']}
            processes.append(subprocess.Popen([sys.executable, '-m', 'controlplane', 'worker'], env=env,
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
        while time.monotonic()-drain_start < 600:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError('Worker exited; inspect local logs')
            with db.connect() as c:
                pending = c.execute("SELECT count(*) AS n FROM cp.jobs WHERE status IN ('ready','retry','leased')").fetchone()['n']
            if not pending:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Workload did not drain within its bounded window')
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=10)
        for log in logs:
            log.close()
    drain_seconds = time.monotonic()-drain_start
    with db.connect() as c:
        statuses = {r['status']: r['n'] for r in c.execute('SELECT status,count(*) AS n FROM cp.jobs GROUP BY status')}
        attempts = c.execute('SELECT sum(attempts) AS n FROM cp.jobs').fetchone()['n']
        reconciled = c.execute("SELECT count(*) AS n FROM cp.jobs WHERE reason='reconciled_committed_effect'").fetchone()['n']
        customers = c.execute('SELECT count(*) AS n FROM cp.customers').fetchone()['n']
    recovery = recovery_snapshot(os.environ['CONTROL_DSN'], os.environ['REMOTE_DSN'])
    assert statuses == {'succeeded': count-expected_dead, 'dead': expected_dead}, statuses
    assert customers == count and recovery['remote_effects'] == count-expected_dead
    assert recovery['safe_to_resume'] and reconciled == sum(r['mode'] == 'timeout_after' for r in schedule)
    result = {'profile': 'enterprise-v1', 'environment': 'local PostgreSQL and separate HTTP/worker processes',
              'entities': customers, 'intents': count, 'workers': workers, 'statuses': statuses,
              'worker_attempts': int(attempts), 'reconciled_effects': reconciled,
              'unique_remote_effects': recovery['remote_effects'], 'duplicate_effects': 0,
              'schedule_sha256': digest(schedule), 'admission_seconds': round(admission_seconds, 3),
              'drain_seconds': round(drain_seconds, 3), 'drain_intents_per_second': round(count/drain_seconds, 3),
              'admission_http_p50_ms': round(statistics.median(elapsed)*1000, 3),
              'admission_http_p95_ms': round(sorted(elapsed)[int(len(elapsed)*.95)]*1000, 3),
              'recovery_comparison': recovery}
    target = Path(os.environ.get('WORKLOAD_OUTPUT', 'work/workload/result.json'))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
