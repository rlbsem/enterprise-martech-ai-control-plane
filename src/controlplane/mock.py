"""Independent HTTP adapter with real PostgreSQL effects and deterministic failure injection.

The idempotency row and the simulated business mutation are ONE transaction here.
An adapter that merely stores a key before calling a non-idempotent third party is not equivalent.
"""

import os
import secrets
import time
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from . import db
from .contracts import FailurePlan, Mutation
from .policy import digest

app = FastAPI(title="Synthetic downstream (no SaaS connection)")


def executor(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, "Bearer " + os.environ["EXECUTOR_TOKEN"]):
        raise HTTPException(401, "executor_credential_required")


def injector(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, "Bearer " + os.environ["INJECTOR_TOKEN"]):
        raise HTTPException(401, "injector_credential_required")


@app.get("/health")
def health():
    with db.connect(os.environ["REMOTE_DSN"]) as c:
        c.execute("SELECT 1 FROM remote.effects LIMIT 1")
    return {"status": "ready"}


@app.post("/failure-plans", dependencies=[Depends(injector)])
def plan(command: FailurePlan):
    with db.connect(os.environ["REMOTE_DSN"]) as c:
        c.execute("INSERT INTO remote.plans VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET modes=excluded.modes",
                  (command.key, Jsonb(command.modes)))
    return {"configured": True}


def receipt(row):
    return {"key": str(row["key"]), "receipt": str(row["receipt"]), "hash": row["hash"],
            "committed_at": row["committed_at"].isoformat()}


@app.get("/effects/{key}", dependencies=[Depends(executor)])
def get_effect(key: UUID):
    with db.connect(os.environ["REMOTE_DSN"]) as c:
        row = c.execute("SELECT * FROM remote.effects WHERE key=%s", (key,)).fetchone()
        if not row:
            raise HTTPException(404, "not_committed")
        return receipt(row)


@app.post("/effects", dependencies=[Depends(executor)])
def mutate(command: Mutation, idempotency_key: UUID = Header()):
    body = command.model_dump(mode="json")
    with db.connect(os.environ["REMOTE_DSN"]) as c:
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(idempotency_key),))
        previous = c.execute("SELECT * FROM remote.effects WHERE key=%s", (idempotency_key,)).fetchone()
        if previous:
            if previous["hash"] != digest(body):
                raise HTTPException(409, "idempotency_payload_conflict")
            return receipt(previous)
        planned = c.execute("SELECT modes FROM remote.plans WHERE key=%s FOR UPDATE", (idempotency_key,)).fetchone()
        modes = planned["modes"] if planned else []
        mode = modes[0] if modes else "ok"
        if modes:
            c.execute("UPDATE remote.plans SET modes=%s WHERE key=%s", (Jsonb(modes[1:]), idempotency_key))
        if mode not in ("429", "500", "422", "timeout_before"):
            row = c.execute("INSERT INTO remote.effects(key,hash,body,receipt) VALUES (%s,%s,%s,%s) RETURNING *",
                            (idempotency_key, digest(body), Jsonb(body), uuid4())).fetchone()
    # Simulate transport latency OUTSIDE the committed mutation transaction.
    if mode.startswith("timeout"):
        time.sleep(1.0)
    if mode in ("429", "500", "422"):
        return JSONResponse(status_code=int(mode), content={"error": "injected"}, headers={"Retry-After": "1"})
    if mode == "timeout_before":
        return JSONResponse(status_code=503, content={"error": "not_committed"})
    return JSONResponse(status_code=201, content=receipt(row))
