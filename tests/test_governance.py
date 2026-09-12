from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from hypothesis import given, settings, strategies as st

from controlplane import db, worker
from controlplane.policy import STAGES, field_decision
from conftest import auth, customer, effects, event, post, propose, state


@given(st.sampled_from(STAGES), st.sampled_from(STAGES), st.integers(1, 10000))
@settings(max_examples=100)
def test_no_regress_invariant(before, incoming, sequence):
    result = field_decision("crm", "lifecycle", incoming, {"lifecycle": before}, {}, sequence)
    assert (result == "accepted") == (STAGES.index(incoming) >= STAGES.index(before))


@pytest.mark.parametrize("field,value", [("email", "other@example.test"), ("owner", "owner-b"),
                                         ("phone", "+14165550123"), ("lifecycle", "Customer"),
                                         ("consent", True), ("suppressed", False), ("score", 99)])
def test_authority_is_not_recency(field, value):
    assert field_decision("agent", field, value, {}, {}, 999999) == "source_not_authoritative"


def test_no_regress_and_stale_delivery(api, evidence):
    cid = customer(api)
    result = event(api, "crm", "one", 50, {"lifecycle": "MQL"})
    assert result["decisions"]["lifecycle"] == "lifecycle_regression"
    assert state(api, cid)["state"]["lifecycle"] == "Opportunity"
    with db.connect() as c:
        queued = c.execute("SELECT count(*) AS n FROM cp.jobs").fetchone()["n"]
    assert queued == 0
    evidence("no-regress", {"before": "Opportunity", "incoming": "MQL", "decision": result,
                             "final": state(api, cid)["state"]["lifecycle"], "queued_actions": queued})
    event(api, "crm", "one", 100, {"lifecycle": "Customer"})
    stale = event(api, "crm", "one", 99, {"lifecycle": "SQL"})
    assert stale["decisions"]["lifecycle"] == "stale_source_sequence"


def test_duplicate_and_conflicting_event(api):
    first = event(api, "crm", "one", 1, {"email": "one@example.test"})
    again = event(api, "crm", "one", 1, {"email": "one@example.test"})
    conflict = event(api, "crm", "one", 1, {"email": "evil@example.test"})
    assert again["duplicate"] is True and conflict["status"] == "conflict"
    assert state(api, first["customer_id"])["state"]["email"] == "one@example.test"


def test_concurrent_duplicate_source_creates_one_customer(api):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: event(api, "crm", "one", 1, {"email": "one@example.test"}), range(6)))
    assert len({r["customer_id"] for r in results}) == 1
    with db.connect() as c:
        assert c.execute("SELECT count(*) AS n FROM cp.customers").fetchone()["n"] == 1


def test_email_is_hint_and_ambiguous_identity_never_merges(api):
    a, b = customer(api, "a", False), customer(api, "b", False)
    assert event(api, "crm", "other", 1, {"email": "a@example.test"})["reason"] == "identity_review_required"
    event(api, "crm", "b", 2, {"email": "a@example.test"})
    ambiguous = event(api, "crm", "ambiguous", 1, {"email": "a@example.test"})
    assert ambiguous["reason"] == "identity_ambiguous"
    assert a != b
    ex = api.get("/exceptions", headers=auth("operator")).json()[-1]
    assert len(ex["detail"]["candidates"]) == 2


def test_identity_link_cannot_rebind(api):
    a, b = customer(api, "a"), customer(api, "b")
    response = api.post("/identity-links", headers=auth("operator"), json={"source": "consent", "subject": "a",
                        "customer_id": b, "expected_revision": state(api, b)["revision"], "evidence": "Attempt wrong mapping"})
    assert response.status_code == 409
    assert state(api, a)["state"]["consent"] is True


def test_lower_authority_crm_cannot_resubscribe(api):
    cid = customer(api)
    event(api, "consent", "one", 2, {"consent": False})
    result = event(api, "crm", "one", 9999, {"consent": True, "suppressed": False})
    assert set(result["decisions"].values()) == {"source_not_authoritative"}
    assert propose(api, cid, "send_marketing")["reason"] == "consent_missing_or_revoked"


def test_unknown_consent_and_suppression_fail_closed(api):
    cid = customer(api, marketable=False)
    assert propose(api, cid, "send_marketing")["decision"] == "deny"


def test_agent_denial_and_autonomy(api):
    cid = customer(api)
    assert propose(api, cid, "set_consent")["reason"] == "protected_write"
    assert propose(api, cid, "sync_profile")["decision"] == "allow"
    assert propose(api, cid, "send_marketing")["decision"] == "approval"


def test_stale_context(api):
    cid = customer(api)
    assert propose(api, cid, expected_revision=0)["reason"] == "stale_context"


def test_duplicate_proposals_and_conflicting_ids(api):
    cid = customer(api)
    request_id = str(uuid4())
    first = propose(api, cid, request_id=request_id)
    assert propose(api, cid, request_id=request_id) == first
    assert propose(api, cid, "set_consent", request_id=request_id)["status"] == "conflict"


def test_consent_revoked_after_approval(api, evidence):
    cid = customer(api)
    p = propose(api, cid, "send_marketing")
    approved = post(api, f"/proposals/{p['id']}/approve", {"expected_revision": state(api, cid)["revision"]}, "approver")
    assert approved["approved"]
    event(api, "consent", "one", 2, {"consent": False})
    worker.once()
    result = api.get(f"/proposals/{p['id']}", headers=auth("agent")).json()["job"]
    assert result["status"] == "blocked" and result["reason"] == "consent_missing_or_revoked"
    assert effects(p["id"]) == 0
    evidence("consent-race", {"proposal": p, "approval": approved, "consent_before_execution": False,
                              "execution": result["status"], "reason": result["reason"], "messages_sent": effects(p["id"])})


def test_suppression_after_approval(api):
    cid = customer(api)
    p = propose(api, cid, "send_marketing")
    post(api, f"/proposals/{p['id']}/approve", {"expected_revision": state(api, cid)["revision"]}, "approver")
    event(api, "privacy", "one", 2, {"suppressed": True})
    worker.once()
    assert effects(p["id"]) == 0


@pytest.mark.parametrize("role,route,body", [
    ("agent", "/events", {"event_id": "x", "subject": "x", "sequence": 1, "attributes": {"consent": True}}),
    ("agent", "/identity-links", {"source": "crm", "subject": "x", "customer_id": str(uuid4()),
                                  "expected_revision": 1, "evidence": "Synthetic evidence"}),
    ("agent", f"/proposals/{uuid4()}/approve", {"expected_revision": 0}),
    ("agent", "/exceptions/1/resolve", {"disposition": "requeue", "note": "Bypass controls"}),
    ("crm", "/proposals", {"request_id": str(uuid4()), "customer_id": str(uuid4()),
                            "expected_revision": 0, "action": "sync_profile", "rationale": "wrong actor"}),
])
def test_role_boundary(api, role, route, body):
    assert api.post(route, json=body, headers=auth(role)).status_code == 403


@pytest.mark.parametrize("attrs", [{"consent": "true"}, {"score": True}, {"lifecycle": "VIP"},
                                   {"email": "invalid"}, {"unrestricted": "write"}, {"phone": "123"},
                                   {}, {"consent": None}])
def test_malformed_input_is_durable_exception(api, attrs):
    response = api.post("/events", headers=auth("crm"), json={"event_id": "bad", "subject": "bad",
                                                            "sequence": 1, "attributes": attrs})
    assert response.status_code == 422
    assert response.json()["exception_id"] > 0


def test_unauthenticated_read_and_malformed_request(api):
    assert api.get("/audit").status_code == 401
    assert api.post("/events", json={"garbage": True}).status_code == 401


def test_one_authoritative_subject_per_source(api):
    cid = customer(api)
    response = api.post("/identity-links", headers=auth("operator"), json={"source": "crm", "subject": "another-crm-record",
                        "customer_id": cid, "expected_revision": state(api, cid)["revision"],
                        "evidence": "Cannot compare independent record sequence clocks"})
    assert response.status_code == 409


def test_reviewed_identity_resolution_requires_new_event(api):
    cid = customer(api, marketable=False)
    pending = event(api, "consent", "one", 1, {"consent": True})
    assert pending["status"] == "exception"
    post(api, "/identity-links", {"source": "consent", "subject": "one", "customer_id": cid,
                                  "expected_revision": state(api, cid)["revision"], "evidence": "Operator checked synthetic IDs"}, "operator")
    replay = event(api, "consent", "one", 1, {"consent": True})
    assert replay["duplicate"] and replay["status"] == "exception"
    accepted = event(api, "consent", "one", 1, {"consent": True}, event_id="reviewed-redelivery")
    assert accepted["decisions"]["consent"] == "accepted"
    result = post(api, f"/exceptions/{pending['exception_id']}/resolve",
                  {"disposition": "acknowledge", "note": "Linked and requested new delivery identity"}, "operator")
    assert result["status"] == "acknowledged"


def test_approval_rechecks_context_before_queueing(api):
    cid = customer(api)
    p = propose(api, cid, "send_marketing")
    revision = state(api, cid)["revision"]
    event(api, "crm", "one", 2, {"email": "changed@example.test"})
    result = post(api, f"/proposals/{p['id']}/approve", {"expected_revision": revision}, "approver")
    assert not result["approved"] and result["reason"] == "stale_context"
    assert worker.once() is False


def test_concurrent_authoritative_updates_preserve_highest_sequence(api):
    cid = customer(api, marketable=False)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: event(api, "crm", "one", n, {"owner": f"owner-{n}"}), range(2, 10)))
    canonical = state(api, cid)
    assert canonical["state"]["owner"] == "owner-9"
    assert canonical["provenance"]["owner"]["sequence"] == 9


def test_source_cannot_spoof_authority_in_payload(api):
    cid = customer(api)
    response = api.post("/events", headers=auth("crm"), json={"event_id": "spoof", "subject": "one", "source": "consent",
                                                            "sequence": 2, "attributes": {"consent": False}})
    assert response.status_code == 422
    assert state(api, cid)["state"]["consent"] is True
