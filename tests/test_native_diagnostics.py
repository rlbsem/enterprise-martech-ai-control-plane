"""Offline failure-capture contracts; these tests do not execute AWS."""

import io
import json
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from controlplane.native_run import NativeRun
from controlplane.native_watchdog import archive_logs, cleanup_pass
from scripts import check_recovery
from test_native_acceptance import manifest


def runner(tmp_path):
    m = manifest()
    m.update(guard_bucket="private-test-bucket", guard_key="private-test-key",
             task_subnet="private-subnet", task_groups={"baseline": "private-group"})
    clients = {name: MagicMock() for name in ("ecs", "logs", "s3")}
    r = NativeRun(m, clients, checkpoint_dir=tmp_path)
    r.gate = r.drained = lambda: None
    arn = "arn:aws:ecs:us-east-2:123456789012:task/csa-native-test2026/owned"
    task = {"taskArn": arn, "lastStatus": "STOPPED", "taskDefinitionArn": m["tasks"]["baseline"],
            "stopCode": "EssentialContainerExited", "stoppedReason": "private-stop-sentinel",
            "containers": [{"name": "baseline", "exitCode": 0, "imageDigest": "sha256:" + "a" * 64}]}
    clients["ecs"].describe_task_definition.return_value = {"taskDefinition": {
        "containerDefinitions": [{"name": "baseline", "image": m["image"],
                                  "command": ["python", "-m", "controlplane.native_tasks", "baseline"]}]}}
    clients["ecs"].run_task.return_value = {"tasks": [{"taskArn": arn}]}
    clients["ecs"].describe_tasks.return_value = {"tasks": [task]}
    clients["logs"].get_log_events.return_value = {"events": [
        {"message": json.dumps({"native_fixture": {"accepted": True}, "private": "log-sentinel"})}]}
    objects = {}
    clients["s3"].put_object.side_effect = lambda **kw: objects.update({kw["Key"]: kw["Body"]})
    clients["s3"].get_object.side_effect = lambda **kw: {"Body": io.BytesIO(objects[kw["Key"]])}
    return r, clients, task, objects


@pytest.mark.parametrize("fault", ["exit", "image", "identity", "logs"])
def test_stopped_task_diagnostics_precede_rejection(tmp_path, fault):
    r, clients, task, objects = runner(tmp_path)
    if fault == "exit":
        task["containers"][0]["exitCode"] = 1
    elif fault == "image":
        task["containers"][0]["imageDigest"] = "sha256:" + "b" * 64
    elif fault == "identity":
        task["taskArn"] = "wrong-task"
    else:
        clients["logs"].get_log_events.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "private-error-sentinel"}},
            "GetLogEvents")
    with pytest.raises(RuntimeError, match="mismatch|logs unavailable"):
        r.task("baseline")
    assert len(objects) == len(list(tmp_path.glob("task-observation-*.json"))) == 1
    assert clients["s3"].get_object.call_count == 1
    public = json.dumps(r.records)
    assert all(s not in public for s in ("private-stop-sentinel", "private-error-sentinel", "log-sentinel"))
    assert r.records[0]["task_observation"]["private_archive_sha256"]
    assert not any("result" in row for row in r.records)


def test_diagnostic_readback_failure_cannot_accept_an_otherwise_successful_task(tmp_path):
    r, clients, _, _ = runner(tmp_path)
    clients["s3"].get_object.side_effect = lambda **kw: {"Body": io.BytesIO(b"wrong")}
    with pytest.raises(RuntimeError, match="diagnostic cloud readback"):
        r.task("baseline")
    assert not any("result" in row for row in r.records)


def test_successful_task_diagnostics_are_also_preserved(tmp_path):
    r, _, _, objects = runner(tmp_path)
    assert r.task("baseline") == {"accepted": True}
    assert len(objects) == 1 and r.records[-1]["result"] == {"accepted": True}


def log_clients():
    logs, storage = MagicMock(), MagicMock()
    logs.get_paginator.return_value.paginate.return_value = [{"logStreams": [{"logStreamName": "owned"}]}]
    logs.get_log_events.side_effect = [
        {"events": [{"message": "private-log-sentinel"}], "nextForwardToken": "end"},
        {"events": [], "nextForwardToken": "end"},
    ]
    data = {}
    storage.put_object.side_effect = lambda **kw: data.update({kw["Key"]: kw["Body"]})
    storage.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(data[kw["Key"]])}
    return logs, storage, data


@pytest.mark.parametrize("valid_readback", [True, False])
def test_scheduled_cleanup_archives_logs_before_deletion(valid_readback):
    logs, storage, data = log_clients()
    if not valid_readback:
        storage.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(b"wrong")}
    rows = [{"kind": "log_group", "id": "owned-group"}]
    result = cleanup_pass(SimpleNamespace(collect=lambda: rows), {"logs": logs},
                          lambda group: archive_logs(logs, storage, group, "private-bucket", "private-key"))
    assert len(data) == 1 and b"private-log-sentinel" in next(iter(data.values()))
    assert logs.get_log_events.call_count == 2
    assert logs.delete_log_group.call_count == int(valid_readback)
    assert bool(result["failed_kinds"]) is not valid_readback


def test_existing_immutable_log_archive_requires_matching_readback():
    logs, storage, _ = log_clients()
    original = storage.put_object.side_effect

    def exists(**kw):
        original(**kw)
        raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")

    storage.put_object.side_effect = exists
    assert len(archive_logs(logs, storage, "owned-group", "bucket", "key")) == 64
    assert storage.put_object.call_args.kwargs["IfNoneMatch"] == "*"


def test_log_stream_budget_failure_prevents_any_archive_write():
    logs, storage, _ = log_clients()
    logs.get_paginator.return_value.paginate.return_value = [{"logStreams": [{}] * 21}]
    with pytest.raises(RuntimeError, match="stream archive budget"):
        archive_logs(logs, storage, "owned-group", "bucket", "key")
    storage.put_object.assert_not_called()


def test_cleanup_error_records_codes_without_private_exception_messages():
    ecs = MagicMock()
    ecs.deregister_task_definition.side_effect = ClientError({
        "Error": {"Code": "AccessDeniedException", "Message": "private-exception-sentinel"},
        "ResponseMetadata": {"RequestId": "test-request-id"}}, "DeregisterTaskDefinition")
    rows = [{"kind": "task_definition", "id": "private-resource-sentinel", "state": "ACTIVE"}]
    result = cleanup_pass(SimpleNamespace(collect=lambda: rows), {"ecs": ecs})
    detail = result["failure_details"][0]
    assert result["failed_kinds"] == ["task_definition"]
    assert detail["aws_code"] == "AccessDeniedException" and detail["request_id"] == "test-request-id"
    assert detail["operation"] == "DeregisterTaskDefinition"
    assert "private-exception-sentinel" not in json.dumps(result)
    assert "private-resource-sentinel" not in json.dumps(result)


@pytest.mark.parametrize("fault", ["success_relabel", "availability_relabel", "source_hash"])
def test_recorded_native_failure_cannot_be_promoted_by_editing_evidence(tmp_path, monkeypatch, fault):
    # Private copies are deliberately corrupted; the committed run is never edited.
    folder = tmp_path / "native-2026-10-28"
    shutil.copytree(check_recovery.PACKAGE / folder.name, folder)
    monkeypatch.setattr(check_recovery, "PACKAGE", tmp_path)
    source = (check_recovery.ROOT / "src/controlplane/native_application.py").read_bytes()
    monkeypatch.setattr(check_recovery.subprocess, "check_output", lambda *a, **kw: source)
    if fault == "availability_relabel":
        path = folder / "checkpoints.json"
        record = json.loads(path.read_bytes())
        next(row for row in record if row["phase"] == "behind-rds-available")["application_recovery"] = "passed"
    else:
        path = folder / "execution.json"
        record = json.loads(path.read_bytes())
        if fault == "success_relabel":
            record["result"]["status"] = "NATIVE_APPLICATION_RECOVERY_ACCEPTANCE_PASSED"
        else:
            record["result"]["execution_binding"]["controller_sha256"] = "0" * 64
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        check_recovery.check_native_attempt()


def test_recorded_failed_native_attempt_stays_distinct_from_local_acceptance(monkeypatch):
    source = (check_recovery.ROOT / "src/controlplane/native_application.py").read_bytes()
    monkeypatch.setattr(check_recovery.subprocess, "check_output", lambda *a, **kw: source)
    result = check_recovery.check_native_attempt()
    assert result == {"status": "FAILED_NATIVE_APPLICATION_ACCEPTANCE", "restores_recorded": 1,
                      "application_acceptance": False}
