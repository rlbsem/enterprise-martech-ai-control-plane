"""Local PostgreSQL compatibility and offline task-boundary diagnostics; no AWS calls."""

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import botocore.session
import psycopg
import pytest
from botocore.exceptions import ClientError, WaiterError
from botocore.validate import validate_parameters
from psycopg import sql

from controlplane import db, recovery
from controlplane.native_app_tasks import preflight, seed
from controlplane.native_diagnostics import process_observation
from controlplane.recovery_lab import LocalDatabases
from controlplane.native_run import validate_manifest
from test_native_application import application_fixture
from test_native_diagnostics import runner


@pytest.mark.parametrize("expiry", ["infinity", "-infinity", "2030-01-01 00:00:00+00"])
def test_recovery_catalog_fingerprint_handles_legal_postgres_expiries(expiry):
    admin = os.environ["TEST_ADMIN_DSN"]
    role = "expiry_probe_" + uuid4().hex
    with db.connect(admin) as c:
        c.execute(sql.SQL("CREATE ROLE {} NOLOGIN VALID UNTIL {}").format(sql.Identifier(role), sql.Literal(expiry)))
    try:
        with db.connect(admin) as c:
            first = recovery.fingerprint(c)
            assert first == recovery.fingerprint(c) and len(first) == 64
    finally:
        with db.connect(admin) as c:
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_source_preflight_uses_real_roles_and_leaves_no_committed_probe(tmp_path):
    databases = LocalDatabases(os.environ["TEST_ADMIN_DSN"])
    try:
        control, remote = databases.create("source"), databases.create("remote")
        databases.setup(control, remote)
        dsns = databases.roles(control, remote)
        result = preflight(dsns, tmp_path / "probe")
        assert result["stage"] == "source_preflight_passed"
        assert result["comparison"]["local_intents"] == result["comparison"]["remote_effects"] == 0
        assert result["write_denials"] == 2 and result["recovery_operations_rolled_back"]
        with db.connect(dsns["reader"]) as c:
            assert c.execute("SELECT count(*) AS n FROM cp.recovery_sessions").fetchone()["n"] == 0
            assert c.execute("SELECT mode,epoch FROM cp.recovery_gate").fetchone() == {"mode": "open", "epoch": 0}
        wrong = {**dsns, "coordinator": dsns["reader"]}
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            preflight(wrong, tmp_path / "wrong-role")
        seed(dsns, tmp_path / "seed", "compat2026")
        with pytest.raises(ValueError, match="empty_ledgers"):
            preflight(dsns, tmp_path / "not-fresh")
    finally:
        assert databases.cleanup()["remaining_generated_roles"] == 0


def test_source_preflight_task_cannot_run_after_writes_are_frozen(tmp_path):
    r, clients, _, _ = runner(tmp_path, "receipt")
    r.m["acceptance_workflow"] = "application-recovery-v1"
    clients["ecs"].describe_task_definition.return_value["taskDefinition"]["containerDefinitions"][0]["command"][2] = "controlplane.native_app_tasks"
    assert r.task("receipt", extra={"RECOVERY_PHASE": "source-preflight"}) == {"accepted": True}
    request = clients["ecs"].run_task.call_args.kwargs
    shape = botocore.session.get_session().get_service_model("ecs").operation_model("RunTask").input_shape
    validate_parameters(request, shape)
    assert len(request["clientToken"]) == 64 and request["startedBy"] == r.prefix
    r.writes_frozen = True
    with pytest.raises(RuntimeError, match="Writers are frozen"):
        r.task("receipt", extra={"RECOVERY_PHASE": "source-preflight"})
    assert clients["ecs"].run_task.call_count == 1


def test_expired_authority_after_task_completion_is_archived_and_rejected(tmp_path):
    r, _, _, objects = runner(tmp_path)
    calls = 0

    def gate():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("expired authority")

    r.gate = gate
    with pytest.raises(RuntimeError, match="expired authority"):
        r.task("baseline")
    assert len(objects) == 2 and not any("result" in row for row in r.records)
    archive = json.loads(list(objects.values())[-1])
    assert archive["expectations"]["expected_exit"] == 0
    assert archive["execution_binding"]["runtime_manifest"] == r.m


@pytest.mark.parametrize("stop_denied", [False, True])
def test_wait_failure_keeps_task_context_even_when_stop_is_denied(tmp_path, stop_denied):
    r, clients, task, objects = runner(tmp_path)
    clients["ecs"].get_waiter.return_value.wait.side_effect = WaiterError("tasksStopped", "test", {})
    if stop_denied:
        clients["ecs"].stop_task.side_effect = ClientError({"Error": {"Code": "AccessDenied"},
            "ResponseMetadata": {"RequestId": "stop-request"}}, "StopTask")
    else:
        clients["ecs"].stop_task.return_value = {"task": task, "ResponseMetadata": {"RequestId": "stop-request"}}
    with pytest.raises((RuntimeError, ClientError)):
        r.task("baseline")
    assert len(objects) == 2
    final = r.records[-1]["task_observation"]
    assert final["phase"] == ("wait_failed_stop_failed" if stop_denied else "wait_failed_stop_requested")
    assert not any("result" in row for row in r.records)
    assert json.loads(list(objects.values())[-1])["aws_request_receipts"]["StopTask"]["RequestId"] == "stop-request"


def test_launch_error_retains_service_code_without_becoming_acceptance(tmp_path):
    r, clients, _, objects = runner(tmp_path)
    clients["ecs"].run_task.side_effect = ClientError({"Error": {"Code": "AccessDenied"},
        "ResponseMetadata": {"RequestId": "launch-request"}}, "RunTask")
    with pytest.raises(ClientError):
        r.task("baseline")
    assert len(objects) == 1
    assert r.records[0]["task_observation"]["phase"] == "launch_failed"
    assert r.records[0]["task_observation"]["log_read_error"]["aws_code"] == "AccessDenied"


def test_durable_application_claim_refuses_controller_restart(tmp_path, monkeypatch):
    f, controller = application_fixture(tmp_path, monkeypatch)
    original = f.runner.c["s3"].put_object.side_effect

    def conditional(**kwargs):
        if kwargs["Key"] in f.objects and kwargs.get("IfNoneMatch") == "*":
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        return original(**kwargs)

    f.runner.c["s3"].put_object.side_effect = conditional
    controller.claim_execution()
    with pytest.raises(ClientError):
        controller.claim_execution()
    f.rds.delete_db_instance.assert_not_called()
    f.rds.restore_db_instance_to_point_in_time.assert_not_called()


def test_old_application_manifest_protocol_is_rejected_before_execution(tmp_path, monkeypatch):
    f, _ = application_fixture(tmp_path, monkeypatch)
    validate_manifest(f.runner.m)
    f.runner.m.pop("application_protocol")
    with pytest.raises(ValueError, match="source-preflight-v2"):
        validate_manifest(f.runner.m)
    f.rds.restore_db_instance_to_point_in_time.assert_not_called()


def test_process_diagnostics_redact_tokens_dsns_endpoints_and_large_logs(tmp_path):
    password, token = "synthetic-password-sensitive", "synthetic-bearer-sensitive"
    folder = tmp_path
    (folder / "0-api.log").write_text(
        f"password={password} Bearer {token} host=private.us-east-2.rds.amazonaws.com 10.43.0.7 123456789012\n"
        + "x" * 40_000, encoding="utf-8")
    runtime = SimpleNamespace(folder=folder, tokens={"agent": token}, executor="executor-sensitive",
        injector="injector-sensitive", dsns={"reader": f"host=private.us-east-2.rds.amazonaws.com password={password}"},
        processes=[("api", SimpleNamespace(poll=lambda: 1))])
    record = process_observation(runtime)
    raw = json.dumps(record)
    assert all(v not in raw for v in [password, token, "private.us-east-2.rds.amazonaws.com", "10.43.0.7", "123456789012"])
    assert record["logs"][0]["truncated"] and record["processes"][0]["return_code"] == 1
    assert len(raw) < 110_000
