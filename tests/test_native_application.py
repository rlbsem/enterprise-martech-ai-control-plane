"""Real local application-task execution and offline AWS orchestration contracts.

No test in this module claims native AWS execution.
"""

import io
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import botocore.session
import pytest
from botocore.validate import validate_parameters

from controlplane import recovery
from controlplane.native_app_tasks import advance, load_dsns, recover, seed
from controlplane.native_application import ApplicationRecovery
from controlplane.native_arming import account_gate
from controlplane.native_inventory import Inventory
from controlplane.native_run import validate_manifest
from controlplane.policy import digest
from controlplane.recovery_lab import LocalDatabases
from test_native_acceptance import armed_clients, manifest
from test_retained_recovery import fixture as retained_fixture


def test_real_application_task_stages_over_local_restored_databases(tmp_path):
    databases = LocalDatabases(os.environ["TEST_ADMIN_DSN"])
    try:
        control, remote = databases.create("source"), databases.create("remote")
        databases.setup(control, remote)
        source = databases.roles(control, remote)
        first = seed(source, tmp_path / "seed", "local2026")
        point1 = databases.create("point1", control)
        databases.freeze(point1)
        second = advance(source, tmp_path / "advance")
        point2 = databases.create("point2", control)
        databases.freeze(point2)
        for phase, template, point in [("behind", point1, first), ("matching", point2, second)]:
            target = databases.create(phase, template)
            dsns = databases.roles(target, remote)
            binding = {
                "environment": "isolated-local",
                "deployment_revision": digest("executed local test"),
                "source_database": first["source_database"],
                "restored_database": recovery.database_identity(dsns["reader"]),
                "recovery_point": point["recovery_point"],
            }
            result = recover(dsns, tmp_path / phase, binding, phase)
            assert result["stage"] == ("behind_blocked" if phase == "behind" else "matching_resumed")
            if phase == "behind":
                assert not result["verification"]["safe_to_resume"]
                assert result["remote_ledger_sha256"] == second["remote_ledger_sha256"]
            else:
                assert result["old_completion_accepted"] is False
                assert result["post_resume"]["local_intents"] == result["post_resume"]["remote_effects"] == 6
                assert result["revoked_action"]["decision"] == "deny"
    finally:
        assert databases.cleanup()["remaining_generated_roles"] == 0


def test_native_secret_loader_is_scoped_and_does_not_rebind_receipts(monkeypatch):
    documents = {
        "api": {"CONTROL_DSN": "host=old dbname=csaacceptance user=api", "API_TOKENS": "{}"},
        "worker": {"CONTROL_DSN": "host=old dbname=csaacceptance user=worker", "EXECUTOR_TOKEN": "test"},
        "remote": {
            "REMOTE_DSN": "host=independent dbname=csaacceptance user=remote",
            "EXECUTOR_TOKEN": "test",
            "INJECTOR_TOKEN": "test",
        },
        "verifier": {
            "CONTROL_DSN": "host=old dbname=csaacceptance user=reader",
            "REMOTE_DSN": "host=independent dbname=csaacceptance user=reader",
        },
        "recovery": {"password": "test-only"},
    }
    monkeypatch.setenv("APP_RUNTIME_SECRET_ARNS", json.dumps({k: k for k in documents}))
    monkeypatch.setattr("controlplane.native_app_tasks.cloud.read_secret", lambda client, key: documents[key])
    actual = load_dsns(None, host="restore.synthetic.us-east-2.rds.amazonaws.com")
    assert "host=restore.synthetic.us-east-2.rds.amazonaws.com" in actual["coordinator"]
    assert actual["remote_reader"] == documents["verifier"]["REMOTE_DSN"]
    assert actual["remote"] == documents["remote"]["REMOTE_DSN"]
    with pytest.raises(ValueError, match="pinned_ohio"):
        load_dsns(None, host="other-region.invalid")
    with pytest.raises(ValueError, match="scoped_application"):
        load_dsns(None, verifier=True)


def test_application_manifest_and_credit_gate_are_stricter_than_legacy():
    m = manifest()
    m["acceptance_workflow"] = "application-recovery-v1"
    with pytest.raises(ValueError, match="retained two-instance"):
        validate_manifest(m)
    clients, _, _ = armed_clients(m)
    clients["freetier"].get_account_plan_state.return_value["accountPlanRemainingCredits"]["unit"] = "USD"
    clients["freetier"].get_account_plan_state.return_value["accountPlanRemainingCredits"]["amount"] = 19.99
    with pytest.raises(RuntimeError, match="20-credit"):
        account_gate(m, clients)
    m["acceptance_workflow"] = "sql-fixture-v1"
    account_gate(m, clients)


def application_fixture(tmp_path, monkeypatch):
    f = retained_fixture(tmp_path, monkeypatch)
    m, rds = f.runner.m, f.rds
    m.update(
        acceptance_workflow="application-recovery-v1",
        commit="a" * 40,
        application_protocol="source-preflight-v2",
        cleanup_at=(datetime.now(UTC) + timedelta(hours=2, minutes=59)).isoformat(),
    )
    f.source["MasterUserSecret"] = {"SecretArn": "source-managed"}
    f.restored["DBInstanceArn"] = f.source["DBInstanceArn"].replace("-control", "-restore")
    f.restored["MasterUserSecret"] = {"SecretArn": "generation-0"}
    original_database = f.runner.database

    def database(name):
        row = original_database(name)
        if name == m["downstream_db"]:
            row["MasterUserSecret"] = {"SecretArn": "receipt-managed"}
        return row

    f.runner.database = database
    observed1, observed2 = f.baseline, f.baseline + timedelta(seconds=10)
    point1, point2 = [p.replace(microsecond=0) + timedelta(seconds=2) for p in (observed1, observed2)]
    f.state.update(generations=0, bad_comparison=False, deleted=[])

    def modify(**kw):
        row = f.source if kw["DBInstanceIdentifier"] == m["control_db"] else f.restored
        for key in ("DeletionProtection", "BackupRetentionPeriod"):
            if key in kw:
                row[key] = kw[key]

    def delete(**kw):
        f.state["deleted"].append(deepcopy(kw))
        if kw["DBInstanceIdentifier"] == m["control_db"]:
            assert kw["DeleteAutomatedBackups"] is False
            f.state["source"] = False
        else:
            assert kw["DBInstanceIdentifier"] == f.restored["DBInstanceIdentifier"]
            assert kw["DeleteAutomatedBackups"] is True
            f.state["restored"] = False

    def restore(**kw):
        assert not f.state["source"] and not f.state["restored"]
        f.state["generations"] += 1
        assert kw["RestoreTime"] == (point1 if f.state["generations"] == 1 else point2)
        model = (
            botocore.session.get_session()
            .get_service_model("rds")
            .operation_model("RestoreDBInstanceToPointInTime")
        )
        validate_parameters(kw, model.input_shape)
        assert not set(kw) & {
            "MaxAllocatedStorage",
            "UseLatestRestorableTime",
            "MasterUserPassword",
            "BackupRetentionPeriod",
        }
        f.restored.update(
            DbiResourceId="db-GEN" + str(f.state["generations"]),
            DeletionProtection=True,
            MasterUserSecret={"SecretArn": "generation-" + str(f.state["generations"])},
        )
        f.state["restored"] = True
        return {"ResponseMetadata": {"RequestId": "offline-restore-" + str(f.state["generations"])}}

    rds.modify_db_instance.side_effect, rds.delete_db_instance.side_effect = modify, delete
    rds.restore_db_instance_to_point_in_time.side_effect = restore

    def task(name, **kw):
        if name == "bootstrap":
            result = {"bootstrap": "complete"}
        elif name in {"baseline", "control"}:
            result = {
                "recovery_point": (observed1 if name == "baseline" else observed2).isoformat(),
                "source_database": "f" * 64,
                "terminated_worker": True,
                "comparison": {"remote_effects": 3 if name == "baseline" else 5},
                "remote_ledger_sha256": "a" * 64,
            }
        elif name == "receipt" and kw["extra"]["RECOVERY_PHASE"] == "source-preflight":
            result = {"stage": "source_preflight_passed", "recovery_operations_rolled_back": True,
                      "write_denials": 2}
        elif name == "receipt":
            phase = kw["extra"]["RECOVERY_PHASE"]
            result = (
                {
                    "stage": "behind_blocked",
                    "worker_claimed": False,
                    "verification": {"safe_to_resume": False},
                    "remote_ledger_sha256": "a" * 64,
                }
                if phase == "behind"
                else {
                    "stage": "matching_resumed",
                    "old_completion_accepted": False,
                    "duplicate_business_effects": 0,
                    "independent_database_identity_unchanged": True,
                    "remote_ledger_sha256": "b" * 64,
                }
            )
        else:
            blocked = kw.get("expected_exit") == 2
            final = f.state["generations"] == 2
            result = {
                "safe_to_resume": not blocked,
                "local_intents": 3 if blocked else 6 if final else 5,
                "remote_effects": 5 if blocked or not final else 6,
                "write_denials": 2,
                "remote_ledger_sha256": "b" * 64
                if final
                else "c" * 64
                if blocked and f.state["bad_comparison"]
                else "a" * 64,
                "execution_gate": {"mode": "closed" if blocked else "open"},
                "findings": (
                    [{"reason": "remote_ahead_missing_intent"}] * 2 + [{"reason": "execution_gate_closed"}]
                )
                if blocked
                else [],
            }
        f.runner.records.append({"task": name, "result": result, "offline_only": True})
        return result

    f.runner.task = task
    return f, ApplicationRecovery(f.runner)


def test_offline_controller_executes_two_sequential_exact_restores_and_tracks_generations(
    tmp_path, monkeypatch
):
    f, controller = application_fixture(tmp_path, monkeypatch)
    result = controller.execute()
    assert result["status"] == "NATIVE_APPLICATION_RECOVERY_ACCEPTANCE_PASSED"
    assert f.rds.restore_db_instance_to_point_in_time.call_count == 2
    assert f.state["max_instances"] == 2
    assert [r["DBInstanceIdentifier"] for r in f.state["deleted"]] == [
        f.runner.m["control_db"],
        controller.target,
    ]
    known = json.loads(f.objects["inventory/registry.json"])
    assert {r["id"] for r in known if r["kind"] == "secret"} == {
        "source-managed",
        "generation-1",
        "generation-2",
    }
    assert result["full_production_application_acceptance"] is False


def test_offline_controller_refuses_second_restore_when_independent_receipts_change(tmp_path, monkeypatch):
    f, controller = application_fixture(tmp_path, monkeypatch)
    f.state["bad_comparison"] = True
    with pytest.raises(RuntimeError, match="receipt database content changed"):
        controller.execute()
    assert f.rds.restore_db_instance_to_point_in_time.call_count == 1
    assert len(f.state["deleted"]) == 1


def test_postgres_restores_use_existing_application_logins_without_assuming_owner_secret(
    tmp_path, monkeypatch
):
    f, controller = application_fixture(tmp_path, monkeypatch)
    original = f.rds.restore_db_instance_to_point_in_time.side_effect

    def without_secret(**kw):
        assert "ManageMasterUserPassword" not in kw and "MasterUserSecretKmsKeyId" not in kw
        result = original(**kw)
        f.restored.pop("MasterUserSecret", None)
        return result

    f.rds.restore_db_instance_to_point_in_time.side_effect = without_secret
    assert controller.execute()["status"] == "NATIVE_APPLICATION_RECOVERY_ACCEPTANCE_PASSED"
    assert {r["id"] for r in json.loads(f.objects["inventory/registry.json"]) if r["kind"] == "secret"} == {
        "source-managed"
    }


def test_offline_controller_does_not_delete_without_exact_backup_window(tmp_path, monkeypatch):
    f, controller = application_fixture(tmp_path, monkeypatch)
    f.state["source_window"] = False
    with pytest.raises(RuntimeError, match="backup window not established"):
        controller.execute()
    f.rds.delete_db_instance.assert_not_called()


def test_point_selection_can_follow_first_available_backup_without_rewriting_task_time(tmp_path, monkeypatch):
    f, controller = application_fixture(tmp_path, monkeypatch)
    observed = f.baseline
    earliest = observed + timedelta(seconds=60)
    f.backup["RestoreWindow"]["EarliestTime"] = earliest
    point, _, _ = controller.select_quiescent_point(observed, "unit-selection")
    assert point.microsecond == 0 and point > earliest and point > observed
    assert point < f.backup["RestoreWindow"]["LatestTime"]
    assert observed == f.baseline
    f.rds.delete_db_instance.assert_not_called()


def test_recovery_point_cannot_be_selected_while_runtime_is_active(tmp_path, monkeypatch):
    f, controller = application_fixture(tmp_path, monkeypatch)

    def active():
        raise RuntimeError("runtime still active")

    f.runner.drained = active
    with pytest.raises(RuntimeError, match="runtime still active"):
        controller.select_quiescent_point(f.baseline, "unit-selection")
    f.rds.describe_db_instance_automated_backups.assert_not_called()
    f.rds.delete_db_instance.assert_not_called()


def test_known_managed_generation_is_checked_directly_when_list_omits_it():
    clients = {
        n: MagicMock() for n in ["sts", "rds", "ecs", "ecr", "secretsmanager", "logs", "kms", "ec2", "iam"]
    }
    clients["sts"].get_caller_identity.return_value = {"Account": "123456789012"}
    clients["ecs"].describe_clusters.return_value = {"clusters": []}
    clients["secretsmanager"].describe_secret.return_value = {
        "ARN": "known-managed",
        "DeletedDate": datetime.now(UTC),
    }
    inventory = Inventory(
        "test2026", "123456789012", clients, [{"kind": "secret", "id": "known-managed", "managed": True}]
    )
    inventory.pages = lambda *a, **kw: []
    records = inventory.collect()
    assert {"kind": "secret", "id": "known-managed", "managed": True, "state": "pending_deletion"} in records
    clients["secretsmanager"].describe_secret.assert_called_once_with(SecretId="known-managed")


def test_generation_registry_readback_failure_blocks_following_actions(tmp_path, monkeypatch):
    f, controller = application_fixture(tmp_path, monkeypatch)
    original = f.runner.c["s3"].get_object.side_effect
    count = 0

    def wrong(**kw):
        nonlocal count
        count += 1
        if count == 2:
            return {"Body": io.BytesIO(b"[]")}
        return original(**kw)

    f.runner.c["s3"].get_object.side_effect = wrong
    with pytest.raises(RuntimeError, match="registry readback"):
        controller.remember(f.source)
    f.rds.delete_db_instance.assert_not_called()
