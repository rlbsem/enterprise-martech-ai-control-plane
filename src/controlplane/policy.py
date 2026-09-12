"""Versioned, deterministic policy; pure functions also support invariant tests."""

import hashlib
import json

STAGES = ["Lead", "MQL", "SQL", "Opportunity", "Customer"]
POLICY = {
    "version": "customer-control/1",
    "owners": {"email": "crm", "phone": "crm", "owner": "crm", "lifecycle": "crm",
               "score": "scoring", "consent": "consent", "suppressed": "privacy"},
    "stages": STAGES,
    "proposal_ttl_seconds": 900,
    "approval_ttl_seconds": 300,
    "max_attempts": 4,
    "actions": {"sync_profile": "allow", "send_marketing": "approval", "set_consent": "deny"},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


POLICY_HASH = digest(POLICY)


def field_decision(source, field, value, state, provenance, sequence):
    if POLICY["owners"][field] != source:
        return "source_not_authoritative"
    previous = provenance.get(field)
    if previous and sequence <= previous["sequence"]:
        return "stale_source_sequence"
    if field == "lifecycle" and STAGES.index(value) < STAGES.index(state.get(field, "Lead")):
        return "lifecycle_regression"
    return "accepted"


def action_decision(action, state, revision, expected_revision):
    if action == "set_consent":
        return "deny", "protected_write"
    if action == "send_marketing":
        if state.get("suppressed", True):
            return "deny", "suppressed_or_unknown"
        if state.get("consent") is not True:
            return "deny", "consent_missing_or_revoked"
        if not state.get("email"):
            return "deny", "missing_email"
    if revision != expected_revision:
        return "deny", "stale_context"
    return POLICY["actions"][action], "policy_allowed"
