"""Offline AWS operational contracts. No native cloud deployment is claimed."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from controlplane import cloud, release
from controlplane.restore import restore_request
from test_cloud_operations import manifest


def task_definition(m, name):
    return {'status': 'ACTIVE', 'containerDefinitions': [{'image': m['image'],
        'command': ['python', '-m', 'controlplane.cloud', name],
        'environment': [{'name': 'SOURCE_COMMIT', 'value': m['commit']}]}]}


@pytest.mark.parametrize('fault', ['entrypoint', 'plaintext', 'secret_injection', 'duplicate_env', 'inactive'])
def test_approved_image_cannot_hide_a_different_runtime(fault):
    m, ecs = manifest(), Mock()
    task = task_definition(m, 'api')
    container = task['containerDefinitions'][0]
    if fault == 'entrypoint':
        container['entryPoint'] = ['sh', '-c']
    elif fault == 'plaintext':
        container['environment'].append({'name': 'CONTROL_ADMIN_DSN', 'value': 'private-placeholder'})
    elif fault == 'secret_injection':
        container['secrets'] = [{'name': 'CONTROL_ADMIN_DSN', 'valueFrom': 'unapproved-secret'}]
    elif fault == 'duplicate_env':
        container['environment'].append(dict(container['environment'][0]))
    else:
        task['status'] = 'INACTIVE'
    ecs.describe_task_definition.return_value = {'taskDefinition': task}
    with pytest.raises(RuntimeError):
        release.Release(m, ecs).validate_tasks()
    ecs.update_service.assert_not_called()
    ecs.run_task.assert_not_called()


def test_declared_task_revision_accepts_only_expected_entrypoint():
    m, ecs = manifest(), Mock()
    ecs.describe_task_definition.side_effect = lambda taskDefinition: {
        'taskDefinition': task_definition(m, taskDefinition.removesuffix('-task'))}
    release.Release(m, ecs).validate_tasks()
    assert ecs.describe_task_definition.call_count == 6


@pytest.mark.parametrize('replicas', [True, False, 0, 9, 1.5])
def test_replica_count_is_a_bounded_integer(replicas):
    m = manifest()
    m['replicas']['worker'] = replicas
    with pytest.raises(ValueError, match='Replica'):
        release.validate_manifest(m)


def utility_ecs():
    ecs = Mock()
    ecs.run_task.return_value = {'tasks': [{'taskArn': 'owned-task'}]}
    ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'owned-task', 'lastStatus': 'STOPPED',
        'taskDefinitionArn': 'verifier-task', 'containers': [{'exitCode': 0}]}]}
    return ecs


def test_utility_requires_independent_matching_stopped_result():
    ecs = utility_ecs()
    assert release.Release(manifest(), ecs).utility('verifier') == 'owned-task'
    ecs.describe_tasks.assert_called_once_with(cluster='mock-cluster', tasks=['owned-task'])


@pytest.mark.parametrize('fault', ['wrong_task', 'wrong_revision', 'still_running', 'failed_container',
                                   'missing_task', 'api_failure'])
def test_successful_run_task_response_is_not_acceptance(fault):
    ecs = utility_ecs()
    response = ecs.describe_tasks.return_value
    task = response['tasks'][0]
    if fault == 'wrong_task':
        task['taskArn'] = 'other-task'
    elif fault == 'wrong_revision':
        task['taskDefinitionArn'] = 'different-task'
    elif fault == 'still_running':
        task['lastStatus'] = 'RUNNING'
    elif fault == 'failed_container':
        task['containers'][0]['exitCode'] = 1
    elif fault == 'missing_task':
        response['tasks'] = []
    else:
        response['failures'] = [{'reason': 'not_visible'}]
    with pytest.raises(RuntimeError):
        release.Release(manifest(), ecs).utility('verifier')


def test_waiter_failure_stops_exact_task_and_does_not_open_services():
    ecs = utility_ecs()
    ecs.get_waiter.return_value.wait.side_effect = RuntimeError('private SDK diagnostic')
    with pytest.raises(RuntimeError, match='termination must be confirmed'):
        release.Release(manifest(), ecs).utility('verifier')
    ecs.stop_task.assert_called_once_with(cluster='mock-cluster', task='owned-task', reason='Bounded utility wait failed')
    ecs.update_service.assert_not_called()


def test_partial_launch_stops_only_tasks_returned_by_own_launch():
    ecs = utility_ecs()
    ecs.run_task.return_value['failures'] = [{'reason': 'partial'}]
    with pytest.raises(RuntimeError):
        release.Release(manifest(), ecs).utility('verifier')
    assert ecs.stop_task.call_args.kwargs['task'] == 'owned-task'
    ecs.get_waiter.assert_not_called()


def test_stop_failure_is_explicit_and_does_not_expose_sdk_text():
    ecs = utility_ecs()
    ecs.get_waiter.return_value.wait.side_effect = RuntimeError('sensitive-detail')
    ecs.stop_task.side_effect = RuntimeError('sensitive-detail')
    with pytest.raises(RuntimeError, match='explicit cleanup required') as error:
        release.Release(manifest(), ecs).utility('verifier')
    assert 'sensitive-detail' not in str(error.value)


def test_runtime_configuration_removes_inherited_other_role_credentials(monkeypatch):
    import os
    for name in set().union(*cloud.RUNTIME_KEYS.values()) | {'APP_DB_PASSWORD', 'REMOTE_DB_PASSWORD'}:
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
    for name in ('CONTROL_ADMIN_DSN', 'REMOTE_DSN', 'EXECUTOR_TOKEN', 'INJECTOR_TOKEN', 'TEST_ADMIN_DSN'):
        monkeypatch.setenv(name, 'should-not-survive')
    monkeypatch.setenv('RUNTIME_SECRET_ARN', 'api-secret')
    monkeypatch.setattr(cloud, 'read_secret', lambda *args: {'CONTROL_DSN': 'scoped-api', 'API_TOKENS': '{}'})
    cloud.configure('api', Mock())
    assert os.environ['CONTROL_DSN'] == 'scoped-api'
    assert not any(k in os.environ for k in ('CONTROL_ADMIN_DSN', 'REMOTE_DSN', 'EXECUTOR_TOKEN',
                                            'INJECTOR_TOKEN', 'TEST_ADMIN_DSN'))


def test_release_failure_creates_sanitized_immutable_record(tmp_path, monkeypatch, capsys):
    m = manifest()
    m['cluster'] = 'arn:aws:ecs:ca-central-1:123456789012:cluster/private'
    source, record = tmp_path/'manifest.json', tmp_path/'record.json'
    source.write_text(json.dumps(m))
    ecs = Mock()
    ecs.describe_task_definition.side_effect = RuntimeError('private endpoint and secret')
    monkeypatch.setattr(release.boto3, 'client', lambda *a, **kw: ecs)
    monkeypatch.setattr('sys.argv', ['release', 'verify', '--manifest', str(source), '--record', str(record)])
    assert release.main() == 1
    content = record.read_text()
    assert json.loads(content)['status'] == 'FAILED'
    assert all(v not in content for v in (m['cluster'], '123456789012', 'private endpoint', 'secret'))
    assert 'private endpoint' not in capsys.readouterr().out
    before = record.read_bytes()
    with pytest.raises(SystemExit):
        release.main()
    assert before == record.read_bytes()


def test_restore_cannot_cross_environment():
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    source = {'DBInstanceIdentifier': 'control-stage-control', 'Engine': 'postgres',
              'StorageEncrypted': True, 'PubliclyAccessible': False,
              'EarliestRestorableTime': now-timedelta(days=1), 'LatestRestorableTime': now}
    with pytest.raises(ValueError, match='environment'):
        restore_request(deepcopy(source), 'control-prod-restore-incident', now-timedelta(minutes=1))
