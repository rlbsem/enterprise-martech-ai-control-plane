"""Governed command handling. Every decision and durable work transition commits together."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db
from .policy import POLICY, POLICY_HASH, action_decision, digest, field_decision


def ingest(source, event):
    body = event.model_dump(mode="json", exclude_none=True)
    payload_hash = digest(body)
    with db.connect() as c:
        # Bounded demo: serialize identity discovery as well as event deduplication.
        # A row lock cannot protect an identity that does not exist yet.
        c.execute("SELECT pg_advisory_xact_lock(83191)")
        previous = c.execute("SELECT * FROM cp.events WHERE source=%s AND event_id=%s",
                             (source, event.event_id)).fetchone()
        if previous:
            if previous["hash"] != payload_hash:
                eid = db.exception(c, "idempotency_conflict", event.event_id,
                                   {"source": source, "original_hash": previous["hash"], "incoming_hash": payload_hash})
                db.audit(c, source, "source_conflict", event.event_id, {"exception_id": eid})
                return {"status": "conflict", "exception_id": eid}
            db.audit(c, source, "source_duplicate", event.event_id, {"hash": payload_hash})
            return {**previous["result"], "duplicate": True}
        identity = c.execute("SELECT customer_id FROM cp.identities WHERE source=%s AND subject=%s",
                             (source, event.subject)).fetchone()
        attrs = body["attributes"]
        if not identity:
            candidates = c.execute("SELECT id FROM cp.customers WHERE state->>'email'=%s",
                                   (attrs.get("email"),)).fetchall() if attrs.get("email") else []
            # Email is a discovery hint, never proof that records belong to one person.
            if source != "crm" or candidates or not attrs.get("email"):
                reason = "identity_ambiguous" if len(candidates) > 1 else "identity_review_required"
                eid = db.exception(c, reason, event.subject,
                                   {"source": source, "event_id": event.event_id,
                                    "candidates": [str(r["id"]) for r in candidates]})
                result = {"status": "exception", "reason": reason, "exception_id": eid}
            else:
                identity = {"customer_id": uuid4()}
                c.execute("INSERT INTO cp.customers(id) VALUES (%s)", (identity["customer_id"],))
                c.execute("INSERT INTO cp.identities VALUES (%s,%s,%s)",
                          (source, event.subject, identity["customer_id"]))
        if identity:
            row = db.customer(c, identity["customer_id"])
            before = dict(row["state"])
            state, provenance, decisions = dict(before), dict(row["provenance"]), {}
            for field, value in attrs.items():
                reason = field_decision(source, field, value, state, provenance, event.sequence)
                decisions[field] = reason
                if reason == "accepted":
                    state[field] = value
                    provenance[field] = {"source": source, "subject": event.subject,
                                         "event_id": event.event_id, "sequence": event.sequence}
            # Accepted newer observations invalidate old context even when the value is equal.
            revision = row["revision"] + int(provenance != row["provenance"])
            c.execute("UPDATE cp.customers SET state=%s,provenance=%s,revision=%s WHERE id=%s",
                      (Jsonb(state), Jsonb(provenance), revision, row["id"]))
            result = {"status": "processed", "customer_id": str(row["id"]), "revision": revision,
                      "decisions": decisions}
            if any(v != "accepted" for v in decisions.values()):
                result["exception_id"] = db.exception(c, "field_governance", row["id"],
                                                       {"source": source, "event_id": event.event_id,
                                                        "decisions": decisions})
            db.audit(c, source, "source_decision", row["id"],
                     {"before": before, "after": state, "incoming": attrs, "event_id": event.event_id,
                      "provenance": provenance, "decisions": decisions, "revision": revision})
        else:
            db.audit(c, source, "identity_exception", event.subject, result)
        c.execute("INSERT INTO cp.events(source,event_id,hash,body,result) VALUES (%s,%s,%s,%s,%s)",
                  (source, event.event_id, payload_hash, Jsonb(body), Jsonb(result)))
        return result


def link(actor, command):
    with db.connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(83191)")
        row = db.customer(c, command.customer_id)
        if row["revision"] != command.expected_revision:
            raise ValueError("stale_context")
        existing = c.execute("SELECT customer_id FROM cp.identities WHERE source=%s AND subject=%s",
                             (command.source, command.subject)).fetchone()
        if existing and existing["customer_id"] != command.customer_id:
            raise ValueError("identity_rebinding_forbidden")
        other = c.execute("SELECT subject FROM cp.identities WHERE source=%s AND customer_id=%s",
                          (command.source, command.customer_id)).fetchone()
        if other and other["subject"] != command.subject:
            raise ValueError("multiple_authoritative_records_require_reviewed_migration")
        c.execute("INSERT INTO cp.identities VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                  (command.source, command.subject, command.customer_id))
        db.audit(c, actor, "identity_link", command.customer_id, command.model_dump(mode="json"))
        return {"linked": True}


def propose(actor, command):
    body = command.model_dump(mode="json")
    with db.connect() as c:
        row = db.customer(c, command.customer_id)
        # Request IDs are global; prevent two customers from racing on a reused ID.
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(command.request_id),))
        previous = c.execute("SELECT * FROM cp.proposals WHERE id=%s", (command.request_id,)).fetchone()
        if previous:
            if previous["hash"] != digest(body) or previous["actor"] != actor:
                db.exception(c, "proposal_idempotency_conflict", command.request_id, {"actor": actor})
                db.audit(c, actor, "proposal_conflict", command.request_id, {})
                return {"status": "conflict"}
            return {"id": str(previous["id"]), "decision": previous["decision"], "reason": previous["reason"]}
        decision, reason = action_decision(command.action, row["state"], row["revision"], command.expected_revision)
        c.execute("""INSERT INTO cp.proposals(id,customer_id,actor,hash,body,policy_hash,revision,decision,reason,expires_at)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+%s * interval '1 second')""",
                  (command.request_id, command.customer_id, actor, digest(body), Jsonb(body), POLICY_HASH,
                   command.expected_revision, decision, reason, POLICY["proposal_ttl_seconds"]))
        if decision == "allow":
            c.execute("INSERT INTO cp.jobs(id,status) VALUES (%s,'ready')", (command.request_id,))
        elif decision == "deny":
            db.exception(c, "proposal_denied", command.request_id, {"reason": reason})
        db.audit(c, actor, "proposal", command.request_id,
                 {"body": body, "state": row["state"], "provenance": row["provenance"],
                  "decision": decision, "reason": reason, "hash": digest(body)})
        return {"id": str(command.request_id), "decision": decision, "reason": reason}


def valid_proposal(proposal, row, now=None, require_approval=True):
    now = now or datetime.now(UTC)
    if proposal["policy_hash"] != POLICY_HASH:
        return "policy_changed"
    if proposal["expires_at"] <= now:
        return "proposal_expired"
    decision, reason = action_decision(proposal["body"]["action"], row["state"], row["revision"], proposal["revision"])
    if decision == "deny":
        return reason
    if decision == "approval" and require_approval:
        if not proposal["approved_by"]:
            return "approval_missing"
        if proposal["approved_until"] <= now:
            return "approval_expired"
    return None


def approve(actor, proposal_id, command):
    with db.connect() as c:
        # Lock order is customer -> proposal. Workers also start with the customer.
        p = c.execute("SELECT * FROM cp.proposals WHERE id=%s", (proposal_id,)).fetchone()
        if not p:
            raise LookupError("proposal_not_found")
        row = db.customer(c, p["customer_id"])
        p = c.execute("SELECT * FROM cp.proposals WHERE id=%s FOR UPDATE", (proposal_id,)).fetchone()
        reason = valid_proposal(p, row, require_approval=False)
        if p["decision"] != "approval":
            reason = "not_approvable"
        if command.expected_revision != p["revision"]:
            reason = "approval_revision_mismatch"
        if actor == p["actor"]:
            reason = "self_approval_forbidden"
        if reason:
            db.audit(c, actor, "approval_denied", proposal_id, {"reason": reason})
            db.exception(c, "approval_denied", proposal_id, {"reason": reason})
            return {"approved": False, "reason": reason}
        if p["approved_by"]:
            # Duplicates cannot refresh an expired approval.
            return {"approved": p["approved_until"] > datetime.now(UTC), "duplicate": True}
        until = min(p["expires_at"], datetime.now(UTC) + timedelta(seconds=POLICY["approval_ttl_seconds"]))
        c.execute("UPDATE cp.proposals SET approved_by=%s,approved_until=%s WHERE id=%s",
                  (actor, until, proposal_id))
        c.execute("INSERT INTO cp.jobs(id,status) VALUES (%s,'ready') ON CONFLICT DO NOTHING", (proposal_id,))
        db.audit(c, actor, "approval", proposal_id,
                 {"hash": p["hash"], "revision": p["revision"], "until": until.isoformat()})
        return {"approved": True}


def resolve(actor, exception_id, command):
    with db.connect() as c:
        ex = c.execute("SELECT * FROM cp.exceptions WHERE id=%s FOR UPDATE", (exception_id,)).fetchone()
        if not ex:
            raise LookupError("exception_not_found")
        if ex["status"] != "open":
            raise ValueError("exception_already_resolved")
        if command.disposition == "requeue":
            if ex["category"] not in ("retry_exhausted", "remote_rejected"):
                raise ValueError("requires_new_source_event_or_proposal")
            job = c.execute("SELECT * FROM cp.jobs WHERE id=%s FOR UPDATE", (ex["subject"],)).fetchone()
            if not job or job["status"] != "dead":
                raise ValueError("job_not_dead")
            # Preserve payload and idempotency key. New attempt budget, not new permission.
            c.execute("UPDATE cp.jobs SET status='retry',attempts=0,available_at=clock_timestamp() WHERE id=%s",
                      (ex["subject"],))
        status = "requeued" if command.disposition == "requeue" else "acknowledged"
        c.execute("UPDATE cp.exceptions SET status=%s WHERE id=%s", (status, exception_id))
        db.audit(c, actor, "exception_resolution", exception_id, command.model_dump())
        return {"status": status}
