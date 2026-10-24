"""Local-only independent traffic observer around the existing transactional destination."""

import json
import os
import sqlite3
import time

from fastapi.responses import JSONResponse
from controlplane.mock import app
from fastapi import Depends
from controlplane.mock import injector


@app.post("/local-alerts", dependencies=[Depends(injector)])
async def alert(body: dict):
    if set(body) != {"destination", "state", "at"} or body["state"] not in ("firing", "cleared"):
        return JSONResponse(status_code=422, content={"error": "invalid_alert"})
    with sqlite3.connect(os.environ["RECEIVER_JOURNAL"], timeout=10) as c:
        c.execute("CREATE TABLE IF NOT EXISTS alerts(record TEXT NOT NULL)")
        c.execute("INSERT INTO alerts VALUES (?)", (json.dumps(body),))
    return {"recorded": True}


@app.middleware("http")
async def observe(request, call_next):
    if not request.url.path.startswith("/effects"):
        return await call_next(request)
    now = time.time()
    start, end = float(os.environ["FAULT_START"]), float(os.environ["FAULT_END"])
    mode = os.environ["FAULT_MODE"] if start <= now < end else "healthy"
    if mode == "mixed":
        mode = "429" if now < (start + end) / 2 else "503"
    if mode in ("429", "503"):
        response = JSONResponse(
            status_code=int(mode), content={"error": "synthetic_outage"}, headers={"Retry-After": "2"}
        )
    else:
        response = await call_next(request)
    record = {
        "at": now,
        "finished": time.time(),
        "method": request.method,
        "key": request.headers.get("idempotency-key") or request.url.path.split("/")[-1],
        "permit": request.headers.get("x-capacity-permit"),
        "mode": mode,
        "status": response.status_code,
    }
    with sqlite3.connect(os.environ["RECEIVER_JOURNAL"], timeout=10) as c:
        c.execute("CREATE TABLE IF NOT EXISTS observations(record TEXT NOT NULL)")
        c.execute("INSERT INTO observations VALUES (?)", (json.dumps(record),))
    return response
