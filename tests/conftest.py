"""Real PostgreSQL required. Never silently skip integration tests or substitute SQLite."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from controlplane import db

TOKENS = {r: f"synthetic-test-{r}" for r in ["crm", "consent", "privacy", "scoring", "agent", "approver", "operator"]}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session", autouse=True)
def runtime(tmp_path_factory):
    admin = os.environ["TEST_ADMIN_DSN"]
    info = conninfo_to_dict(admin)
    if not info.get("dbname", "").endswith("_test"):
        raise RuntimeError("TEST_ADMIN_DSN database must end in _test: tests destroy its schemas")
    os.environ["CONTROL_ADMIN_DSN"] = admin
    os.environ["APP_DB_PASSWORD"] = "local-app-only"
    os.environ["REMOTE_DB_PASSWORD"] = "local-remote-only"
    os.environ["CONTROL_DSN"] = make_conninfo(admin, user="control_app", password="local-app-only")
    os.environ["REMOTE_DSN"] = make_conninfo(admin, user="synthetic_remote", password="local-remote-only")
    os.environ["API_TOKENS"] = json.dumps(TOKENS)
    os.environ["EXECUTOR_TOKEN"] = "synthetic-executor-only"
    os.environ["INJECTOR_TOKEN"] = "synthetic-injector-only"
    # Keep ordinary CI round trips out of the timeout fault path; injected delays are 1 second.
    os.environ["REMOTE_TIMEOUT"] = "0.5"
    with psycopg.connect(admin, autocommit=True) as c:
        for role, password in [("control_app", "local-app-only"), ("synthetic_remote", "local-remote-only")]:
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
    with db.connect(admin) as c:
        c.execute("DROP SCHEMA IF EXISTS cp CASCADE; DROP SCHEMA IF EXISTS remote CASCADE; "
                  "DROP TABLE IF EXISTS public.schema_migrations")
    db.migrate(admin)
    processes, logs = [], []
    folder = tmp_path_factory.mktemp("servers")
    try:
        for service, variable in [("mock", "DOWNSTREAM_URL"), ("api", "CONTROL_URL")]:
            port = free_port()
            os.environ[variable] = f"http://127.0.0.1:{port}"
            log = (folder / f"{service}.log").open("w")
            logs.append(log)
            env = os.environ.copy()
            if service == "api":
                for key in ["EXECUTOR_TOKEN", "INJECTOR_TOKEN", "REMOTE_DSN", "CONTROL_ADMIN_DSN", "TEST_ADMIN_DSN"]:
                    env.pop(key, None)
            else:
                for key in ["CONTROL_DSN", "API_TOKENS", "CONTROL_ADMIN_DSN", "TEST_ADMIN_DSN"]:
                    env.pop(key, None)
            p = subprocess.Popen([sys.executable, "-m", "uvicorn", f"controlplane.{service}:app", "--host", "127.0.0.1",
                                  "--port", str(port), "--no-access-log"], stdout=log, stderr=log, env=env,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes.append(p)
            for _ in range(100):
                try:
                    if httpx.get(os.environ[variable] + "/health", trust_env=False).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if p.poll() is not None:
                    raise RuntimeError((folder / f"{service}.log").read_text())
                time.sleep(0.05)
            else:
                raise RuntimeError("HTTP service did not start")
        yield
    finally:
        for p in processes:
            p.terminate()
            p.wait(timeout=10)
        for log in logs:
            log.close()


@pytest.fixture(autouse=True)
def clean_database(runtime):
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("DROP SCHEMA cp CASCADE; DROP SCHEMA remote CASCADE; DELETE FROM public.schema_migrations")
    db.migrate(os.environ["TEST_ADMIN_DSN"])


@pytest.fixture
def api():
    with httpx.Client(base_url=os.environ["CONTROL_URL"], timeout=10, trust_env=False) as c:
        yield c


def auth(role):
    return {"Authorization": "Bearer " + TOKENS[role]}


def post(api, route, body, role="agent"):
    response = api.post(route, json=body, headers=auth(role))
    assert response.status_code == 200, response.text
    return response.json()


def state(api, cid):
    response = api.get(f"/customers/{cid}", headers=auth("agent"))
    assert response.status_code == 200, response.text
    return response.json()


def event(api, source, subject, sequence, attrs, event_id=None):
    return post(api, "/events", {"event_id": event_id or f"{subject}-{sequence}", "subject": subject,
                               "sequence": sequence, "attributes": attrs}, source)


def customer(api, name="one", marketable=True):
    created = event(api, "crm", name, 1, {"email": name + "@example.test", "lifecycle": "Opportunity"})
    cid = created["customer_id"]
    if marketable:
        for source, attrs in [("consent", {"consent": True}), ("privacy", {"suppressed": False})]:
            post(api, "/identity-links", {"source": source, "subject": name, "customer_id": cid,
                                          "expected_revision": state(api, cid)["revision"],
                                          "evidence": "Synthetic reviewed source account mapping"}, "operator")
            event(api, source, name, 1, attrs)
    return cid


def propose(api, cid, action="sync_profile", **overrides):
    from uuid import uuid4
    body = {"request_id": str(uuid4()), "customer_id": cid, "expected_revision": state(api, cid)["revision"],
            "action": action, "rationale": "Deterministic simulator proposes a governed action"}
    body.update(overrides)
    return post(api, "/proposals", body)


def job(pid):
    with db.connect() as c:
        return c.execute("SELECT * FROM cp.jobs WHERE id=%s", (pid,)).fetchone()


def due(pid):
    with db.connect(os.environ["TEST_ADMIN_DSN"]) as c:
        c.execute("UPDATE cp.jobs SET available_at=clock_timestamp()-interval '1 second' WHERE id=%s", (pid,))


def effects(pid):
    with db.connect(os.environ["REMOTE_DSN"]) as c:
        return c.execute("SELECT count(*) AS n FROM remote.effects WHERE key=%s", (pid,)).fetchone()["n"]


def plan(pid, modes):
    response = httpx.post(os.environ["DOWNSTREAM_URL"] + "/failure-plans", json={"key": pid, "modes": modes},
                          headers={"Authorization": "Bearer " + os.environ["INJECTOR_TOKEN"]}, trust_env=False)
    assert response.status_code == 200


@pytest.fixture
def evidence(request):
    def write(name, value):
        folder = os.environ.get("EVIDENCE_DIR")
        if folder:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{name}.json").write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    return write
