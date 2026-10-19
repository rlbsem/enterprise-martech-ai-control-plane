"""Native SQL fixture and offline orchestration safety tests; no AWS calls."""

from datetime import UTC, datetime, timedelta
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from psycopg.types.json import Jsonb

from controlplane import db, operations
from controlplane.native_arming import armed_gate
from controlplane.native_inventory import Inventory, identity, public_report
from controlplane.native_tasks import control_fixture, receipt_fixture, permission_denials
from controlplane.native_watchdog import cleanup_pass
from controlplane.native_run import NativeRun, validate_manifest


def manifest():
    prefix, account = "csa-native-test2026", "123456789012"
    return dict(
        account=account,
        region="us-east-2",
        backup_retention_days=1,
        database_name="csaacceptance",
        run="test2026",
        cleanup_at=(datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0).isoformat(),
        cluster=f"arn:aws:ecs:us-east-2:{account}:cluster/{prefix}",
        control_db=prefix + "-control",
        downstream_db=prefix + "-receipts",
        tasks={
            n: f"arn:aws:ecs:us-east-2:{account}:task-definition/{prefix}-{n}:1"
            for n in ("bootstrap", "baseline", "control", "receipt", "verify")
        },
        image=f"{account}.dkr.ecr.us-east-2.amazonaws.com/{prefix}@sha256:" + ("a" * 64),
        schedule=prefix + "-cleanup",
        watchdog=prefix + "-watchdog",
        guard_code_sha256="source-bound",
    )


def armed_clients(m):
    c = {name: MagicMock() for name in ("sts", "freetier", "lambda", "scheduler")}
    c["sts"].get_caller_identity.return_value = {"Account": m["account"]}
    c["freetier"].get_account_plan_state.return_value = {
        "accountPlanType": "FREE",
        "accountPlanStatus": "ACTIVE",
        "accountPlanRemainingCredits": {"amount": 100},
    }
    prefix = "csa-native-" + m["run"]
    arn = f"arn:aws:lambda:us-east-2:{m['account']}:function:{prefix}-watchdog"
    deadline = datetime.fromisoformat(m["cleanup_at"])
    c["scheduler"].get_schedule.return_value = {
        "State": "ENABLED",
        "ScheduleExpression": "rate(1 minute)",
        "ScheduleExpressionTimezone": "UTC",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "StartDate": deadline,
        "EndDate": deadline + timedelta(hours=1),
        "Target": {
            "Arn": arn,
            "RoleArn": f"arn:aws:iam::{m['account']}:role/csa-native-guard/{m['run']}/{prefix}-schedule",
            "RetryPolicy": {"MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 2},
            "DeadLetterConfig": {"Arn": f"arn:aws:sqs:us-east-2:{m['account']}:{prefix}-cleanup-failures"},
        },
    }
    env = {
        "RUN_ID": m["run"],
        "ACCOUNT_ID": m["account"],
        "CLEANUP_AT": m["cleanup_at"],
        "DISPOSITION": "discard_isolated_synthetic_state",
    }
    fn = {
        "FunctionArn": arn,
        "CodeSha256": m["guard_code_sha256"],
        "State": "Active",
        "Environment": {"Variables": env},
        "Role": f"arn:aws:iam::{m['account']}:role/csa-native-guard/{m['run']}/{prefix}-watchdog",
    }
    checker = {**fn, "Role": f"arn:aws:iam::{m['account']}:role/csa-native-guard/{m['run']}/{prefix}-checker"}
    c["lambda"].get_function_configuration.side_effect = (
        lambda **kw: fn if kw["FunctionName"].endswith("watchdog") else checker
    )
    return c, fn, checker


def test_sql_fixture_exact_replay_and_remote_ahead():
    fixture = control_fixture("test2026")
    assert control_fixture("test2026") == fixture
    result = receipt_fixture(fixture, os.environ["REMOTE_DSN"])
    assert result == receipt_fixture(fixture, os.environ["REMOTE_DSN"])
    snapshot = operations.recovery_snapshot(os.environ["CONTROL_DSN"], os.environ["REMOTE_DSN"])
    assert snapshot["safe_to_resume"] and snapshot["local_intents"] == snapshot["remote_effects"] == 1
    # A local rollback is a unit/integration check, never represented as real RDS PITR.
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("DELETE FROM cp.jobs")
    blocked = operations.recovery_snapshot(os.environ["CONTROL_DSN"], os.environ["REMOTE_DSN"])
    assert not blocked["safe_to_resume"]
    assert {f["reason"] for f in blocked["findings"]} == {"remote_ahead_missing_intent"}


def test_receipt_conflicting_content_cannot_replace_effect():
    fixture = control_fixture("test2026")
    receipt_fixture(fixture, os.environ["REMOTE_DSN"])
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("UPDATE remote.effects SET body=%s", (Jsonb({"tampered": True}),))
    with pytest.raises(ValueError, match="conflict"):
        receipt_fixture(fixture, os.environ["REMOTE_DSN"])


@pytest.mark.parametrize("change", [{"scope": "ordinary dispatch"}, {"hash": "wrong"}, {"extra": "field"}])
def test_receipt_rejects_non_fixture(change):
    fixture = control_fixture("test2026")
    fixture.update(change)
    with pytest.raises(ValueError):
        receipt_fixture(fixture, os.environ["REMOTE_DSN"])


def test_effective_reader_denials():
    admin = os.environ["TEST_ADMIN_DSN"]
    with db.connect(admin) as c:
        c.execute("CREATE ROLE native_fixture_reader LOGIN PASSWORD 'local-reader-only'")
        c.execute("GRANT USAGE ON SCHEMA cp,remote TO native_fixture_reader")
        c.execute("GRANT SELECT ON cp.jobs,remote.effects TO native_fixture_reader")
    from psycopg.conninfo import make_conninfo

    # A separate temporary local login exercises effective permissions, never existing cloud users.
    reader = make_conninfo(admin, user="native_fixture_reader", password="local-reader-only")
    try:
        assert permission_denials(reader, reader) == 2
        with pytest.raises(ValueError):
            permission_denials(admin, admin)
    finally:
        with db.connect(admin) as c:
            c.execute("DROP OWNED BY native_fixture_reader; DROP ROLE native_fixture_reader")


@pytest.mark.parametrize("retained", [False, True])
def test_independent_armed_gate(retained):
    m = manifest()
    c, fn, _ = armed_clients(m)
    if retained:
        m.update(recovery_strategy="retained-two-instance-v1", guard_bucket="pinned-private-bucket", guard_key="pinned-key")
        fn["Environment"]["Variables"].update(GUARD_BUCKET=m["guard_bucket"], GUARD_KEY_ARN=m["guard_key"])
    assert armed_gate(m, c)["watchdog_armed"]


@pytest.mark.parametrize(
    "fault",
    [
        "paid",
        "low_credit",
        "wrong_account",
        "disabled",
        "late_end",
        "wrong_hash",
        "wrong_run",
        "same_role",
        "vpc",
        "disposition",
        "retry",
        "wrong_checkpoint_bucket",
        "wrong_checkpoint_key",
    ],
)
def test_arming_fail_closed(fault):
    m = manifest()
    c, fn, checker = armed_clients(m)
    if fault.startswith("wrong_checkpoint"):
        m.update(recovery_strategy="retained-two-instance-v1", guard_bucket="pinned-private-bucket", guard_key="pinned-key")
        fn["Environment"]["Variables"].update(GUARD_BUCKET=m["guard_bucket"], GUARD_KEY_ARN=m["guard_key"])
        m["guard_bucket" if fault.endswith("bucket") else "guard_key"] = "different-storage"
    if fault == "paid":
        c["freetier"].get_account_plan_state.return_value["accountPlanType"] = "PAID"
    if fault == "low_credit":
        c["freetier"].get_account_plan_state.return_value["accountPlanRemainingCredits"]["amount"] = 1
    if fault == "wrong_account":
        c["sts"].get_caller_identity.return_value["Account"] = "000000000000"
    if fault == "disabled":
        c["scheduler"].get_schedule.return_value["State"] = "DISABLED"
    if fault == "late_end":
        c["scheduler"].get_schedule.return_value["EndDate"] += timedelta(hours=1)
    if fault == "wrong_hash":
        fn["CodeSha256"] = "unapproved"
    if fault == "wrong_run":
        fn["Environment"]["Variables"]["RUN_ID"] = "other2026"
    if fault == "same_role":
        checker["Role"] = "writer"
    if fault == "vpc":
        fn["VpcConfig"] = {"VpcId": "vpc-dependent-on-runtime"}
    if fault == "disposition":
        fn["Environment"]["Variables"]["DISPOSITION"] = "keep"
    if fault == "retry":
        c["scheduler"].get_schedule.return_value["Target"]["RetryPolicy"]["MaximumRetryAttempts"] = 0
    with pytest.raises((RuntimeError, ValueError)):
        armed_gate(m, c)


@pytest.mark.parametrize(
    "key,value",
    [
        ("region", "ca-central-1"),
        ("account", ""),
        ("downstream_db", "csa-native-test2026-control"),
        ("image", "latest"),
        ("cleanup_at", "2020-01-01T00:00:00+00:00"),
    ],
)
def test_native_manifest_refuses_unsafe_scope(key, value):
    m = manifest()
    m[key] = value
    with pytest.raises(ValueError):
        validate_manifest(m)


def test_read_denial_is_not_empty_inventory():
    c = {"sts": MagicMock(), "rds": MagicMock()}
    c["sts"].get_caller_identity.return_value = {"Account": "123456789012"}
    c["rds"].can_paginate.return_value = False
    c["rds"].describe_db_instances.side_effect = RuntimeError("denied")
    with pytest.raises(RuntimeError, match="denied"):
        Inventory("test2026", "123456789012", c).collect()
    with pytest.raises(ValueError):
        identity("../foreign", "123456789012")


@pytest.mark.parametrize("foreign", [False, True])
def test_inventory_queries_full_ecs_families_and_tracks_pending_deletion(foreign):
    m = manifest()
    clients = {"sts": MagicMock(), "ecs": MagicMock()}
    clients["sts"].get_caller_identity.return_value = {"Account": m["account"]}
    clients["ecs"].describe_clusters.return_value = {"clusters": []}
    queries = []

    class Reader(Inventory):
        def pages(self, service, operation, key, **params):
            if operation != "list_task_definitions":
                return []
            family = params["familyPrefix"]
            assert family in {self.prefix + "-" + n for n in m["tasks"]}
            queries.append((family, params["status"]))
            family = "foreign-family" if foreign else family
            return [f"arn:aws:ecs:us-east-2:{m['account']}:task-definition/{family}:1"]

    reader = Reader(m["run"], m["account"], clients)
    if foreign:
        with pytest.raises(RuntimeError, match="family mismatch"):
            reader.collect()
    else:
        rows = reader.collect()
        assert len(rows) == len(set(queries)) == 15
        assert {r["state"] for r in rows} == {"ACTIVE", "INACTIVE", "DELETE_IN_PROGRESS"}
        assert not public_report(rows)["active_resources_absent"]


def test_cleanup_waits_for_ecs_definition_deletion_without_reissuing_it():
    inventory = SimpleNamespace(collect=lambda: [
        {"kind": "task_definition", "id": "owned:1", "state": "DELETE_IN_PROGRESS"}
    ])
    clients = {"ecs": MagicMock()}
    result = cleanup_pass(inventory, clients)
    assert not result["failed_kinds"] and not result["attempted_kinds"]
    clients["ecs"].delete_task_definitions.assert_not_called()
    assert result["before"]["active_resources_absent"]
    assert not result["before"]["all_resources_absent"]
    assert result["before"]["residuals"][0]["state"] == "DELETE_IN_PROGRESS"


@pytest.mark.parametrize("remaining", ["cluster", "task"])
def test_deleting_definition_metadata_cannot_hide_remaining_compute(remaining):
    report = public_report([
        {"kind": "task_definition", "id": "owned:1", "state": "DELETE_IN_PROGRESS"},
        {"kind": remaining, "id": "owned-runtime"},
    ])
    assert not report["active_resources_absent"]
    assert not report["all_resources_absent"]


def test_pending_deletions_not_all_clear():
    report = public_report(
        [
            {"kind": "key", "id": "private-key-id", "state": "PendingDeletion"},
            {"kind": "secret", "id": "private-secret-arn", "state": "pending_deletion"},
        ]
    )
    assert report["active_resources_absent"] and not report["all_resources_absent"]
    assert "private-key-id" not in str(report)
    assert report["billing_clearance"] != "clear"


def test_cleanup_stops_tasks_before_any_other_mutations():
    inventory = SimpleNamespace(
        collect=lambda: [
            {"kind": "database", "id": "db"},
            {"kind": "task", "id": "task", "cluster": "cluster"},
        ]
    )
    c = {k: MagicMock() for k in ("ecs", "rds")}
    result = cleanup_pass(inventory, c)
    assert result["attempted_kinds"] == ["task"]
    c["rds"].modify_db_instance.assert_not_called()


def test_cleanup_partial_denial_and_managed_secret_are_not_false_success():
    rows = [
        {"kind": "endpoint", "id": "vpce-owned"},
        {"kind": "secret", "id": "managed-owner", "state": "active", "managed": True},
    ]
    c = {"ec2": MagicMock(), "secretsmanager": MagicMock()}
    c["ec2"].delete_vpc_endpoints.return_value = {"Unsuccessful": [{"ResourceId": "vpce-owned"}]}
    result = cleanup_pass(SimpleNamespace(collect=lambda: rows), c)
    assert result["failed_kinds"] == ["endpoint"]
    assert not result["before"]["active_resources_absent"]
    c["secretsmanager"].delete_secret.assert_not_called()


def test_security_group_references_removed_before_delete():
    rows = [{"kind": "security_group", "id": "sg-owned", "ingress": [{"IpProtocol": "tcp"}], "egress": []}]
    ec2 = MagicMock()
    cleanup_pass(SimpleNamespace(collect=lambda: rows), {"ec2": ec2})
    assert [call[0] for call in ec2.mock_calls] == ["revoke_security_group_ingress", "delete_security_group"]


def script(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guard_bundle_is_minimal_deterministic(tmp_path):
    module = script("package_native_guard")
    assert module.package(tmp_path / "one.zip") == module.package(tmp_path / "two.zip")
    from zipfile import ZipFile

    with ZipFile(tmp_path / "one.zip") as archive:
        assert set(archive.namelist()) == {
            "controlplane/__init__.py",
            "controlplane/native_inventory.py",
            "controlplane/native_watchdog.py",
        }


@pytest.mark.parametrize("actions", [["delete"], ["update"], ["delete", "create"]])
def test_deployment_rejects_existing_resource_mutation(actions):
    with pytest.raises(ValueError):
        script("native_deploy").review_plan(
            {
                "resource_changes": [
                    {"address": "aws_db_instance.store", "mode": "managed", "change": {"actions": actions}}
                ]
            },
            "runtime",
        )


def test_wrong_engine_cannot_be_accepted():
    c = {"rds": MagicMock()}
    c["rds"].describe_db_instances.return_value = {
        "DBInstances": [
            {
                "DBInstanceIdentifier": "db",
                "DBInstanceStatus": "available",
                "DBInstanceClass": "db.t4g.micro",
                "MultiAZ": False,
                "PubliclyAccessible": False,
                "StorageEncrypted": True,
                "Engine": "postgres",
                "EngineVersion": "16.1",
                "AllocatedStorage": 20,
                "BackupRetentionPeriod": 1,
            }
        ]
    }
    with pytest.raises(RuntimeError):
        NativeRun(manifest(), c).database("db")


@pytest.mark.parametrize("restore_error", [None, "InstanceQuotaExceeded", "AccessDenied"])
def test_controller_requests_real_timestamp_restore_and_independent_negative_proof(restore_error):
    m = manifest()
    baseline = datetime.now(UTC) - timedelta(minutes=2)
    rds = MagicMock()
    available = dict(DBInstanceStatus="available", BackupRetentionPeriod=1)
    rds.describe_db_instances.return_value = {"DBInstances": [available]}

    class Runner(NativeRun):
        verification_reads = 0

        def gate(self):
            return {"mock_only": True}

        def drained(self):
            return None

        def database(self, identifier):
            return dict(
                DbiResourceId=identifier,
                DBSubnetGroup={"DBSubnetGroupName": "owned"},
                VpcSecurityGroups=[{"VpcSecurityGroupId": "owned-sg"}],
                DBParameterGroups=[{"DBParameterGroupName": "owned-tls"}],
                LatestRestorableTime=baseline + timedelta(minutes=1),
                Endpoint={"Address": "private-restored-host"},
            )

        def restorable_window(self, source):
            return {"earliest": baseline - timedelta(minutes=1),
                    "latest": baseline + timedelta(minutes=1),
                    "backup_reference": "mock-only", "request_id": "mock-only"}

        def task(self, name, **kwargs):
            if name == "baseline":
                return {"baseline_at": baseline.isoformat()}
            if name == "control":
                return {"synthetic": "fixture"}
            if name == "receipt":
                return {"effects": 1, "receipt": "same-receipt"}
            if name == "verify":
                self.verification_reads += 1
                if self.verification_reads == 1:
                    return {"safe_to_resume": True, "local_intents": 0, "remote_effects": 0}
                if kwargs.get("expected_exit") == 2:
                    assert kwargs["extra"] == {"RESTORED_HOST": "private-restored-host"}
                    return {
                        "safe_to_resume": False,
                        "local_intents": 0,
                        "remote_effects": 1,
                        "findings": [{"reason": "remote_ahead_missing_intent"}],
                    }
                return {"safe_to_resume": True, "local_intents": 1, "remote_effects": 1}
            return {}

    runner = Runner(m, {"rds": rds})
    if restore_error:
        rds.restore_db_instance_to_point_in_time.side_effect = ClientError(
            {"Error": {"Code": restore_error, "Message": "offline rejection"}},
            "RestoreDBInstanceToPointInTime",
        )
        with pytest.raises(ClientError) as raised:
            runner.run()
        assert raised.value.response["Error"]["Code"] == restore_error
        assert runner.verification_reads == 2  # Empty and original only; no invented restored proof.
        rds.describe_db_instances.assert_not_called()
        rds.modify_db_instance.assert_not_called()
    else:
        result = runner.run()
        assert result["status"] == "NATIVE_SQL_PITR_ACCEPTANCE_PASSED"
        assert not result["full_application_acceptance"]
    assert rds.restore_db_instance_to_point_in_time.call_count == 1
    request = rds.restore_db_instance_to_point_in_time.call_args.kwargs
    assert request["RestoreTime"] == baseline
    assert request["SourceDBInstanceIdentifier"] == m["control_db"]
    assert request["TargetDBInstanceIdentifier"] == "csa-native-test2026-restore"
    assert not request["MultiAZ"] and not request["PubliclyAccessible"]
    assert "UseLatestRestorableTime" not in request
    assert "ManageMasterUserPassword" not in request
    assert "MasterUserPassword" not in request
    rds.delete_db_instance.assert_not_called()


def test_cleanup_durable_registry_precedes_mutation_and_independent_check(monkeypatch):
    import io
    import json
    from controlplane import native_watchdog

    m = manifest()
    session = MagicMock()
    clients = {
        n: MagicMock()
        for n in ("s3", "lambda", "sts", "rds", "ecs", "ec2", "ecr", "logs", "kms", "iam", "secretsmanager")
    }
    session.client.side_effect = lambda name: clients[name]
    monkeypatch.setattr(native_watchdog.boto3, "Session", lambda **kw: session)
    monkeypatch.setattr(native_watchdog, "Inventory", lambda *args: SimpleNamespace(collect=lambda: []))
    clients["s3"].get_object.return_value = {
        "Body": io.BytesIO(
            json.dumps([{"kind": "database", "id": "old", "owner_secret": "old-managed"}]).encode()
        )
    }
    trace = []
    clients["s3"].put_object.side_effect = lambda **kw: trace.append(("persist", kw["Key"], kw))

    def cleanup(*args):
        assert trace[0][1] == "inventory/registry.json"
        assert b"old-managed" in trace[0][2]["Body"]
        trace.append(("mutation",))
        return {"failed_kinds": []}

    monkeypatch.setattr(native_watchdog, "cleanup_pass", cleanup)
    for key, value in {
        "RUN_ID": m["run"],
        "ACCOUNT_ID": m["account"],
        "CLEANUP_AT": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "DISPOSITION": "discard_isolated_synthetic_state",
        "GUARD_BUCKET": "private",
        "GUARD_KEY_ARN": "owned-key",
        "CHECKER_ARN": "reader",
    }.items():
        monkeypatch.setenv(key, value)
    native_watchdog.handler({"untrusted_target": "production"}, None)
    assert trace[2][0] == "mutation"
    assert all(row[2]["SSEKMSKeyId"] == "owned-key" for row in trace if row[0] == "persist")
    clients["lambda"].invoke.assert_called_once_with(
        FunctionName="reader", InvocationType="Event", Payload=b"{}"
    )


def test_guard_before_deadline_has_no_cloud_operations(monkeypatch):
    from controlplane import native_watchdog

    session = MagicMock()
    monkeypatch.setattr(native_watchdog.boto3, "Session", session)
    monkeypatch.setenv("RUN_ID", "test2026")
    monkeypatch.setenv("ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("CLEANUP_AT", (datetime.now(UTC) + timedelta(hours=1)).isoformat())
    assert native_watchdog.handler({}, None)["status"] == "ARMED_NO_MUTATION"
    session.assert_not_called()


def test_inventory_pagination_budget_does_not_claim_absence():
    client = MagicMock()
    client.can_paginate.return_value = True
    client.get_paginator.return_value.paginate.return_value = [{"Rows": list(range(1001))}]
    with pytest.raises(RuntimeError, match="budget"):
        Inventory("test2026", "123456789012", {"rds": client}).pages("rds", "describe_db_instances", "Rows")


def test_post_teardown_access_denial_is_not_resource_absence():
    module = script("native_post_teardown")
    session = MagicMock()
    session.client.return_value.get_schedule.side_effect = RuntimeError("read denied")
    with pytest.raises((RuntimeError, TypeError)):
        module.guard_inventory({"run": "test2026", "account": "123456789012"}, session)


def test_finalizer_cannot_retire_guard_while_runtime_exists(monkeypatch, tmp_path):
    module = script("native_finalize_guard")
    session = MagicMock()
    monkeypatch.setattr(
        module,
        "Inventory",
        lambda *args: SimpleNamespace(collect=lambda: [{"kind": "database", "id": "owned-live"}]),
    )
    with pytest.raises(RuntimeError, match="absence"):
        module.finalize(manifest(), session, tmp_path / "archive", [])
    session.client.return_value.delete_bucket.assert_not_called()
    assert not (tmp_path / "archive").exists()


def test_finalizer_refuses_foreign_bucket_before_any_deletion(monkeypatch, tmp_path):
    module = script("native_finalize_guard")
    session = MagicMock()
    monkeypatch.setattr(module, "Inventory", lambda *args: SimpleNamespace(collect=lambda: []))
    m = manifest()
    m["guard_bucket"] = "production-bucket"
    with pytest.raises(ValueError, match="bucket"):
        module.finalize(m, session, tmp_path / "archive", [])
    session.client.return_value.delete_bucket.assert_not_called()


def test_inactive_task_metadata_deletion_failure_stays_visible():
    ecs = MagicMock()
    ecs.delete_task_definitions.return_value = {"failures": [{"reason": "RESOURCE_IN_USE"}]}
    rows = [{"kind": "task_definition", "id": "owned-definition", "state": "INACTIVE"}]
    result = cleanup_pass(SimpleNamespace(collect=lambda: rows), {"ecs": ecs})
    assert result["failed_kinds"] == ["task_definition"]
    assert not result["before"]["all_resources_absent"]


def test_guard_apply_time_clock_requires_explicit_approval():
    module = script("native_deploy")
    document = {
        "variables": {"cleanup_at": {"value": None}},
        "output_changes": {
            "guard_manifest": {
                "after": {"deadline_policy": "three_hours_from_guard_apply_start"},
                "after_unknown": {"cleanup_at": True, "deployment_started_at": True},
            }
        },
    }
    module.guard_deadline_gate(document, {"deadline_policy": "three_hours_from_guard_apply_start"})
    with pytest.raises(ValueError, match="approval"):
        module.guard_deadline_gate(document, {})


def test_guard_cannot_substitute_known_timestamp_for_deployment_clock():
    module = script("native_deploy")
    document = {
        "variables": {"cleanup_at": {"value": None}},
        "output_changes": {
            "guard_manifest": {
                "after": {"deadline_policy": "three_hours_from_guard_apply_start"},
                "after_unknown": {"cleanup_at": False, "deployment_started_at": False},
            }
        },
    }
    with pytest.raises(ValueError, match="only at apply"):
        module.guard_deadline_gate(document, {"deadline_policy": "three_hours_from_guard_apply_start"})


def test_explicit_old_deadline_still_expires():
    with pytest.raises(ValueError, match="within three hours"):
        script("native_deploy").guard_deadline_gate(
            {"variables": {"cleanup_at": {"value": "2020-01-01T00:00:00Z"}}}, {}
        )


def registry_session(monkeypatch, rows):
    import io
    module = script("native_initialize_registry")
    monkeypatch.setattr(module, "armed_gate", lambda *_: None)
    monkeypatch.setattr(module, "Inventory", lambda *_: SimpleNamespace(collect=lambda: rows))
    session, s3 = MagicMock(), MagicMock()
    session.client.return_value = s3
    class Missing(Exception):
        pass
    s3.exceptions.NoSuchKey = Missing
    s3.get_object.side_effect = [Missing(), {"Body": io.BytesIO(b"[]")}]
    s3.put_object.return_value = {"VersionId": "version-one"}
    m = manifest()
    m.update(guard_bucket=f"csa-native-{m['run']}-guard-{m['account']}", guard_key="guard-key")
    return module, m, session, s3


def test_registry_initialization_is_conditional_and_exact(monkeypatch):
    module, m, session, s3 = registry_session(monkeypatch, [])
    result = module.initialize_registry(m, session)
    assert result["status"] == "EMPTY_REGISTRY_INITIALIZED"
    assert s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    assert s3.put_object.call_args.kwargs["Body"] == b"[]"
    assert s3.get_object.call_args.kwargs["VersionId"] == "version-one"


def test_registry_initialization_refuses_existing_runtime(monkeypatch):
    module, m, session, s3 = registry_session(monkeypatch, [{"id": "existing"}])
    with pytest.raises(RuntimeError, match="must be empty"):
        module.initialize_registry(m, session)
    s3.put_object.assert_not_called()


def test_registry_initialization_does_not_treat_denial_as_absence(monkeypatch):
    module, m, session, s3 = registry_session(monkeypatch, [])
    s3.get_object.side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        module.initialize_registry(m, session)
    s3.put_object.assert_not_called()


def test_registry_initialization_preserves_existing_history(monkeypatch):
    import io
    module, m, session, s3 = registry_session(monkeypatch, [])
    s3.get_object.side_effect = None
    s3.get_object.return_value = {"Body": io.BytesIO(b'[{"id":"prior"}]')}
    assert module.initialize_registry(m, session)["status"] == "EXISTING_REGISTRY_PRESERVED"
    s3.put_object.assert_not_called()


def test_registry_initialization_does_not_swallow_concurrent_writer(monkeypatch):
    module, m, session, s3 = registry_session(monkeypatch, [])
    s3.put_object.side_effect = RuntimeError("precondition failed")
    with pytest.raises(RuntimeError, match="precondition failed"):
        module.initialize_registry(m, session)


def test_registry_initialization_requires_exact_readback(monkeypatch):
    import io
    module, m, session, s3 = registry_session(monkeypatch, [])
    s3.get_object.side_effect = [s3.exceptions.NoSuchKey(), {"Body": io.BytesIO(b"{}")}]
    with pytest.raises(RuntimeError, match="read-back"):
        module.initialize_registry(m, session)


@pytest.mark.parametrize("days", [0, 2, 14])
def test_native_profile_rejects_unapproved_retention(days):
    m = manifest()
    m["backup_retention_days"] = days
    with pytest.raises(ValueError, match="one-day automated backups"):
        validate_manifest(m)


@pytest.mark.parametrize("days,accepted", [(0, False), (1, True), (14, False)])
def test_native_database_requires_actual_nonzero_pinned_retention(days, accepted):
    c = {"rds": MagicMock()}
    c["rds"].describe_db_instances.return_value = {"DBInstances": [{
        "DBInstanceIdentifier": "db", "DBInstanceStatus": "available",
        "DBInstanceClass": "db.t4g.micro", "MultiAZ": False,
        "PubliclyAccessible": False, "StorageEncrypted": True,
        "Engine": "postgres", "EngineVersion": "17.11", "AllocatedStorage": 20,
        "BackupRetentionPeriod": days, "DBName": "csaacceptance",
    }]}
    if accepted:
        assert NativeRun(manifest(), c).database("db")["BackupRetentionPeriod"] == 1
    else:
        with pytest.raises(RuntimeError):
            NativeRun(manifest(), c).database("db")



def test_native_failure_diagnostic_never_serializes_secret_messages():
    import json
    from controlplane.native_tasks import safe_failure
    exc = RuntimeError('password=never-publish-this-value')
    exc.response = {'Error': {'Code': 'AccessDeniedException', 'Message': 'never-publish-this-value'}}
    result = safe_failure(exc)
    assert result['error_type'] == 'RuntimeError'
    assert result['aws_code'] == 'AccessDeniedException'
    assert 'never-publish-this-value' not in json.dumps(result)
    assert 'Message' not in json.dumps(result)



def test_rds_restore_bounds_use_actual_sdk_response_contract():
    import boto3
    client = boto3.client('rds', region_name='us-east-2', aws_access_key_id='mock', aws_secret_access_key='mock')
    assert 'EarliestRestorableTime' not in client.meta.service_model.shape_for('DBInstance').members
    assert 'LatestRestorableTime' in client.meta.service_model.shape_for('DBInstance').members
    shape = client.meta.service_model.shape_for('DBInstanceAutomatedBackup').members['RestoreWindow']
    assert set(shape.members) == {'EarliestTime', 'LatestTime'}


def window_fixture():
    now = datetime.now(UTC)
    source = {'DbiResourceId': 'physical-control', 'DBInstanceIdentifier': 'owned-control',
              'LatestRestorableTime': now}
    response = {'DBInstanceAutomatedBackups': [{
        'DbiResourceId': source['DbiResourceId'], 'DBInstanceIdentifier': source['DBInstanceIdentifier'],
        'DBInstanceAutomatedBackupsArn': 'arn:aws:rds:us-east-2:123456789012:auto-backup:owned',
        'Status': 'active', 'RestoreWindow': {'EarliestTime': now-timedelta(hours=1),
                                           'LatestTime': now+timedelta(minutes=1)},
    }], 'ResponseMetadata': {'RequestId': 'mock-only'}}
    return source, response


def test_restore_window_intersects_two_service_bounds_and_pins_physical_identity():
    source, response = window_fixture()
    c = {'rds': MagicMock()}
    c['rds'].describe_db_instance_automated_backups.return_value = response
    result = NativeRun(manifest(), c).restorable_window(source)
    assert result['latest'] == source['LatestRestorableTime']
    assert result['earliest'] == response['DBInstanceAutomatedBackups'][0]['RestoreWindow']['EarliestTime']
    c['rds'].describe_db_instance_automated_backups.assert_called_once_with(DbiResourceId='physical-control')


@pytest.mark.parametrize('case', ['missing_window', 'different_database', 'different_resource', 'ambiguous', 'denied'])
def test_restore_window_cannot_invent_or_cross_database_bounds(case):
    source, response = window_fixture()
    c = {'rds': MagicMock()}
    c['rds'].describe_db_instance_automated_backups.return_value = response
    if case == 'missing_window':
        response['DBInstanceAutomatedBackups'][0].pop('RestoreWindow')
        assert NativeRun(manifest(), c).restorable_window(source) is None
        return
    if case == 'different_database':
        response['DBInstanceAutomatedBackups'][0]['DBInstanceIdentifier'] = 'other'
    elif case == 'different_resource':
        response['DBInstanceAutomatedBackups'][0]['DbiResourceId'] = 'other'
    elif case == 'ambiguous':
        response['DBInstanceAutomatedBackups'] *= 2
    else:
        c['rds'].describe_db_instance_automated_backups.side_effect = PermissionError('denied')
        with pytest.raises(PermissionError):
            NativeRun(manifest(), c).restorable_window(source)
        return
    with pytest.raises(RuntimeError, match='identity mismatch'):
        NativeRun(manifest(), c).restorable_window(source)
