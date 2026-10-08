"""AWS task entrypoint. Credentials are retrieved at startup, never passed in task definitions."""

import json
import os
import secrets
import sys
import time

import boto3
from botocore.exceptions import ClientError
from psycopg import sql
from psycopg.conninfo import make_conninfo

from . import db, operations

ROLES = ('crm', 'consent', 'privacy', 'scoring', 'agent', 'approver', 'operator')
RUNTIME_KEYS = {
    'api': {'CONTROL_DSN', 'API_TOKENS'},
    'worker': {'CONTROL_DSN', 'EXECUTOR_TOKEN'},
    'remote': {'REMOTE_DSN', 'EXECUTOR_TOKEN', 'INJECTOR_TOKEN'},
    'verifier': {'CONTROL_DSN', 'REMOTE_DSN'},
    'observer': {'CONTROL_DSN'},
}


def read_secret(client, secret_id):
    result = json.loads(client.get_secret_value(SecretId=secret_id)['SecretString'])
    if not isinstance(result, dict) or not result:
        raise ValueError('Invalid secret document')
    return result


def configure(service, client):
    document = read_secret(client, os.environ['RUNTIME_SECRET_ARN'])
    if set(document) != RUNTIME_KEYS[service] or any(not isinstance(v, str) or not v for v in document.values()):
        raise ValueError('Invalid role secret contract')
    os.environ.update(document)


def role(c, name, password):
    if not c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (name,)).fetchone():
        c.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(name)))
    c.execute(sql.SQL('ALTER ROLE {} PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
              .format(sql.Identifier(name), sql.Literal(password)))


def grants(control_dsn, remote_dsn):
    """Additional cloud logins; v1 migration and local authorities stay unchanged."""
    with db.connect(control_dsn) as c:
        c.execute('GRANT USAGE ON SCHEMA cp TO control_api,control_worker,control_audit')
        c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA cp TO control_api,control_worker,control_audit')
        c.execute('GRANT INSERT,UPDATE ON cp.customers,cp.identities,cp.proposals,cp.jobs,cp.exceptions TO control_api')
        c.execute('GRANT INSERT ON cp.events,cp.audit TO control_api')
        c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA cp TO control_api,control_worker')
        c.execute('GRANT UPDATE ON cp.jobs TO control_worker')
        # SELECT FOR UPDATE requires an UPDATE privilege even though the worker never changes customer state.
        c.execute('GRANT UPDATE (revision) ON cp.customers TO control_worker')
        c.execute('GRANT INSERT ON cp.audit,cp.exceptions TO control_worker')
        c.execute('GRANT USAGE ON SCHEMA cp TO control_observer')
        c.execute('GRANT SELECT ON cp.jobs TO control_observer')
    with db.connect(remote_dsn) as c:
        c.execute('GRANT USAGE ON SCHEMA remote TO remote_audit')
        c.execute('GRANT SELECT ON remote.effects TO remote_audit')


def bootstrap(client):
    """One-shot task: migrations and initial secret creation, idempotent on partial failure.

    Runtime secret values are created BEFORE applying DB passwords so restart uses the same values.
    Services remain at zero replicas until this task exits successfully.
    """
    runtime_arns = json.loads(os.environ['RUNTIME_SECRET_ARNS'])
    masters = json.loads(os.environ['DB_MASTER_SECRET_ARNS'])
    hosts = json.loads(os.environ['DB_HOSTS'])
    dsns = {}
    for name in ('control', 'downstream'):
        master = read_secret(client, masters[name])
        dsns[name] = make_conninfo(host=hosts[name], dbname='control', user=master['username'],
                                  password=master['password'], sslmode='verify-full',
                                  sslrootcert='/app/certs/rds-global-bundle.pem')
    passwords = {key: secrets.token_urlsafe(36) for key in ('api', 'worker', 'remote', 'audit', 'remote_audit', 'observer')}
    def login(database, username, password):
        return make_conninfo(dsns[database], user=username, password=password)
    executor = secrets.token_urlsafe(36)
    # Either side can have survived an interrupted bootstrap. Keep its stable shared token.
    for key in ('worker', 'remote'):
        try:
            executor = read_secret(client, runtime_arns[key])['EXECUTOR_TOKEN']
            break
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'ResourceNotFoundException':
                raise
    initial = {
        'api': {'CONTROL_DSN': login('control', 'control_api', passwords['api']),
                'API_TOKENS': json.dumps({r: secrets.token_urlsafe(36) for r in ROLES})},
        'worker': {'CONTROL_DSN': login('control', 'control_worker', passwords['worker']), 'EXECUTOR_TOKEN': executor},
        'remote': {'REMOTE_DSN': login('downstream', 'synthetic_remote', passwords['remote']),
                   'EXECUTOR_TOKEN': executor, 'INJECTOR_TOKEN': secrets.token_urlsafe(36)},
        'verifier': {'CONTROL_DSN': login('control', 'control_audit', passwords['audit']),
                     'REMOTE_DSN': login('downstream', 'remote_audit', passwords['remote_audit'])},
        'observer': {'CONTROL_DSN': login('control', 'control_observer', passwords['observer'])},
    }
    documents = {}
    for key, arn in runtime_arns.items():
        try:
            documents[key] = read_secret(client, arn)
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'ResourceNotFoundException':
                raise
            # Empty Secrets Manager resources return ResourceNotFoundException until first version.
            document = initial[key]
            if key == 'remote' and 'worker' in documents:
                document['EXECUTOR_TOKEN'] = documents['worker']['EXECUTOR_TOKEN']
            client.put_secret_value(SecretId=arn, SecretString=json.dumps(document))
            documents[key] = document
    if documents['remote']['EXECUTOR_TOKEN'] != documents['worker']['EXECUTOR_TOKEN']:
        raise ValueError('Executor credential mismatch; rotation requires coordinated service restart')
    from psycopg.conninfo import conninfo_to_dict
    # A reviewed PITR binding supplies the new host via Terraform. Keep passwords and effect keys stable.
    for key, document in documents.items():
        revised = dict(document)
        for field, value in document.items():
            if field.endswith('_DSN'):
                info = conninfo_to_dict(value)
                database = 'downstream' if info['user'] in ('synthetic_remote', 'remote_audit') else 'control'
                revised[field] = make_conninfo(value, host=hosts[database], sslmode='verify-full',
                                               sslrootcert='/app/certs/rds-global-bundle.pem')
        if revised != document:
            client.put_secret_value(SecretId=runtime_arns[key], SecretString=json.dumps(revised))
            documents[key] = revised
    for database, dsn in dsns.items():
        with db.connect(dsn) as c:
            for name in ('control_app', 'synthetic_remote'):
                if not c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (name,)).fetchone():
                    role(c, name, secrets.token_urlsafe(36))
            for document in documents.values():
                for key, value in document.items():
                    if key.endswith('_DSN'):
                        info = conninfo_to_dict(value)
                        if info['host'] == hosts[database]:
                            role(c, info['user'], info['password'])
        db.migrate(dsn)
        with db.connect(dsn) as c:
            c.execute('ALTER ROLE control_app NOLOGIN')
            if database == 'control':
                c.execute('ALTER ROLE synthetic_remote NOLOGIN')
    grants(dsns['control'], dsns['downstream'])


def main():
    service = sys.argv[1]
    try:
        client = boto3.client('secretsmanager')
        if service == 'migration':
            bootstrap(client)
            print(json.dumps({'migration': 'complete'}))
            return
        configure(service, client)
        if service in ('api', 'remote'):
            import uvicorn
            uvicorn.run(f'controlplane.{"api" if service == "api" else "mock"}:app',
                        host='0.0.0.0', port=8000, access_log=False)
        elif service == 'worker':
            from .worker import run
            run()
        elif service == 'observer':
            while True:
                operations.emit(operations.queue_metrics(), 'observer', os.environ['ENVIRONMENT'])
                time.sleep(30)
        elif service == 'verifier':
            result = operations.recovery_snapshot(os.environ['CONTROL_DSN'], os.environ['REMOTE_DSN'])
            # Structured report contains only stable keys/counts/hashes; no customer payload.
            print(json.dumps(result))
            if not result['safe_to_resume']:
                raise RuntimeError('Reconciliation requires review')
        else:
            raise ValueError('Unknown task responsibility')
    except Exception:
        # SDK / connection errors can contain credentials. Preserve a bounded operational signal instead.
        operations.emit({'RuntimeError': 1}, service, os.environ.get('ENVIRONMENT', 'unknown'))
        raise SystemExit('Task failed closed; inspect service authority, connectivity and recovery findings') from None


if __name__ == '__main__':
    main()
