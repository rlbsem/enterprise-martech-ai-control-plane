"""Cloud boundaries use SDK stubs; SQL and HTTP assertions still execute on PostgreSQL."""

import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import boto3
import psycopg
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from psycopg.conninfo import make_conninfo

from controlplane import cloud, db, operations, worker
from controlplane.policy import digest
from controlplane.release import Release
from controlplane.restore import restore_request
from conftest import customer, effects, job, propose


def secrets_client():
    return boto3.client('secretsmanager', region_name='ca-central-1',
                        aws_access_key_id='mock', aws_secret_access_key='mock')


@pytest.mark.parametrize('error', ['AccessDeniedException', 'ResourceNotFoundException', 'DecryptionFailure'])
def test_credentials_fail_closed(monkeypatch, error):
    client = secrets_client()
    monkeypatch.setenv('RUNTIME_SECRET_ARN', 'mock-secret')
    with Stubber(client) as stub:
        stub.add_client_error('get_secret_value', error, expected_params={'SecretId': 'mock-secret'})
        with pytest.raises(ClientError):
            cloud.configure('worker', client)


def test_secret_contract_rejects_authority_escalation(monkeypatch):
    client = secrets_client()
    monkeypatch.setenv('RUNTIME_SECRET_ARN', 'mock-secret')
    with Stubber(client) as stub:
        stub.add_response('get_secret_value', {'SecretString': json.dumps({
            'CONTROL_DSN': 'mock', 'EXECUTOR_TOKEN': 'mock', 'CONTROL_ADMIN_DSN': 'forbidden'})},
            {'SecretId': 'mock-secret'})
        with pytest.raises(ValueError, match='contract'):
            cloud.configure('worker', client)


def provision_roles():
    admin = os.environ['TEST_ADMIN_DSN']
    with db.connect(admin) as c:
        for name in ('control_api', 'control_worker', 'control_audit', 'remote_audit', 'control_observer'):
            cloud.role(c, name, 'local-cloud-role-only')
    cloud.grants(admin, admin)
    return admin


def test_cloud_worker_executes_but_cannot_forge_source_state(api, monkeypatch):
    admin = provision_roles()
    cid = customer(api)
    pid = propose(api, cid)['id']
    dsn = make_conninfo(admin, user='control_worker', password='local-cloud-role-only')
    monkeypatch.setenv('CONTROL_DSN', dsn)
    assert worker.once()
    assert job(pid)['status'] == 'succeeded'
    assert effects(pid) == 1
    for statement in ['INSERT INTO cp.events SELECT * FROM cp.events',
                      "UPDATE cp.customers SET state='{}'::jsonb", 'SELECT * FROM remote.effects',
                      'DELETE FROM cp.audit']:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.connect(dsn) as c:
            c.execute(statement)


def test_auditor_can_compare_but_cannot_replay(api):
    admin = provision_roles()
    pid = propose(api, customer(api))['id']
    worker.once()
    audit = make_conninfo(admin, user='control_audit', password='local-cloud-role-only')
    remote = make_conninfo(admin, user='remote_audit', password='local-cloud-role-only')
    assert operations.recovery_snapshot(audit, remote)['safe_to_resume']
    for dsn, statement in [(audit, 'UPDATE cp.jobs SET attempts=0'),
                           (remote, 'DELETE FROM remote.effects'), (audit, 'SELECT * FROM remote.effects')]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.connect(dsn) as c:
            c.execute(statement)
    assert effects(pid) == 1


def test_remote_commit_survives_logical_restore_and_blocks_resume(api, evidence):
    pid = propose(api, customer(api))['id']
    worker.once()
    assert effects(pid) == 1
    # Restore-point surrogate: control database is rolled back before the proposal, independent receipts survive.
    # Production RDS PITR is documented separately; this is an executable remote-ahead boundary test.
    with db.connect(os.environ['TEST_ADMIN_DSN']) as c:
        c.execute('DELETE FROM cp.jobs WHERE id=%s', (pid,))
        c.execute('DELETE FROM cp.proposals WHERE id=%s', (pid,))
    result = operations.recovery_snapshot(os.environ['CONTROL_DSN'], os.environ['REMOTE_DSN'])
    assert not result['safe_to_resume']
    assert result['findings'] == [{'key': pid, 'reason': 'remote_ahead_missing_intent'}]
    assert not worker.once()
    assert effects(pid) == 1
    evidence('remote-ahead-restore', result)


@pytest.mark.parametrize('fault,reason', [
    ('hash', 'remote_identity_conflict'), ('ack', 'acknowledged_receipt_missing'),
    ('receipt', 'receipt_identity_changed'), ('uncertain', 'remote_ahead_not_reconcilable'),
    ('terminal', 'terminal_intent_requires_review')])
def test_recovery_conflicts_require_review(fault, reason):
    key, receipt = str(uuid4()), str(uuid4())
    mutation = {'customer_id': 'example', 'action': 'sync_profile'}
    jobs = [{'id': key, 'status': 'succeeded', 'uncertain': False, 'mutation': mutation,
             'remote_result': {'receipt': receipt}}]
    receipts = [{'key': key, 'hash': digest(mutation), 'receipt': receipt}]
    if fault == 'hash':
        receipts[0]['hash'] = 'wrong'
    elif fault == 'ack':
        receipts = []
    elif fault == 'receipt':
        receipts[0]['receipt'] = 'wrong'
    else:
        jobs[0]['status'] = 'retry' if fault == 'uncertain' else 'dead'
        jobs[0]['uncertain'] = fault == 'terminal'
    assert reason in {f['reason'] for f in operations.compare_receipts(jobs, receipts)['findings']}


def test_uncertain_matching_receipt_is_reconcilable():
    mutation = {'action': 'sync_profile'}
    assert operations.compare_receipts(
        [{'id': 'key', 'status': 'retry', 'mutation': mutation, 'uncertain': True}],
        [{'key': 'key', 'hash': digest(mutation), 'receipt': 'receipt'}])['safe_to_resume']


def test_rejected_executor_does_not_create_effect(api, monkeypatch):
    pid = propose(api, customer(api))['id']
    monkeypatch.setenv('EXECUTOR_TOKEN', 'credential-revoked')
    worker.once()
    assert job(pid)['reason'] == 'remote_rejected'
    assert effects(pid) == 0


def test_emf_dimensions_are_bounded_and_queue_age_is_measured(api):
    propose(api, customer(api))
    record = operations.emit(operations.queue_metrics(), 'observer', 'stage')
    assert record['Backlog'] == 1
    assert record['EligibleAgeSeconds'] >= 0
    assert record['_aws']['CloudWatchMetrics'][0]['Dimensions'] == [['Environment', 'Service']]
    with pytest.raises(ValueError):
        operations.emit({'CustomerId': 1}, 'observer', 'stage')


def manifest():
    names = ['api', 'worker', 'remote', 'observer']
    return {'commit': 'a' * 40, 'image': '123456789012.dkr.ecr.ca-central-1.amazonaws.com/control-stage@sha256:' + 'a'*64,
            'cluster': 'mock-cluster', 'services': {n: n for n in names}, 'replicas': {n: 1 for n in names},
            'tasks': {n: n+'-task' for n in names+['migration', 'verifier']}, 'region': 'ca-central-1',
            'groups': {'verifier': 'mock-sg'}, 'subnets': ['mock-subnet']}


def test_resume_never_opens_services_when_verifier_fails():
    release = Release(manifest(), Mock())
    release.validate_tasks = Mock()
    release.assert_drained = Mock()
    release.utility = Mock(side_effect=RuntimeError('remote ahead'))
    with pytest.raises(RuntimeError, match='remote ahead'):
        release.resume()
    release.ecs.update_service.assert_not_called()


def test_busy_cluster_cannot_be_declared_reconciled():
    release = Release(manifest(), Mock())
    release.services = Mock(return_value=[{'desiredCount': 0, 'runningCount': 1, 'pendingCount': 0}])
    with pytest.raises(RuntimeError, match='drained'):
        release.assert_drained()
    release.ecs.run_task.assert_not_called()


def test_task_revision_cannot_substitute_mutable_or_other_image():
    release = Release(manifest(), Mock())
    release.ecs.describe_task_definition.return_value = {'taskDefinition': {'containerDefinitions': [{'image': 'latest'}]}}
    with pytest.raises(RuntimeError, match='image'):
        release.validate_tasks()
    bad = deepcopy(manifest())
    bad['image'] = 'latest'
    with pytest.raises(ValueError, match='digest'):
        Release(bad, Mock())


def test_restore_request_preserves_private_independent_boundary():
    now = datetime.now(UTC)
    source = {'DBInstanceIdentifier': 'control-stage-control', 'Engine': 'postgres', 'StorageEncrypted': True,
              'PubliclyAccessible': False, 'EarliestRestorableTime': now-timedelta(days=1),
              'LatestRestorableTime': now, 'DBInstanceClass': 'db.t4g.medium',
              'DBSubnetGroup': {'DBSubnetGroupName': 'private'},
              'VpcSecurityGroups': [{'VpcSecurityGroupId': 'sg-mock'}],
              'DBParameterGroups': [{'DBParameterGroupName': 'force-tls'}], 'MultiAZ': True,
              'MasterUserSecret': {'KmsKeyId': 'mock-kms'}}
    request = restore_request(source, 'control-stage-restore-incident', now-timedelta(minutes=10))
    assert request['SourceDBInstanceIdentifier'] != request['TargetDBInstanceIdentifier']
    assert request['DeletionProtection'] and not request['PubliclyAccessible']
    assert request['ManageMasterUserPassword']
    with pytest.raises(ValueError, match='window'):
        restore_request(source, 'control-stage-restore-incident', now+timedelta(days=1))
    with pytest.raises(ValueError, match='distinct'):
        restore_request(source, source['DBInstanceIdentifier'], now)


def test_api_cloud_role_can_admit_but_cannot_touch_remote(api, monkeypatch):
    from controlplane.contracts import SourceEvent
    from controlplane.service import ingest
    admin = provision_roles()
    dsn = make_conninfo(admin, user='control_api', password='local-cloud-role-only')
    monkeypatch.setenv('CONTROL_DSN', dsn)
    result = ingest('crm', SourceEvent(event_id='cloud-role', subject='enterprise-account', sequence=1,
                                     attributes={'lifecycle': 'Opportunity', 'email': 'account@example.test'}))
    assert result['customer_id']
    for statement in ['SELECT * FROM remote.effects', 'UPDATE cp.policies SET hash=hash',
                      'UPDATE cp.events SET hash=hash', 'TRUNCATE cp.audit']:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.connect(dsn) as c:
            c.execute(statement)


def test_observer_has_only_queue_read_authority():
    admin = provision_roles()
    dsn = make_conninfo(admin, user='control_observer', password='local-cloud-role-only')
    assert operations.queue_metrics(dsn)['Backlog'] == 0
    for statement in ['SELECT * FROM cp.customers', 'SELECT * FROM remote.effects', 'UPDATE cp.jobs SET attempts=0']:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.connect(dsn) as c:
            c.execute(statement)


@pytest.mark.parametrize('restored', [False, True])
def test_bootstrap_reuses_partial_credentials_and_rebinds_only_hosts(monkeypatch, restored):
    from psycopg.conninfo import conninfo_to_dict
    client = Mock()
    host = 'restored.control.test' if restored else 'control.test'
    documents = {
        'master-control': {'username': 'owner', 'password': 'mock-owner'},
        'master-remote': {'username': 'owner', 'password': 'mock-owner'},
        'worker': {'CONTROL_DSN': 'host=control.test dbname=control user=control_worker password=preserved',
                   'EXECUTOR_TOKEN': 'preserved-executor-token'},
    }
    def get(SecretId):
        if SecretId not in documents:
            raise ClientError({'Error': {'Code': 'ResourceNotFoundException', 'Message': 'Empty fixture'}},
                              'GetSecretValue')
        return {'SecretString': json.dumps(documents[SecretId])}
    def put(SecretId, SecretString):
        documents[SecretId] = json.loads(SecretString)
    client.get_secret_value.side_effect = get
    client.put_secret_value.side_effect = put
    monkeypatch.setenv('RUNTIME_SECRET_ARNS', json.dumps({k: k for k in ['api', 'observer', 'remote', 'verifier', 'worker']}))
    monkeypatch.setenv('DB_MASTER_SECRET_ARNS', json.dumps({'control': 'master-control', 'downstream': 'master-remote'}))
    monkeypatch.setenv('DB_HOSTS', json.dumps({'control': host, 'downstream': 'remote.test'}))
    monkeypatch.setattr(db, 'connect', MagicMock())
    monkeypatch.setattr(db, 'migrate', Mock())
    monkeypatch.setattr(cloud, 'grants', Mock())
    cloud.bootstrap(client)
    assert documents['worker']['EXECUTOR_TOKEN'] == documents['remote']['EXECUTOR_TOKEN'] == 'preserved-executor-token'
    info = conninfo_to_dict(documents['worker']['CONTROL_DSN'])
    assert info['host'] == host and info['password'] == 'preserved' and info['sslmode'] == 'verify-full'
    assert set(documents['observer']) == {'CONTROL_DSN'}
    before = deepcopy(documents)
    cloud.bootstrap(client)
    assert before == documents
