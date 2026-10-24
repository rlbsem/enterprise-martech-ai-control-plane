"""Actual PostgreSQL admission/permit races; existing HTTP destination remains independent."""

import os
import subprocess
import sys
import time

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from controlplane import capacity, db, service, worker
from controlplane.contracts import Proposal, SourceEvent


def configure(limit=10, concurrency=2, backlog=2):
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        for name in ("healthy", "affected"):
            c.execute(
                "INSERT INTO cp.destinations VALUES (%s,%s,%s,%s,%s,%s)",
                (name, os.environ["DOWNSTREAM_URL"], "EXECUTOR_TOKEN", limit, concurrency, backlog),
            )
            c.execute("INSERT INTO cp.destination_routes VALUES (%s,%s)", (name, name))


def proposal(owner="healthy", key=None):
    cid = service.ingest(
        "crm",
        SourceEvent(
            event_id=str(uuid4()),
            subject=str(uuid4()),
            sequence=1,
            attributes={"email": str(uuid4()) + "@example.test", "owner": owner},
        ),
    )["customer_id"]
    return Proposal(
        request_id=key or uuid4(),
        customer_id=cid,
        expected_revision=1,
        action="sync_profile",
        rationale="Synthetic capacity acceptance",
    )


def test_overload_duplicate_and_other_destination():
    configure(backlog=1)
    command = proposal()
    first = service.propose("agent", command)
    assert service.propose("agent", command) == first
    with pytest.raises(ValueError, match="overloaded"):
        service.propose("agent", proposal())
    assert service.propose("agent", proposal("affected"))["decision"] == "allow"
    with pytest.raises(ValueError, match="not_authorized"):
        service.propose("agent", proposal("unregistered"))


def test_admission_race_never_overfills():
    configure(backlog=1)
    commands = [proposal() for _ in range(8)]

    def submit(command):
        try:
            service.propose("agent", command)
            return True
        except ValueError as e:
            assert str(e) == "destination_overloaded"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(submit, commands)) == 1


def test_concurrent_permits_and_reserved_receipt_capacity():
    configure(limit=100, concurrency=2)
    route = {"id": "healthy", "requests_per_second": 100, "concurrency": 2}
    with ThreadPoolExecutor(max_workers=8) as pool:
        permits = list(pool.map(lambda _: capacity.acquire(route, "dispatch"), range(8)))
    assert 1 <= len([p for p in permits if p]) <= 2
    for token in permits:
        if token:
            capacity.release(route, token, False)
    # Pin the stored bucket ahead: the backward-clock clamp preserves it across real second boundaries.
    route["requests_per_second"] = 3
    with db.connect() as c:
        c.execute(
            "UPDATE cp.capacity_state SET window_id=floor(extract(epoch FROM clock_timestamp()))+60,used=2,next_probe='-infinity'"
        )
    assert capacity.acquire(route, "dispatch") is None
    assert capacity.acquire(route, "receipt") is not None


def test_permit_survives_client_restart_and_expires():
    configure(concurrency=1)
    route = {"id": "healthy", "requests_per_second": 10, "concurrency": 1}
    token = capacity.acquire(route, "receipt")
    assert token and capacity.acquire(route, "receipt") is None
    with db.connect() as c:
        c.execute("UPDATE cp.capacity_state SET next_probe='-infinity'")
    assert capacity.acquire(route, "receipt") is None
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("UPDATE cp.request_permits SET expires=clock_timestamp()-interval '1 second'")
    assert capacity.acquire(route, "receipt") is not None


def test_bound_route_immutable_and_execution_readback():
    configure()
    result = service.propose("agent", proposal())
    with db.connect() as c, pytest.raises(psycopg.Error):
        c.execute("UPDATE cp.job_routes SET destination='affected'")
    with db.connect() as c, pytest.raises(psycopg.Error):
        c.execute("UPDATE cp.destinations SET url='http://attacker.invalid'")
    assert worker.once()
    with db.connect() as c:
        job = c.execute("SELECT * FROM cp.jobs WHERE id=%s", (result["id"],)).fetchone()
    assert job["status"] == "succeeded"
    assert capacity.destination(job)["id"] == "healthy"


def test_scheduler_deferral_keeps_attempt_budget_and_fences():
    configure()
    service.propose("agent", proposal())
    job = worker.claim()
    capacity.defer(job, False)
    with db.connect() as c:
        row = c.execute("SELECT * FROM cp.jobs WHERE id=%s", (job["id"],)).fetchone()
    assert row["attempts"] == 0 and row["status"] == "retry"
    assert worker.finish(job, "succeeded", "stale") is False


def test_shared_cooldown_is_destination_specific():
    configure()
    route = {"id": "affected", "requests_per_second": 10, "concurrency": 2}
    token = capacity.acquire(route, "dispatch")
    capacity.release(route, token, True)
    assert capacity.acquire(route, "receipt") is None
    service.propose("agent", proposal("affected"))
    healthy = service.propose("agent", proposal("healthy"))
    assert str(worker.claim()["id"]) == healthy["id"]


def test_bound_worker_death_preserves_permit_and_receipt(evidence):
    from conftest import effects, job, plan

    configure(concurrency=1)
    command = proposal()
    result = service.propose("agent", command)
    plan(result["id"], ["timeout_after"])
    child = subprocess.Popen(
        [sys.executable, "-m", "controlplane", "worker", "--once"],
        env={**os.environ, "REMOTE_TIMEOUT": "5"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        for _ in range(200):
            if effects(result["id"]) == 1:
                break
            assert child.poll() is None
            time.sleep(0.02)
        else:
            pytest.fail("no independent committed effect")
        child.kill()
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    route = {"id": "healthy", "requests_per_second": 10, "concurrency": 1}
    assert capacity.acquire(route, "receipt") is None
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("UPDATE cp.customers SET revision=revision+1 WHERE id=%s", (command.customer_id,))
    # Actual expiry, not an edited lease/permit; the receiver effect survives separately.
    time.sleep(30.1)
    assert worker.once()
    assert job(result["id"])["reason"] == "reconciled_committed_effect"
    assert effects(result["id"]) == 1
    evidence(
        "bound-worker-death",
        {
            "process_killed": True,
            "permit_blocked_after_death": True,
            "real_expiry_wait_seconds": 30.1,
            "historical_receipt_after_revision_change": True,
            "effects": 1,
        },
    )


def test_bound_new_action_rechecks_revision():
    configure()
    command = proposal()
    result = service.propose("agent", command)
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("UPDATE cp.customers SET revision=revision+1 WHERE id=%s", (command.customer_id,))
    assert worker.once()
    with db.connect() as c:
        assert (
            c.execute("SELECT status FROM cp.jobs WHERE id=%s", (result["id"],)).fetchone()["status"]
            == "blocked"
        )


def test_api_overload_is_explicit_and_duplicate_still_works(api):
    from conftest import auth

    configure(backlog=1)
    command = proposal()
    body = command.model_dump(mode="json")
    assert api.post("/proposals", json=body, headers=auth("agent")).status_code == 200

    blocked = api.post("/proposals", json=proposal().model_dump(mode="json"), headers=auth("agent"))
    assert blocked.status_code == 409 and blocked.json()["error"] == "destination_overloaded"
    assert api.post("/proposals", json=body, headers=auth("agent")).status_code == 200


@pytest.mark.parametrize("change", ["consent", "owner", "approval"])
def test_routed_marketing_rechecks_current_authority(api, change):
    from conftest import customer, effects, event, post, propose, state

    configure()
    cid = customer(api, "routed-marketing")
    event(api, "crm", "routed-marketing", 2, {"owner": "healthy"})
    result = propose(api, cid, action="send_marketing")
    assert post(
        api,
        f"/proposals/{result['id']}/approve",
        {"expected_revision": state(api, cid)["revision"]},
        "approver",
    )["approved"]
    if change == "consent":
        event(api, "consent", "routed-marketing", 2, {"consent": False})
    elif change == "owner":
        event(api, "crm", "routed-marketing", 3, {"owner": "affected"})
    else:
        with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
            c.execute(
                "UPDATE cp.proposals SET approved_until=clock_timestamp()-interval '1 second' WHERE id=%s",
                (result["id"],),
            )
    assert worker.once()
    with db.connect() as c:
        assert (
            c.execute("SELECT status FROM cp.jobs WHERE id=%s", (result["id"],)).fetchone()["status"]
            == "blocked"
        )
    assert effects(result["id"]) == 0


def test_single_reader_cannot_bypass_registered_recovery_scope():
    from controlplane.operations import recovery_snapshot

    configure()
    admin = os.environ["TEST_ADMIN_DSN"]
    with pytest.raises(ValueError, match="all_destination_readers_required"):
        recovery_snapshot(admin, admin)
    with pytest.raises(ValueError, match="all_destination_readers_required"):
        recovery_snapshot(admin, {"healthy": admin})
    assert recovery_snapshot(admin, {"healthy": admin, "affected": admin, "__legacy__": admin})[
        "safe_to_resume"
    ]


def test_scoped_worker_executes_without_registry_write(monkeypatch):
    from psycopg.conninfo import make_conninfo
    from test_cloud_operations import provision_roles

    configure()
    result = service.propose("agent", proposal())
    admin = provision_roles()
    with db.connect(admin) as c:
        capacity.grants(c)
    monkeypatch.setenv(
        "CONTROL_DSN", make_conninfo(admin, user="control_worker", password="local-cloud-role-only")
    )
    assert worker.once()
    with db.connect() as c:
        assert (
            c.execute("SELECT status FROM cp.jobs WHERE id=%s", (result["id"],)).fetchone()["status"]
            == "succeeded"
        )
    for statement in [
        "UPDATE cp.destinations SET url='http://attacker.invalid'",
        "DELETE FROM cp.capacity_requests",
    ]:
        with pytest.raises(psycopg.Error), db.connect() as c:
            c.execute(statement)
