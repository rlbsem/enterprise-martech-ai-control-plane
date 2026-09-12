"""Role-separated API. Bearer tokens are explicit local configuration, not production identity."""

import json
import os
import secrets
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import db, service
from .contracts import Approval, Link, Proposal, Resolution, SourceEvent


def principal(authorization: str = Header(default="")):
    configured = json.loads(os.environ["API_TOKENS"])
    supplied = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    for actor, token in configured.items():
        if token and secrets.compare_digest(supplied, token):
            return actor
    raise HTTPException(401, "authentication_required")


def require(*roles):
    def check(actor=Depends(principal)):
        if actor not in roles:
            raise HTTPException(403, "role_not_allowed")
        return actor
    return check


@asynccontextmanager
async def lifespan(app):
    tokens = json.loads(os.environ["API_TOKENS"])
    expected = {"crm", "consent", "scoring", "privacy", "agent", "approver", "operator"}
    if (set(tokens) != expected or any(not isinstance(v, str) or len(v) < 12 for v in tokens.values())
            or len(set(tokens.values())) != len(tokens)):
        raise RuntimeError("Configure distinct role credentials of at least 12 characters for every API role")
    yield


app = FastAPI(title="Synthetic customer control plane", version="1.0.0", lifespan=lifespan)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse(status_code=409, content={"error": str(exc)})


@app.exception_handler(LookupError)
async def missing(request, exc):
    return JSONResponse(status_code=404, content={"error": str(exc)})


@app.exception_handler(RequestValidationError)
async def invalid(request: Request, exc):
    # Persist only locations/types, not arbitrary rejected input or authentication headers.
    try:
        actor = principal(request.headers.get("authorization", ""))
    except HTTPException:
        return JSONResponse(status_code=401, content={"error": "authentication_required"})
    errors = [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
    with db.connect() as c:
        eid = db.exception(c, "invalid_contract", request.url.path, {"errors": errors})
        db.audit(c, actor, "invalid_contract", request.url.path, {"exception_id": eid, "errors": errors})
    return JSONResponse(status_code=422, content={"error": "invalid_contract", "exception_id": eid})


@app.get("/health")
def health():
    with db.connect() as c:
        c.execute("SELECT 1 FROM cp.policies LIMIT 1")
    return {"status": "ready"}


@app.post("/events")
def ingest(event: SourceEvent, actor=Depends(require("crm", "consent", "scoring", "privacy"))):
    return service.ingest(actor, event)


@app.post("/identity-links")
def link(command: Link, actor=Depends(require("operator"))):
    return service.link(actor, command)


@app.get("/customers/{customer_id}")
def customer(customer_id: UUID, actor=Depends(require("agent", "operator", "approver"))):
    with db.connect() as c:
        return db.customer(c, customer_id, lock=False)


@app.post("/proposals")
def propose(command: Proposal, actor=Depends(require("agent"))):
    return service.propose(actor, command)


@app.post("/proposals/{proposal_id}/approve")
def approve(proposal_id: UUID, command: Approval, actor=Depends(require("approver"))):
    return service.approve(actor, proposal_id, command)


@app.get("/proposals/{proposal_id}")
def proposal(proposal_id: UUID, actor=Depends(require("agent", "operator", "approver"))):
    with db.connect() as c:
        p = c.execute("SELECT * FROM cp.proposals WHERE id=%s", (proposal_id,)).fetchone()
        if not p:
            raise LookupError("proposal_not_found")
        p["job"] = c.execute("SELECT * FROM cp.jobs WHERE id=%s", (proposal_id,)).fetchone()
        return p


@app.get("/exceptions")
def exceptions(after: int = 0, actor=Depends(require("operator"))):
    with db.connect() as c:
        return c.execute("SELECT * FROM cp.exceptions WHERE id>%s ORDER BY id LIMIT 100", (after,)).fetchall()


@app.post("/exceptions/{exception_id}/resolve")
def resolve(exception_id: int, command: Resolution, actor=Depends(require("operator"))):
    return service.resolve(actor, exception_id, command)


@app.get("/audit")
def audit(after: int = 0, actor=Depends(require("operator"))):
    with db.connect() as c:
        return c.execute("SELECT * FROM cp.audit WHERE id>%s ORDER BY id LIMIT 100", (after,)).fetchall()
