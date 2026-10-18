"""One-off SQL fixture tasks. These do not claim HTTP/worker delivery acceptance."""

import json
import os
import sys
from uuid import UUID, NAMESPACE_URL, uuid5

import boto3
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from . import cloud, db, operations, service
from .contracts import Proposal, SourceEvent
from .policy import digest


def control_fixture(run):
    """Admit through existing governance, then explicitly simulate durable dispatch intent."""
    result = service.ingest(
        "crm",
        SourceEvent(
            event_id=run + "-event",
            subject=run,
            sequence=1,
            attributes={"email": run + "@example.test", "lifecycle": "Opportunity"},
        ),
    )
    key = uuid5(NAMESPACE_URL, "native-pitr/" + run)
    command = Proposal(
        request_id=key,
        customer_id=UUID(result["customer_id"]),
        expected_revision=result["revision"],
        action="sync_profile",
        rationale="Synthetic native SQL/PITR fixture, not an HTTP dispatch",
    )
    proposal = service.propose("agent", command)
    if proposal.get("decision") != "allow":
        raise ValueError("Fixture admission denied")
    with db.connect() as c:
        row = db.customer(c, command.customer_id)
        mutation = {
            "customer_id": str(row["id"]),
            "action": "sync_profile",
            "revision": row["revision"],
            "payload": dict(row["state"]),
        }
        job = c.execute("SELECT mutation,status FROM cp.jobs WHERE id=%s FOR UPDATE", (key,)).fetchone()
        if not job or (job["mutation"] is not None and job["mutation"] != mutation):
            raise ValueError("Existing fixture key has different content")
        c.execute(
            "UPDATE cp.jobs SET mutation=%s,uncertain=true,status='retry' WHERE id=%s", (Jsonb(mutation), key)
        )
        db.audit(c, "native-fixture", "synthetic_sql_intent", key, {"hash": digest(mutation)})
    return {
        "key": str(key),
        "mutation": mutation,
        "hash": digest(mutation),
        "scope": "synthetic_SQL_intent_not_worker_dispatch",
    }


def receipt_fixture(document, remote_dsn):
    if (
        set(document) != {"key", "mutation", "hash", "scope"}
        or document["scope"] != "synthetic_SQL_intent_not_worker_dispatch"
        or document["mutation"]["action"] != "sync_profile"
        or digest(document["mutation"]) != document["hash"]
        or not document["mutation"]["payload"]["email"].endswith("@example.test")
    ):
        raise ValueError("Exact synthetic fixture required")
    key = UUID(document["key"])
    receipt = uuid5(key, "native-independent-receipt")
    with db.connect(remote_dsn) as c:
        c.execute(
            "INSERT INTO remote.effects(key,hash,body,receipt) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT(key) DO NOTHING",
            (key, document["hash"], Jsonb(document["mutation"]), receipt),
        )
        actual = c.execute("SELECT hash,body,receipt FROM remote.effects WHERE key=%s", (key,)).fetchone()
        if actual != {"hash": document["hash"], "body": document["mutation"], "receipt": receipt}:
            raise ValueError("Receipt identity/content conflict")
        count = c.execute("SELECT count(*) AS n FROM remote.effects").fetchone()["n"]
    return {"key": str(key), "receipt": str(receipt), "hash": document["hash"], "effects": count}


def permission_denials(control_dsn, remote_dsn):
    denied = 0
    for dsn, query in [
        (control_dsn, "UPDATE cp.jobs SET attempts=attempts WHERE false"),
        (remote_dsn, "DELETE FROM remote.effects WHERE false"),
    ]:
        try:
            with db.connect(dsn) as c:
                c.execute(query)
        except psycopg.errors.InsufficientPrivilege:
            denied += 1
    if denied != 2:
        raise ValueError("Verifier effective write denial not established")
    return denied


def main():
    action = sys.argv[1]
    run = os.environ["ACCEPTANCE_RUN"]
    import re

    if not re.fullmatch("[a-z0-9]{8,16}", run):
        raise ValueError("Pinned synthetic run required")
    client = boto3.client("secretsmanager", region_name="us-east-2")
    if action == "bootstrap":
        cloud.bootstrap(client, database_name="csaacceptance")
        result = {"bootstrap": "complete"}
    elif action in ("baseline", "control"):
        cloud.configure("api", client)
        if action == "baseline":
            with db.connect() as c:
                result = {
                    "baseline_at": c.execute("SELECT clock_timestamp() AS at").fetchone()["at"].isoformat()
                }
        else:
            result = control_fixture(run)
    elif action == "receipt":
        cloud.configure("remote", client)
        result = receipt_fixture(json.loads(os.environ["FIXTURE_JSON"]), os.environ["REMOTE_DSN"])
    elif action == "verify":
        cloud.configure("verifier", client)
        host = os.environ.get("RESTORED_HOST")
        control = make_conninfo(os.environ["CONTROL_DSN"], host=host) if host else os.environ["CONTROL_DSN"]
        result = operations.recovery_snapshot(control, os.environ["REMOTE_DSN"])
        result["write_denials"] = permission_denials(control, os.environ["REMOTE_DSN"])
    else:
        raise ValueError("Unknown native task")
    print(json.dumps({"native_fixture": result}, default=str), flush=True)
    return 2 if action == "verify" and not result["safe_to_resume"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit("Native task failed closed; inspect private configuration") from None
