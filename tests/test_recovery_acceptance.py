"""Fresh PostgreSQL/HTTP restore proof plus negative durable gate and role boundaries."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from controlplane import db, operations, recovery
from controlplane.policy import digest
from controlplane.recovery_lab import LocalDatabases, Runtime, controller, execute_local, refresh


@pytest.fixture
def pair(tmp_path):
    databases = LocalDatabases(os.environ["TEST_ADMIN_DSN"])
    control, remote = databases.create("control"), databases.create("remote")
    databases.setup(control, remote)
    runtime = Runtime(tmp_path, databases.roles(control, remote))
    runtime.start("api")
    subjects = [("contract0", runtime.seed("contract0"))]
    runtime.stop()
    gate = controller(
        databases,
        control,
        remote,
        digest("unit contract; not an infrastructure restore"),
        "unit-contract-no-PITR",
        digest("test deployment"),
        runtime,
    )
    try:
        yield databases, control, remote, runtime, subjects, gate
    finally:
        runtime.stop()
        assert databases.cleanup()["remaining_generated_databases"] == 0


def test_complete_database_restore_http_and_process_acceptance(tmp_path):
    result = execute_local(os.environ["TEST_ADMIN_DSN"], tmp_path)
    assert result["status"] == "passed" and len(result["scenarios"]) == 8
    assert all(r["status"] == "passed" for r in result["scenarios"].values())
    assert result["scenarios"]["A_remote_ahead"]["missing_restored_intents"] == 2
    assert result["scenarios"]["A_remote_ahead"]["verification"]["safe_to_resume"] is False
    assert result["scenarios"]["C_killed_worker"]["old_completion_accepted"] is False
    assert result["scenarios"]["F_current_authority"]["new_dispatch"]["decision"] == "deny"
    assert result["scenarios"]["G_safe_resumption"]["post_resume"]["safe_to_resume"] is True
    assert result["scenarios"]["G_safe_resumption"]["duplicate_business_effects"] == 0
    assert len(result["independent_receipts"]) == result["independent_final_effects"] == 6
    assert result["cleanup"] == {
        "created_databases": 9,
        "remaining_generated_databases": 0,
        "created_roles": 6,
        "remaining_generated_roles": 0,
    }
    assert result["aws_execution"] is False
    if os.environ.get("EVIDENCE_DIR"):
        (Path(os.environ["EVIDENCE_DIR"]) / "cloud-recovery.json").write_text(
            json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
        )


def test_closed_gate_blocks_work_but_allows_authoritative_refresh(pair):
    _, _, _, runtime, subjects, gate = pair
    session = gate.begin()
    assert runtime.once() is False
    runtime.start("api")
    assert runtime.propose(subjects[0][1]) == {"decision": "deny", "reason": "recovery_closed"}
    with pytest.raises(ValueError, match="runtime_not_quiescent"):
        gate.verify(session)
    runtime.stop()
    missing = gate.verify(session)
    assert not missing["safe_to_resume"] and {f["field"] for f in missing["findings"]} == {
        "consent",
        "suppressed",
    }
    refresh(runtime, subjects, 2)
    assert gate.verify(session)["safe_to_resume"]


def test_stored_report_without_exact_approval_cannot_resume(pair):
    *_, runtime, subjects, gate = pair
    session = gate.begin()
    refresh(runtime, subjects, 2)
    proof = gate.verify(session)
    with pytest.raises(ValueError, match="exact_operator_approval_required"):
        gate.resume(session, proof["proof_id"])
    with pytest.raises(ValueError, match="fresh_safe_proof_required"):
        gate.resume(session, str(uuid4()))
    assert runtime.once() is False


def test_current_control_change_invalidates_approved_proof(pair):
    *_, runtime, subjects, gate = pair
    session = gate.begin()
    refresh(runtime, subjects, 2)
    proof = gate.verify(session)
    gate.approve(session, proof["proof_id"])
    runtime.start("api")
    runtime.event("crm", subjects[0][0], 2, {"owner": "new_owner"})
    runtime.stop()
    with pytest.raises(ValueError, match="recovery_evidence_changed"):
        gate.resume(session, proof["proof_id"])
    assert runtime.once() is False
    current = gate.verify(session)
    gate.approve(session, current["proof_id"])
    assert gate.resume(session, current["proof_id"])["resumed"]


@pytest.mark.parametrize("fault", ["binding", "quiescence", "stale_witness", "wrong_database"])
def test_recovery_rejects_stale_or_wrong_scope(pair, fault):
    *_, runtime, subjects, gate = pair
    session = gate.begin()
    refresh(runtime, subjects, 2)
    if fault == "binding":
        gate.binding["deployment_revision"] = "f" * 64
        reason = "recovery_binding_changed"
    elif fault == "quiescence":
        gate.witness = lambda: {"quiescent": False}
        reason = "runtime_not_quiescent"
    elif fault == "stale_witness":
        gate.witness = lambda: {
            "quiescent": True,
            "observed_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "registered_runtime": [],
        }
        reason = "stale_quiescence_witness"
    else:
        gate.remote_identity = "0" * 64
        reason = "recovery_database_identity_changed"
    with pytest.raises(ValueError, match=reason):
        gate.verify(session)


def test_concurrent_recovery_and_old_session_proof_are_rejected(pair):
    *_, runtime, subjects, gate = pair
    session = gate.begin()
    with pytest.raises(ValueError, match="recovery_already_active"):
        gate.begin()
    refresh(runtime, subjects, 2)
    proof = gate.verify(session)
    gate.approve(session, proof["proof_id"])
    gate.resume(session, proof["proof_id"])
    next_session = gate.begin()
    with pytest.raises(ValueError, match="closed_current_recovery_session_required"):
        gate.resume(session, proof["proof_id"])
    with pytest.raises(ValueError, match="fresh_safe_proof_required"):
        gate.resume(next_session, proof["proof_id"])


@pytest.mark.parametrize(
    "role,statement",
    [
        ("reader", "UPDATE cp.recovery_gate SET mode='open'"),
        ("worker", "UPDATE cp.recovery_gate SET mode='open'"),
        ("reader", "INSERT INTO cp.recovery_proofs SELECT * FROM cp.recovery_proofs"),
        ("coordinator", "INSERT INTO cp.customers SELECT * FROM cp.customers"),
        ("remote_reader", "DELETE FROM remote.effects"),
    ],
)
def test_recovery_authorities_cannot_forge_business_or_open_gate(pair, role, statement):
    *_, runtime, subjects, gate = pair
    gate.begin()
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.connect(runtime.dsns[role]) as c:
        c.execute(statement)


def test_recovery_proof_and_binding_history_cannot_be_edited(pair):
    databases, control, _, runtime, subjects, gate = pair
    session = gate.begin()
    refresh(runtime, subjects, 2)
    proof = gate.verify(session)
    for statement in [
        "UPDATE cp.recovery_proofs SET report='{}'::jsonb",
        "DELETE FROM cp.recovery_proofs",
        "UPDATE cp.recovery_sessions SET binding='{}'::jsonb",
        "DELETE FROM cp.recovery_sessions",
    ]:
        with pytest.raises(psycopg.errors.RaiseException), db.connect(databases.dsn(control)) as c:
            c.execute(statement)
    assert gate.verify(session)["snapshot_hash"] == proof["snapshot_hash"]


def test_reconciler_rejects_business_body_that_disagrees_with_receipt_hash():
    mutation = {"action": "sync_profile", "payload": {"email": "synthetic@example.test"}}
    result = operations.compare_receipts(
        [
            {
                "id": "key",
                "status": "succeeded",
                "mutation": mutation,
                "uncertain": False,
                "remote_result": {"receipt": "receipt"},
            }
        ],
        [{"key": "key", "hash": digest(mutation), "body": {"action": "unexpected"}, "receipt": "receipt"}],
    )
    assert not result["safe_to_resume"]
    assert {x["reason"] for x in result["findings"]} == {"receipt_body_hash_conflict"}


def test_local_harness_rejects_nonisolated_or_unowned_database_targets():
    with pytest.raises(ValueError, match="isolated_local_test_cluster_required"):
        LocalDatabases("host=production.invalid dbname=control_test")
    local = LocalDatabases(os.environ["TEST_ADMIN_DSN"])
    with pytest.raises(ValueError, match="unowned_snapshot"):
        local.create("bad", template="some_other_database")


def test_runtime_children_have_scoped_credentials_only(pair):
    *_, runtime, _, _ = pair
    forbidden = {"TEST_ADMIN_DSN", "CONTROL_ADMIN_DSN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}
    for role in ("api", "remote", "worker"):
        env = runtime.env(role)
        assert not set(env) & forbidden
        assert not (role == "remote" and "CONTROL_DSN" in env)
        assert not (role != "remote" and "REMOTE_DSN" in env)


def test_previous_workload_runtime_is_preserved_at_exact_revision():
    import hashlib

    root = Path(__file__).resolve().parents[1]
    record = json.loads((root / "docs/evidence/cloud-recovery/prior-runtime/manifest.json").read_bytes())
    assert record["source_revision"] == "0f934d6b14f2cb23fc88f4c33a65ba3034133847"
    assert set(record["files"]) == {"scripts/native_deploy.py"} | {
        "src/controlplane/" + n + ".py"
        for n in [
            "db",
            "worker",
            "operations",
            "service",
            "cloud",
            "native_arming",
            "native_inventory",
            "native_run",
        ]
    }
    for row in record["files"].values():
        assert hashlib.sha256((root / row["path"]).read_bytes()).hexdigest() == row["sha256"]


def test_deployment_verifier_does_not_confuse_closed_reconciliation_with_resume(pair):
    *_, runtime, subjects, gate = pair
    session = gate.begin()
    refresh(runtime, subjects, 2)
    proof = gate.verify(session)
    assert proof["safe_to_resume"]
    record = operations.runtime_acceptance_snapshot(runtime.dsns["reader"], runtime.dsns["remote_reader"])
    assert not record["safe_to_resume"] and record["findings"] == [{"reason": "execution_gate_closed"}]
    gate.approve(session, proof["proof_id"])
    gate.resume(session, proof["proof_id"])
    assert operations.runtime_acceptance_snapshot(runtime.dsns["reader"], runtime.dsns["remote_reader"])[
        "safe_to_resume"
    ]


def test_old_safe_proof_expires_without_changing_history(pair, monkeypatch):
    *_, runtime, subjects, gate = pair
    session = gate.begin()
    refresh(runtime, subjects, 2)
    proof = gate.verify(session)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=91)

    monkeypatch.setattr(recovery, "datetime", Later)
    with pytest.raises(ValueError, match="fresh_safe_proof_required"):
        gate.approve(session, proof["proof_id"])
    assert runtime.once() is False


def test_direct_work_insert_cannot_bypass_closed_admission(pair):
    *_, runtime, _, gate = pair
    gate.begin()
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), db.connect(runtime.dsns["api"]) as c:
        c.execute("INSERT INTO cp.jobs(id,status) VALUES (%s,'ready')", (uuid4(),))
