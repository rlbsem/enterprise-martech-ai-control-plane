"""Offline service-contract/state-machine tests. These are not native AWS results."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import io
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
import botocore.session
from botocore.validate import validate_parameters
import pytest

from controlplane import db
from controlplane.native_retained import RetainedRecovery, STRATEGY
from controlplane.native_run import NativeRun, validate_manifest
from controlplane.native_tasks import control_fixture, receipt_fixture, receipt_fingerprint
from controlplane.native_watchdog import cleanup_pass


def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr("controlplane.native_retained.time.sleep", lambda _: None)
    account, run = "123456789012", "retained2026"
    prefix = "csa-native-" + run
    key = f"arn:aws:kms:us-east-2:{account}:key/11111111-1111-1111-1111-111111111111"
    m = {"account": account, "run": run, "region": "us-east-2", "backup_retention_days": 1,
         "database_name": "csaacceptance", "control_db": prefix + "-control",
         "downstream_db": prefix + "-receipts", "cleanup_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
         "recovery_strategy": STRATEGY, "maximum_concurrent_instances": 2,
         "source_deletion_scope": "disposable_synthetic_control_only",
         "verification_contract": "receipt-ledger-sha256-v1", "guard_bucket": f"{prefix}-guard-{account}",
         "guard_key": key, "cluster": f"arn:aws:ecs:us-east-2:{account}:cluster/{prefix}",
         "image": f"{account}.dkr.ecr.us-east-2.amazonaws.com/{prefix}@sha256:" + "a" * 64,
         "tasks": {n: f"arn:aws:ecs:us-east-2:{account}:task-definition/{prefix}-{n}:1"
                   for n in ("bootstrap", "baseline", "control", "receipt", "verify")}}
    baseline = datetime.now(UTC) - timedelta(minutes=10)
    source = {"DBInstanceIdentifier": m["control_db"], "DbiResourceId": "db-SOURCE",
              "DBInstanceArn": f"arn:aws:rds:us-east-2:{account}:db:{m['control_db']}",
              "KmsKeyId": key, "DBInstanceStatus": "available", "DeletionProtection": True,
              "LatestRestorableTime": baseline + timedelta(minutes=5), "BackupRetentionPeriod": 1,
              "DBSubnetGroup": {"DBSubnetGroupName": prefix},
              "DBParameterGroups": [{"DBParameterGroupName": prefix}],
              "VpcSecurityGroups": [{"VpcSecurityGroupId": "owned-sg"}]}
    remote = {**source, "DBInstanceIdentifier": m["downstream_db"], "DbiResourceId": "db-RECEIPTS"}
    restored = {**source, "DBInstanceIdentifier": prefix + "-restore", "DbiResourceId": "db-RESTORED", "BackupRetentionPeriod": 1,
                "Endpoint": {"Address": "synthetic-private-restore.test"}, "MaxAllocatedStorage": 0}
    backup = {"DBInstanceIdentifier": m["control_db"], "DbiResourceId": source["DbiResourceId"],
              "DBInstanceArn": source["DBInstanceArn"], "Region": "use2", "Engine": "postgres",
              "EngineVersion": "17.11", "AllocatedStorage": 20, "BackupRetentionPeriod": 1,
              "Encrypted": True, "KmsKeyId": key, "Status": "active",
              "DBInstanceAutomatedBackupsArn": f"arn:aws:rds:us-east-2:{account}:auto-backup:ab-SOURCE",
              "RestoreWindow": {"EarliestTime": baseline - timedelta(minutes=5),
                                "LatestTime": baseline + timedelta(minutes=5)}}
    snapshot = {"DbiResourceId": source["DbiResourceId"], "Status": "available", "Encrypted": True,
                "KmsKeyId": key, "SnapshotCreateTime": baseline - timedelta(minutes=1)}
    state = {"source": True, "restored": False, "backup_retained": True, "foreign": False,
             "snapshot": True, "bad_readback": False, "tagged": True, "changed_receipts": False,
             "source_window": True, "max_instances": 2}
    clients = {n: MagicMock() for n in ("rds", "kms", "s3")}
    rds = clients["rds"]

    class Missing(Exception):
        pass

    rds.exceptions.DBInstanceNotFoundFault = Missing

    def databases():
        rows = [remote] + ([source] if state["source"] else []) + ([restored] if state["restored"] else [])
        if state["foreign"]:
            rows.append({"DBInstanceIdentifier": "unrelated"})
        state["max_instances"] = max(state["max_instances"], len(rows))
        return deepcopy(rows)

    def paginator(operation):
        def pages(**kwargs):
            if operation == "describe_db_instances":
                return [{"DBInstances": databases()}]
            assert operation == "describe_db_snapshots" and kwargs["SnapshotType"] == "automated"
            return [{"DBSnapshots": [deepcopy(snapshot)] if state["snapshot"] else []}]
        return SimpleNamespace(paginate=pages)

    rds.get_paginator.side_effect = paginator

    def describe(DBInstanceIdentifier):
        for row in databases():
            if row["DBInstanceIdentifier"] == DBInstanceIdentifier:
                return {"DBInstances": [row]}
        raise Missing()

    rds.describe_db_instances.side_effect = describe

    def backups(**kwargs):
        assert kwargs == {"DbiResourceId": "db-SOURCE"}
        row = deepcopy(backup)
        if not state["source"]:
            row["Status"] = "retained" if state["backup_retained"] else "creating"
            row.pop("BackupRetentionPeriod", None)  # Optional field omitted by real retained-backup API
        if not state["source_window"]:
            row["RestoreWindow"] = {}
        return {"DBInstanceAutomatedBackups": [row], "ResponseMetadata": {"RequestId": "offline-backup"}}

    rds.describe_db_instance_automated_backups.side_effect = backups
    rds.list_tags_for_resource.side_effect = lambda **_: {"TagList": [
        {"Key": "AcceptanceRun", "Value": run if state["tagged"] else "foreign"}]}
    def modify(**kwargs):
        if kwargs["DBInstanceIdentifier"] == m["control_db"]:
            source.update(DeletionProtection=kwargs["DeletionProtection"])
        else:
            assert kwargs["DBInstanceIdentifier"] == restored["DBInstanceIdentifier"]
            restored["BackupRetentionPeriod"] = kwargs["BackupRetentionPeriod"]
    rds.modify_db_instance.side_effect = modify

    def delete(**kwargs):
        assert kwargs == {"DBInstanceIdentifier": m["control_db"], "SkipFinalSnapshot": True,
                          "DeleteAutomatedBackups": False}
        state["source"] = False

    rds.delete_db_instance.side_effect = delete

    def restore(**kwargs):
        assert not state["source"] and not state["restored"]
        assert kwargs["SourceDbiResourceId"] == "db-SOURCE" and kwargs["RestoreTime"] == baseline
        assert not set(kwargs) & {"SourceDBInstanceIdentifier", "SourceDBInstanceAutomatedBackupsArn",
                                  "ManageMasterUserPassword", "MasterUserPassword", "UseLatestRestorableTime"}
        assert not kwargs["MultiAZ"] and not kwargs["PubliclyAccessible"]
        assert kwargs["DBParameterGroupName"] == prefix and "BackupRetentionPeriod" not in kwargs
        assert "MaxAllocatedStorage" not in kwargs
        state["restored"] = True
        assert len(databases()) == 2
        return {"ResponseMetadata": {"RequestId": "offline-restore"}}

    rds.restore_db_instance_to_point_in_time.side_effect = restore
    clients["kms"].describe_key.return_value = {"KeyMetadata": {"KeyState": "Enabled"}}
    objects = {"inventory/registry.json": b"[]"}

    def put(**kwargs):
        if kwargs["Key"].startswith(("recovery/", "execution/")):
            assert kwargs["IfNoneMatch"] == "*" and kwargs["Key"] not in objects
        else:
            assert kwargs["IfMatch"] == "offline-etag"
        assert kwargs["SSEKMSKeyId"] == key
        objects[kwargs["Key"]] = kwargs["Body"]

    def get(**kwargs):
        raw = objects[kwargs["Key"]]
        if state["bad_readback"] and kwargs["Key"].startswith("recovery/"):
            raw = b"altered"
        return {"Body": io.BytesIO(raw), "ETag": "offline-etag", "ResponseMetadata": {"RequestId": "offline-readback"}}

    clients["s3"].put_object.side_effect, clients["s3"].get_object.side_effect = put, get

    class Runner:
        def __init__(self):
            self.m, self.c, self.prefix = m, clients, prefix
            self.checkpoint_dir = tmp_path / "private-checkpoints"
            self.allow_source_deletion = True
            self.records, self.writes_frozen = [], False

        def gate(self):
            return {"offline_only": True}

        def drained(self):
            pass

        def database(self, identifier):
            return describe(identifier)["DBInstances"][0]

        def task(self, name, **kwargs):
            assert name == "verify"
            blocked = kwargs.get("expected_exit") == 2
            if blocked:
                assert kwargs["extra"] == {"RESTORED_HOST": restored["Endpoint"]["Address"]}
            value = {"safe_to_resume": not blocked, "local_intents": 0 if blocked else 1,
                     "remote_effects": 1, "write_denials": 2,
                     "remote_ledger_sha256": "b" * 64 if blocked and state["changed_receipts"] else "a" * 64,
                     "findings": [{"reason": "remote_ahead_missing_intent"}] if blocked else []}
            self.records.append({"task": name, "result": value, "offline_only": True})
            return value

    runner = Runner()
    return SimpleNamespace(runner=runner, recovery=RetainedRecovery(runner), state=state, source=source,
                           backup=backup, snapshot=snapshot, restored=restored, baseline=baseline, rds=rds, objects=objects)


def test_backup_accepts_regional_code_and_pinned_arn(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    r = f.recovery.backup(f.source, "active", f.baseline)
    assert r and r["metadata"]["Region"] == "use2"
    f.backup["Region"] = "us-east-2"
    assert f.recovery.backup(f.source, "active", f.baseline)
    f.backup["Region"] = "usw2"
    with pytest.raises(RuntimeError, match="Backup metadata differs"):
        f.recovery.backup(f.source, "active", f.baseline)
    f.backup["Region"] = "use2"
    f.backup["DBInstanceAutomatedBackupsArn"] = f.backup["DBInstanceAutomatedBackupsArn"].replace(
        ":us-east-2:", ":us-west-2:")
    with pytest.raises(RuntimeError, match="Backup metadata differs"):
        f.recovery.backup(f.source, "active", f.baseline)


def test_missing_retained_field_does_not_weaken_source_retention_requirement(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.state["source"] = False
    assert f.recovery.backup(f.source, "retained", f.baseline)
    f.source["BackupRetentionPeriod"] = 7
    with pytest.raises(RuntimeError, match="Backup metadata differs"):
        f.recovery.backup(f.source, "retained", f.baseline)


def test_restored_unexpected_autoscaling_ceiling_fails_closed(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.restored["MaxAllocatedStorage"] = 100
    with pytest.raises(RuntimeError, match="autoscaling unexpectedly enabled"):
        f.recovery.execute(f.baseline)
    assert not any(json.loads(v).get("phase") == "restored-fail-closed-verified"
                   for k, v in f.objects.items() if k.startswith("recovery/"))


def test_retained_protocol_never_needs_a_third_instance_and_preserves_receipt_content(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    result = f.recovery.execute(f.baseline)
    assert result["status"] == "NATIVE_RETAINED_SQL_PITR_ACCEPTANCE_PASSED"
    assert result["original_control_deleted"] and not result["full_application_acceptance"]
    assert f.state["max_instances"] == 2 and f.runner.writes_frozen
    assert len(f.runner.records) == 3 and f.runner.records[-1]["result"]["safe_to_resume"] is False
    phases = [json.loads(v)["phase"] for k, v in f.objects.items() if k.startswith("recovery/")]
    assert phases == ["before-source-deletion", "retained-backup-observed", "restored-instance-available",
                      "restored-reconciliation-1-verified", "restored-reconciliation-2-verified",
                      "restored-fail-closed-verified"]
    model = botocore.session.get_session().get_service_model("rds")
    for operation, method in [("DeleteDBInstance", f.rds.delete_db_instance),
                              ("RestoreDBInstanceToPointInTime", f.rds.restore_db_instance_to_point_in_time)]:
        validate_parameters(method.call_args.kwargs, model.operation_model(operation).input_shape)


def test_inherited_retention_is_corrected_only_on_restored_target(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.restored["BackupRetentionPeriod"] = 7
    result = f.recovery.execute(f.baseline)
    assert result["status"] == "NATIVE_RETAINED_SQL_PITR_ACCEPTANCE_PASSED"
    calls = [call.kwargs for call in f.rds.modify_db_instance.call_args_list]
    assert calls == [{"DBInstanceIdentifier": f.runner.m["control_db"],
                      "DeletionProtection": False, "ApplyImmediately": True},
                     {"DBInstanceIdentifier": f.runner.prefix + "-restore",
                      "BackupRetentionPeriod": 1, "ApplyImmediately": True}]
    shape = botocore.session.get_session().get_service_model("rds").operation_model("ModifyDBInstance").input_shape
    for call in calls:
        validate_parameters(call, shape)


@pytest.mark.parametrize("fault", ["snapshot", "snapshot_age", "foreign", "key", "readback", "tag",
                                    "wrong_source", "window", "short_window", "approval", "fingerprint"])
def test_predelete_faults_do_not_delete_a_source(tmp_path, monkeypatch, fault):
    f = fixture(tmp_path, monkeypatch)
    if fault in {"snapshot", "foreign"}:
        f.state[fault] = fault == "foreign"
    if fault == "snapshot_age":
        f.snapshot["SnapshotCreateTime"] -= timedelta(days=1)
    if fault == "key":
        f.runner.c["kms"].describe_key.return_value = {"KeyMetadata": {"KeyState": "PendingDeletion"}}
    if fault == "readback":
        f.state["bad_readback"] = True
    if fault == "tag":
        f.state["tagged"] = False
    if fault == "wrong_source":
        f.backup["DbiResourceId"] = "foreign-physical-id"
    if fault == "window":
        f.state["source_window"] = False
    if fault == "short_window":
        f.runner.m["cleanup_at"] = (datetime.now(UTC) + timedelta(minutes=89)).isoformat()
    if fault == "approval":
        f.runner.allow_source_deletion = False
    if fault == "fingerprint":
        monkeypatch.setattr(f.recovery, "verify_receipts", lambda *_: (_ for _ in ()).throw(RuntimeError("missing fingerprint")))
    with pytest.raises(RuntimeError):
        f.recovery.execute(f.baseline)
    assert f.state["source"]
    f.rds.delete_db_instance.assert_not_called()
    f.rds.restore_db_instance_to_point_in_time.assert_not_called()


@pytest.mark.parametrize("fault", ["retained_unavailable", "lost_delete_ack", "restore_denied", "receipt_changed"])
def test_postdelete_faults_never_become_fake_recovery_success(tmp_path, monkeypatch, fault):
    f = fixture(tmp_path, monkeypatch)
    if fault == "retained_unavailable":
        f.state["backup_retained"] = False
    if fault == "lost_delete_ack":
        def lost(**_):
            f.state["source"] = False
            raise TimeoutError("offline simulated acknowledgement loss")
        f.rds.delete_db_instance.side_effect = lost
    if fault == "restore_denied":
        f.rds.restore_db_instance_to_point_in_time.side_effect = ClientError(
            {"Error": {"Code": "FreeTierRestrictionError", "Message": "offline denial"}}, "RestoreDBInstanceToPointInTime")
    if fault == "receipt_changed":
        f.state["changed_receipts"] = True
    with pytest.raises((RuntimeError, TimeoutError, ClientError)):
        f.recovery.execute(f.baseline)
    assert not f.state["source"] and f.state["max_instances"] <= 2
    assert any(k.startswith("recovery/before-source-deletion-") for k in f.objects)
    f.rds.restore_db_instance_from_db_snapshot.assert_not_called()


@pytest.mark.parametrize("change", [{"maximum_concurrent_instances": 3}, {"source_deletion_scope": "any"},
                                   {"verification_contract": "count-only"}, {"guard_bucket": "public"}])
def test_retained_manifest_refuses_weaker_protocol(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    f.runner.m.update(change)
    with pytest.raises(ValueError):
        validate_manifest(f.runner.m)


def test_native_runner_requires_deletion_approval_before_any_fixture(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="deletion approval"):
        NativeRun(f.runner.m, f.runner.c)
    runner = NativeRun(f.runner.m, f.runner.c, allow_source_deletion=True, checkpoint_dir=".local/unused-offline")
    runner.writes_frozen = True
    with pytest.raises(RuntimeError, match="frozen"):
        runner.task("receipt")
    assert not f.runner.c["rds"].mock_calls
    with pytest.raises(ValueError, match="private"):
        NativeRun(f.runner.m, f.runner.c, allow_source_deletion=True, checkpoint_dir=".local/../docs")


@pytest.mark.parametrize("tagged", [False, True])
def test_guard_retained_backup_cleanup_requires_effective_ownership(tagged):
    rows = [{"kind": "backup", "id": "owned-physical-source", "state": "retained", "run_tag_verified": tagged},
            {"kind": "key", "id": "owned-encryption-key", "state": "Enabled"}]
    clients = {n: MagicMock() for n in ("rds", "kms")}
    result = cleanup_pass(SimpleNamespace(collect=lambda: rows), clients)
    assert ("backup" in result["failed_kinds"]) == (not tagged)
    assert clients["rds"].delete_db_instance_automated_backup.call_count == int(tagged)
    clients["kms"].schedule_key_deletion.assert_not_called()  # Keep the key while backups remain.


def test_receipt_fingerprint_detects_real_sql_content_change_without_new_effect():
    receipt_fixture(control_fixture("retained2026"), os.environ["REMOTE_DSN"])
    before = receipt_fingerprint(os.environ["REMOTE_DSN"])
    assert before == receipt_fingerprint(os.environ["REMOTE_DSN"])
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as connection:
        connection.execute("UPDATE remote.effects SET body=body || '{\"changed\":true}'::jsonb")
    assert before != receipt_fingerprint(os.environ["REMOTE_DSN"])


@pytest.mark.parametrize("change", [
    {"safe_to_resume": 1}, {"safe_to_resume": "true"}, {"safe_to_resume": None},
    {"local_intents": True}, {"remote_effects": True}, {"local_intents": 1.0},
    {"write_denials": 2.0}, {"findings": [{"reason": "remote_ahead_missing_intent"}]},
])
def test_untyped_original_verdict_cannot_authorize_source_deletion(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    original = f.runner.task

    def task(name, **kwargs):
        return {**original(name, **kwargs), **change}

    monkeypatch.setattr(f.runner, "task", task)
    with pytest.raises(RuntimeError):
        f.recovery.execute(f.baseline)
    f.rds.delete_db_instance.assert_not_called()
    f.rds.restore_db_instance_to_point_in_time.assert_not_called()
    assert f.state["source"]


@pytest.mark.parametrize("change", [
    {"safe_to_resume": None}, {"safe_to_resume": 0}, {"safe_to_resume": ""},
    {"local_intents": False}, {"remote_effects": True}, {"local_intents": 0.0},
    {"write_denials": 2.0}, {"findings": []}, {"findings": None},
    {"findings": [{"reason": "remote_ahead_missing_intent"}] * 2},
    {"findings": [{"reason": "different_failure"}]},
])
def test_untyped_restored_verdict_never_becomes_acceptance(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    original = f.runner.task

    def task(name, **kwargs):
        result = original(name, **kwargs)
        return {**result, **change} if kwargs.get("expected_exit") == 2 else result

    monkeypatch.setattr(f.runner, "task", task)
    with pytest.raises(RuntimeError):
        f.recovery.execute(f.baseline)
    phases = [json.loads(v)["phase"] for k, v in f.objects.items() if k.startswith("recovery/")]
    assert phases[-1] == "restored-instance-available"
    assert "restored-fail-closed-verified" not in phases


@pytest.mark.parametrize("failed_attempt", [1, 2])
def test_task_failure_preserves_available_restore_without_claiming_final_acceptance(
    tmp_path, monkeypatch, failed_attempt
):
    f = fixture(tmp_path, monkeypatch)
    original = f.runner.task
    attempts = []

    def task(name, **kwargs):
        if kwargs.get("expected_exit") == 2:
            attempts.append(name)
            if len(attempts) == failed_attempt:
                raise RuntimeError("offline verification task interruption")
        return original(name, **kwargs)

    monkeypatch.setattr(f.runner, "task", task)
    with pytest.raises(RuntimeError, match="interruption"):
        f.recovery.execute(f.baseline)
    checkpoints = [json.loads(v) for k, v in f.objects.items() if k.startswith("recovery/")]
    available = next(c for c in checkpoints if c["phase"] == "restored-instance-available")
    assert available["restored_reconciliation"] == "not_yet_verified"
    assert available["restored"]["DbiResourceId"] not in {f.source["DbiResourceId"], "db-REMOTE"}
    assert not available["full_application_acceptance"]
    phases = [c["phase"] for c in checkpoints]
    assert ("restored-reconciliation-1-verified" in phases) == (failed_attempt == 2)
    assert "restored-reconciliation-2-verified" not in phases
    assert "restored-fail-closed-verified" not in phases


def test_unverified_available_checkpoint_prevents_further_verification_tasks(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    original = f.recovery.checkpoint

    def checkpoint(phase, payload):
        if phase == "restored-instance-available":
            f.state["bad_readback"] = True
        return original(phase, payload)

    monkeypatch.setattr(f.recovery, "checkpoint", checkpoint)
    with pytest.raises(RuntimeError, match="read-back mismatch"):
        f.recovery.execute(f.baseline)
    assert len(f.runner.records) == 1  # only original-control reconciliation
    assert not any(p["phase"].startswith("restored-reconciliation") for p in f.recovery.phases)
