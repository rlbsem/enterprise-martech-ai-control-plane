"""Leased PostgreSQL outbox, stable remote idempotency, and uncertain-outcome reconciliation."""

import os
import random
import time
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

from . import db
from .policy import POLICY, digest
from .service import valid_proposal


def claim(lease_seconds=30):
    with db.connect() as c:
        job = c.execute("""SELECT * FROM cp.jobs WHERE
          (status IN ('ready','retry') AND available_at<=clock_timestamp()) OR
          (status='leased' AND lease_until<=clock_timestamp())
          ORDER BY available_at,id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if not job:
            return None
        token = uuid4()
        row = c.execute("""UPDATE cp.jobs SET status='leased',lease_token=%s,
            lease_until=clock_timestamp()+%s * interval '1 second',attempts=attempts+1
            WHERE id=%s RETURNING *""", (token, lease_seconds, job["id"])).fetchone()
        db.audit(c, "executor", "claim", job["id"], {"attempt": row["attempts"], "recovered": job["status"] == "leased"})
        return row


def finish(job, status, reason, remote_result=None, uncertain=None, delay=None):
    with db.connect() as c:
        current = c.execute("SELECT * FROM cp.jobs WHERE id=%s FOR UPDATE", (job["id"],)).fetchone()
        if current["lease_token"] != job["lease_token"] or current["status"] != "leased":
            return False  # Fenced-out workers cannot overwrite the newer worker's result.
        if status == "retry" and current["attempts"] >= POLICY["max_attempts"]:
            status, reason = "dead", "retry_exhausted"
        backoff = delay if delay is not None else min(60, 2 ** current["attempts"]) + random.uniform(0, 1)
        c.execute("""UPDATE cp.jobs SET status=%s,reason=%s,remote_result=%s,lease_token=NULL,lease_until=NULL,
          uncertain=%s,available_at=clock_timestamp()+%s * interval '1 second' WHERE id=%s""",
                  (status, reason, Jsonb(remote_result) if remote_result else None,
                   current["uncertain"] if uncertain is None else uncertain, backoff, job["id"]))
        if status in ("dead", "blocked"):
            category = reason if reason in ("retry_exhausted", "remote_rejected") else "execution_blocked"
            db.exception(c, category, job["id"], {"reason": reason, "uncertain": current["uncertain"]})
        db.audit(c, "executor", "execution_result", job["id"],
                 {"status": status, "reason": reason, "attempt": current["attempts"], "remote": remote_result})
        return True


def prepare(job):
    with db.connect() as c:
        p = c.execute("SELECT * FROM cp.proposals WHERE id=%s", (job["id"],)).fetchone()
        row = db.customer(c, p["customer_id"])
        current = c.execute("SELECT * FROM cp.jobs WHERE id=%s FOR UPDATE", (job["id"],)).fetchone()
        if current["lease_token"] != job["lease_token"] or current["status"] != "leased":
            return None, "lease_lost"
        reason = valid_proposal(p, row)
        if reason:
            return None, reason
        action = p["body"]["action"]
        payload = ({"email": row["state"]["email"], "template": p["body"]["template"]}
                   if action == "send_marketing" else
                   {k: v for k, v in row["state"].items() if k in ("email", "phone", "owner", "lifecycle", "score")})
        mutation = {"customer_id": str(row["id"]), "action": action, "revision": row["revision"], "payload": payload}
        if current["mutation"] and current["mutation"] != mutation:
            return None, "mutation_changed"
        # Commit intent BEFORE networking. A killed worker leaves a reconcilable record.
        c.execute("UPDATE cp.jobs SET mutation=%s,uncertain=true WHERE id=%s", (Jsonb(mutation), job["id"]))
        db.audit(c, "executor", "dispatch_authorized", job["id"],
                 {"revision": row["revision"], "mutation_hash": digest(mutation), "attempt": current["attempts"]})
        return mutation, None


def execute(job):
    url = os.environ["DOWNSTREAM_URL"].rstrip("/")
    token = os.environ["EXECUTOR_TOKEN"]
    timeout = float(os.environ.get("REMOTE_TIMEOUT", "2"))
    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout,
                      trust_env=False) as client:
        try:
            if job["uncertain"]:
                response = client.get(f"/effects/{job['id']}")
                if response.status_code == 200:
                    result = response.json()
                    if not job["mutation"] or result.get("hash") != digest(job["mutation"]):
                        finish(job, "dead", "remote_identity_conflict")
                    else:
                        # This reports an existing fact, not permission for a new action.
                        finish(job, "succeeded", "reconciled_committed_effect", result, uncertain=False)
                    return
                if response.status_code != 404:
                    finish(job, "retry", "reconciliation_unavailable")
                    return
            mutation, reason = prepare(job)
            if reason:
                if reason != "lease_lost":
                    finish(job, "blocked", reason)
                return
            response = client.post("/effects", headers={"Idempotency-Key": str(job["id"])}, json=mutation)
            if response.status_code in (200, 201):
                result = response.json()
                if result.get("hash") != digest(mutation):
                    finish(job, "dead", "remote_identity_conflict")
                else:
                    finish(job, "succeeded", "remote_committed", result, uncertain=False)
            elif response.status_code == 429:
                try:
                    delay = max(1, min(300, int(response.headers.get("Retry-After", "5"))))
                except ValueError:
                    delay = 5
                finish(job, "retry", "rate_limited", uncertain=False, delay=delay)
            elif response.status_code in (400, 401, 403, 404, 409, 422):
                finish(job, "dead", "remote_rejected", uncertain=False)
            else:
                finish(job, "retry", "remote_server_error")
        except (httpx.TransportError, ValueError, KeyError):
            # Do not persist raw exceptions: URLs, proxy configuration and headers can contain credentials.
            finish(job, "retry", "remote_outcome_unknown")


def once():
    job = claim()
    if job:
        execute(job)
    return job is not None


def run():
    while True:
        if not once():
            time.sleep(0.5)
