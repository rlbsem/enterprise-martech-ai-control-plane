"""Deterministic simulator/harness. API and downstream must be running; no paid accounts."""

import json
import os
from uuid import uuid4

import httpx

from controlplane import worker


def main():
    tokens = json.loads(os.environ["API_TOKENS"])
    os.environ["REMOTE_TIMEOUT"] = "0.15"
    with httpx.Client(base_url=os.environ["CONTROL_URL"], timeout=10, trust_env=False) as api:
        def call(method, path, role, body=None):
            r = api.request(method, path, headers={"Authorization": "Bearer " + tokens[role]}, json=body)
            r.raise_for_status()
            return r.json()

        name = "demo-" + uuid4().hex[:12]

        def event(source, seq, attrs):
            return call("POST", "/events", source, {"event_id": f"{name}-{seq}", "subject": name,
                                                     "sequence": seq, "attributes": attrs})

        cid = event("crm", 1, {"email": name + "@example.test", "lifecycle": "Opportunity"})["customer_id"]

        def state():
            return call("GET", f"/customers/{cid}", "agent")

        def propose(action):
            return call("POST", "/proposals", "agent", {"request_id": str(uuid4()), "customer_id": cid,
                        "expected_revision": state()["revision"], "action": action,
                        "rationale": "Synthetic simulator recommends a customer follow-up"})

        for source, attrs in [("consent", {"consent": True}), ("privacy", {"suppressed": False})]:
            call("POST", "/identity-links", "operator", {"source": source, "subject": name, "customer_id": cid,
                 "expected_revision": state()["revision"], "evidence": "Synthetic operator reviewed both account references"})
            event(source, 1, attrs)
        no_regress = event("crm", 2, {"lifecycle": "MQL"})
        assert no_regress["decisions"]["lifecycle"] == "lifecycle_regression"
        assert state()["state"]["lifecycle"] == "Opportunity"
        print("NO-REGRESS: Opportunity -> MQL refused; Opportunity retained.")
        assert propose("set_consent")["decision"] == "deny"
        p = propose("send_marketing")
        assert call("POST", f"/proposals/{p['id']}/approve", "approver",
                    {"expected_revision": state()["revision"]})["approved"]
        event("consent", 2, {"consent": False})
        worker.once()
        result = call("GET", f"/proposals/{p['id']}", "operator")["job"]
        assert result["status"] == "blocked" and result["reason"] == "consent_missing_or_revoked"
        print("CONSENT RACE: Approved, then revoked; execution blocked.")
        p = propose("sync_profile")
        r = httpx.post(os.environ["DOWNSTREAM_URL"] + "/failure-plans", trust_env=False,
                       headers={"Authorization": "Bearer " + os.environ["INJECTOR_TOKEN"]},
                       json={"key": p["id"], "modes": ["timeout_after"]})
        r.raise_for_status()
        worker.once()
        result = call("GET", f"/proposals/{p['id']}", "operator")["job"]
        assert result["status"] == "retry"
        # Respect durable backoff in the demo. The test suite separately controls database time.
        import time
        for _ in range(20):
            time.sleep(0.5)
            worker.once()
            result = call("GET", f"/proposals/{p['id']}", "operator")["job"]
            if result["status"] == "succeeded":
                break
        assert result["status"] == "succeeded" and result["attempts"] == 2
        assert result["reason"] == "reconciled_committed_effect"
        print("UNKNOWN RESULT: 2 worker attempts; committed effect reconciled under one stable key.")
        print(json.dumps({"customer_id": cid, "recovered_job": p["id"], "receipt": result["remote_result"]}, indent=2))


if __name__ == "__main__":
    main()
