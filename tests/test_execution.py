import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import psycopg
import pytest

from controlplane import db, worker
from conftest import auth, customer, due, effects, event, job, plan, post, propose, state


def test_approved_send_and_duplicate_approval(api):
    cid = customer(api)
    p = propose(api, cid, "send_marketing")
    body = {"expected_revision": state(api, cid)["revision"]}
    first = post(api, f"/proposals/{p['id']}/approve", body, "approver")
    second = post(api, f"/proposals/{p['id']}/approve", body, "approver")
    assert first["approved"] and second["duplicate"]
    worker.once()
    assert job(p["id"])["status"] == "succeeded" and effects(p["id"]) == 1
    assert worker.once() is False


@pytest.mark.parametrize("column,expected", [("expires_at", "proposal_expired"), ("approved_until", "approval_expired")])
def test_expired_approval_cannot_send_or_extend(api, column, expected):
    cid = customer(api)
    p = propose(api, cid, "send_marketing")
    body = {"expected_revision": state(api, cid)["revision"]}
    post(api, f"/proposals/{p['id']}/approve", body, "approver")
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        # Parametrized closed column list; simulate time passing without sleeping 15 minutes.
        c.execute(f"UPDATE cp.proposals SET {column}=clock_timestamp()-interval '1 second' WHERE id=%s", (p["id"],))
    assert post(api, f"/proposals/{p['id']}/approve", body, "approver")["approved"] is False
    worker.once()
    assert job(p["id"])["reason"] == expected and effects(p["id"]) == 0


@pytest.mark.parametrize("mode", ["429", "500", "timeout_before", "timeout_after"])
def test_transient_remote_failures_recover_with_one_effect(api, mode, evidence):
    cid = customer(api)
    p = propose(api, cid)
    plan(p["id"], [mode])
    worker.once()
    first = job(p["id"])
    assert first["status"] == "retry", first
    if mode == "timeout_after":
        assert effects(p["id"]) == 1 and first["uncertain"]
    else:
        assert effects(p["id"]) == 0
    due(p["id"])
    worker.once()
    final = job(p["id"])
    assert final["status"] == "succeeded" and effects(p["id"]) == 1
    assert final["attempts"] == 2
    if mode == "timeout_after":
        assert final["reason"] == "reconciled_committed_effect"
        evidence("unknown-result", {"failure": mode, "worker_attempts": final["attempts"],
                                    "remote_business_effects": effects(p["id"]), "idempotency_key": p["id"],
                                    "first_status": first["status"], "final_status": final["status"],
                                    "resolution": final["reason"], "receipt": final["remote_result"]})


def test_committed_effect_is_reconciled_even_after_consent_revoked(api):
    cid = customer(api)
    p = propose(api, cid, "send_marketing")
    post(api, f"/proposals/{p['id']}/approve", {"expected_revision": state(api, cid)["revision"]}, "approver")
    plan(p["id"], ["timeout_after"])
    worker.once()
    event(api, "consent", "one", 2, {"consent": False})
    due(p["id"])
    worker.once()
    assert job(p["id"])["reason"] == "reconciled_committed_effect"
    assert effects(p["id"]) == 1


def test_unknown_without_effect_cannot_retry_after_revocation(api):
    cid = customer(api)
    p = propose(api, cid, "send_marketing")
    post(api, f"/proposals/{p['id']}/approve", {"expected_revision": state(api, cid)["revision"]}, "approver")
    plan(p["id"], ["timeout_before"])
    worker.once()
    event(api, "consent", "one", 2, {"consent": False})
    due(p["id"])
    worker.once()
    assert job(p["id"])["status"] == "blocked" and effects(p["id"]) == 0


def test_permanent_failure_and_governed_operator_requeue(api):
    cid = customer(api)
    p = propose(api, cid)
    plan(p["id"], ["422"])
    worker.once()
    assert job(p["id"])["status"] == "dead"
    ex = api.get("/exceptions", headers=auth("operator")).json()[-1]
    event(api, "crm", "one", 2, {"owner": "new-owner"})
    post(api, f"/exceptions/{ex['id']}/resolve", {"disposition": "requeue", "note": "Retry after adapter investigation"}, "operator")
    worker.once()
    assert job(p["id"])["status"] == "blocked" and effects(p["id"]) == 0


def test_retry_exhaustion_is_visible_and_can_recover(api):
    cid = customer(api)
    p = propose(api, cid)
    plan(p["id"], ["500"] * 4)
    for _ in range(4):
        due(p["id"])
        worker.once()
    assert job(p["id"])["reason"] == "retry_exhausted"
    assert job(p["id"])["uncertain"]  # Dead letter does not claim that a remote effect is impossible.
    ex = api.get("/exceptions", headers=auth("operator")).json()[-1]
    post(api, f"/exceptions/{ex['id']}/resolve", {"disposition": "requeue", "note": "Adapter recovered; reconcile original intent"}, "operator")
    worker.once()
    assert job(p["id"])["status"] == "succeeded" and effects(p["id"]) == 1


def test_competing_workers_execute_one_job_once(api, evidence):
    cid = customer(api)
    p = propose(api, cid)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: worker.once(), range(8)))
    assert sum(claims) == 1
    assert effects(p["id"]) == 1 and job(p["id"])["attempts"] == 1
    evidence("competing-workers", {"workers": 8, "claimed": sum(claims), "effects": effects(p["id"])})


def test_expired_lease_fences_old_worker(api):
    cid = customer(api)
    p = propose(api, cid)
    old = worker.claim(lease_seconds=-1)
    replacement = worker.claim()
    assert old["lease_token"] != replacement["lease_token"]
    worker.execute(replacement)
    assert worker.finish(old, "dead", "old_worker_must_not_win") is False
    assert job(p["id"])["status"] == "succeeded" and effects(p["id"]) == 1


def test_process_death_after_remote_commit_recovers(api, evidence):
    cid = customer(api)
    p = propose(api, cid)
    plan(p["id"], ["timeout_after"])
    env = {**os.environ, "REMOTE_TIMEOUT": "5"}
    child = subprocess.Popen([sys.executable, "-m", "controlplane", "worker", "--once"], env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        for _ in range(100):
            if effects(p["id"]) == 1:
                break
            assert child.poll() is None
            time.sleep(0.02)
        else:
            pytest.fail("worker did not reach remote commit")
        child.kill()
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    assert job(p["id"])["status"] == "leased"
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("UPDATE cp.jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s", (p["id"],))
    worker.once()
    assert job(p["id"])["status"] == "succeeded" and effects(p["id"]) == 1
    evidence("process-death", {"killed_process": True, "recovered_attempts": job(p["id"])["attempts"],
                               "effects": effects(p["id"]), "final": job(p["id"])["status"]})


def test_remote_idempotency_payload_conflict(api):
    cid = customer(api)
    pid = str(uuid4())
    url = os.environ["DOWNSTREAM_URL"] + "/effects"
    headers = {"Authorization": "Bearer " + os.environ["EXECUTOR_TOKEN"], "Idempotency-Key": pid}
    body = {"customer_id": cid, "action": "sync_profile", "revision": 1, "payload": {"owner": "one"}}
    assert httpx.post(url, headers=headers, json=body, trust_env=False).status_code == 201
    assert httpx.post(url, headers=headers, json=body, trust_env=False).status_code == 200
    body["payload"]["owner"] = "changed"
    assert httpx.post(url, headers=headers, json=body, trust_env=False).status_code == 409
    assert effects(pid) == 1


def test_agent_cannot_mutate_downstream(api):
    cid = customer(api)
    response = httpx.post(os.environ["DOWNSTREAM_URL"] + "/effects", headers={**auth("agent"), "Idempotency-Key": str(uuid4())},
                          json={"customer_id": cid, "action": "sync_profile", "revision": 1, "payload": {}}, trust_env=False)
    assert response.status_code == 401


@pytest.mark.parametrize("statement", ["UPDATE cp.audit SET actor='forged'", "DELETE FROM cp.audit", "TRUNCATE cp.audit",
                                        "UPDATE cp.policies SET document='{}'", "DELETE FROM cp.events",
                                        "INSERT INTO remote.effects(key,hash,body,receipt) VALUES (gen_random_uuid(),'x','{}',gen_random_uuid())"])
def test_application_database_privileges_protect_history_and_remote(api, statement):
    customer(api)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.connect() as c:
            c.execute(statement)


def test_remote_database_cannot_modify_customer_state():
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.connect(os.environ["REMOTE_DSN"]) as c:
            c.execute("UPDATE cp.customers SET state='{}'")


def test_migration_replay_and_policy_snapshot(api):
    from controlplane.policy import POLICY, POLICY_HASH
    db.migrate(os.environ["TEST_ADMIN_DSN"])
    customer(api)
    with db.connect() as c:
        assert c.execute("SELECT document FROM cp.policies WHERE hash=%s", (POLICY_HASH,)).fetchone()["document"] == POLICY
        assert c.execute("SELECT count(*) AS n FROM cp.audit WHERE policy_hash=%s", (POLICY_HASH,)).fetchone()["n"] > 0


def test_concurrent_remote_retransmissions_have_one_receipt(api):
    cid = customer(api)
    pid = str(uuid4())
    headers = {"Authorization": "Bearer " + os.environ["EXECUTOR_TOKEN"], "Idempotency-Key": pid}
    body = {"customer_id": cid, "action": "sync_profile", "revision": 1, "payload": {"owner": "one"}}
    def send(_):
        response = httpx.post(os.environ["DOWNSTREAM_URL"] + "/effects", headers=headers, json=body, trust_env=False)
        assert response.status_code in (200, 201)
        return response.json()["receipt"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(send, range(8)))
    assert len(set(receipts)) == 1 and effects(pid) == 1


def test_policy_version_change_blocks_existing_permission(api):
    from psycopg.types.json import Jsonb
    from controlplane.policy import digest
    cid = customer(api)
    p = propose(api, cid)
    historic = {"version": "synthetic-older-policy"}
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("INSERT INTO cp.policies VALUES (%s,%s)", (digest(historic), Jsonb(historic)))
        c.execute("UPDATE cp.proposals SET policy_hash=%s WHERE id=%s", (digest(historic), p["id"]))
    worker.once()
    assert job(p["id"])["reason"] == "policy_changed" and effects(p["id"]) == 0


def test_audit_trigger_protects_against_accidental_owner_update(api):
    customer(api)
    with pytest.raises(psycopg.errors.RaiseException, match="immutable history"):
        with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
            c.execute("UPDATE cp.audit SET actor='accidental-admin-edit'")


def test_audit_records_provenance_policy_decisions_and_attempts_without_tokens(api):
    import json
    cid = customer(api)
    p = propose(api, cid)
    worker.once()
    records = api.get("/audit", headers=auth("operator")).json()
    source = next(r for r in records if r["kind"] == "source_decision")
    assert {"before", "after", "incoming", "provenance", "decisions"} <= source["detail"].keys()
    assert all(r["policy_hash"] and r["actor"] for r in records)
    kinds = {r["kind"] for r in records if r["subject"] == p["id"]}
    assert {"proposal", "claim", "dispatch_authorized", "execution_result"} <= kinds
    serialized = json.dumps(records)
    assert os.environ["EXECUTOR_TOKEN"] not in serialized
    assert "Bearer " not in serialized


def test_migration_content_change_fails_closed():
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("UPDATE public.schema_migrations SET hash='changed'")
    with pytest.raises(RuntimeError, match="Applied migration changed"):
        db.migrate(os.environ["TEST_ADMIN_DSN"])
