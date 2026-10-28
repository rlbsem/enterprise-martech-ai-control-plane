"""Executed local PostgreSQL database snapshots and separate HTTP/worker processes.

Local checkpoints use PostgreSQL CREATE DATABASE ... TEMPLATE with all control
writers stopped. This is an actual database copy/restore, not WAL replay or RDS PITR.
The independent receipt database is never restored with the control database.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from . import cloud, db, operations, recovery
from .policy import digest

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Runtime:
    """Actual child processes; runtime children receive no database-owner credentials."""

    def __init__(self, folder, dsns):
        self.folder, self.dsns, self.processes = Path(folder), dsns, []
        self.folder.mkdir(parents=True, exist_ok=True)
        self.tokens = {r: secrets.token_urlsafe(30) for r in cloud.ROLES}
        self.executor, self.injector = secrets.token_urlsafe(30), secrets.token_urlsafe(30)
        self.api_url, self.remote_url = "http://127.0.0.1:" + str(port()), "http://127.0.0.1:" + str(port())

    def env(self, role):
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "SYSTEMDRIVE", "COMSPEC", "HOME", "LANG"}
        result = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        result.update(PYTHONUTF8="1", PYTHONPATH=str(ROOT / "src"))
        if role == "remote":
            result.update(
                REMOTE_DSN=self.dsns["remote"], EXECUTOR_TOKEN=self.executor, INJECTOR_TOKEN=self.injector
            )
        elif role == "api":
            result.update(CONTROL_DSN=self.dsns["api"], API_TOKENS=json.dumps(self.tokens))
        elif role == "worker":
            result.update(
                CONTROL_DSN=self.dsns["worker"],
                EXECUTOR_TOKEN=self.executor,
                DOWNSTREAM_URL=self.remote_url,
                REMOTE_TIMEOUT="0.2",
            )
        return result

    def spawn(self, role, command, extra=None):
        log = (self.folder / (str(len(self.processes)) + "-" + role + ".log")).open("wb")
        try:
            p = subprocess.Popen(
                [sys.executable, *command],
                env={**self.env(role), **(extra or {})},
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        finally:
            log.close()
        self.processes.append((role, p))
        return p

    def start(self, role):
        url = self.api_url if role == "api" else self.remote_url
        module = "api" if role == "api" else "mock"
        p = self.spawn(
            role,
            [
                "-m",
                "uvicorn",
                "controlplane." + module + ":app",
                "--host",
                "127.0.0.1",
                "--port",
                url.rsplit(":", 1)[1],
                "--no-access-log",
            ],
        )
        for _ in range(150):
            try:
                if httpx.get(url + "/health", timeout=1, trust_env=False).status_code == 200:
                    return p
            except httpx.TransportError:
                pass
            if p.poll() is not None:
                raise RuntimeError("runtime_start_failed_" + role)
            time.sleep(0.03)
        raise TimeoutError("runtime_health_timeout")

    def stop(self, role=None):
        for name, p in self.processes:
            if (role is None or name == role) and p.poll() is None:
                p.terminate()
                p.wait(timeout=10)

    def witness(self):
        return {
            "quiescent": all(p.poll() is not None for _, p in self.processes),
            "observed_at": datetime.now(UTC).isoformat(),
            "registered_runtime": [
                {"service": n, "pid": p.pid, "exited": p.poll() is not None} for n, p in self.processes
            ],
        }

    def request(self, path, body, actor="agent"):
        r = httpx.post(
            self.api_url + path,
            json=body,
            headers={"Authorization": "Bearer " + self.tokens[actor]},
            timeout=10,
            trust_env=False,
        )
        if r.status_code != 200:
            raise RuntimeError("synthetic_api_rejected_" + str(r.status_code))
        return r.json()

    def customer(self, key):
        r = httpx.get(
            self.api_url + "/customers/" + key,
            headers={"Authorization": "Bearer " + self.tokens["agent"]},
            timeout=10,
            trust_env=False,
        )
        r.raise_for_status()
        return r.json()

    def event(self, source, subject, sequence, attributes):
        return self.request(
            "/events",
            {"event_id": str(uuid4()), "subject": subject, "sequence": sequence, "attributes": attributes},
            source,
        )

    def seed(self, subject):
        key = self.event("crm", subject, 1, {"email": subject + "@example.test", "lifecycle": "Opportunity"})[
            "customer_id"
        ]
        for source, attributes in [("consent", {"consent": True}), ("privacy", {"suppressed": False})]:
            self.request(
                "/identity-links",
                {
                    "source": source,
                    "subject": subject,
                    "customer_id": key,
                    "expected_revision": self.customer(key)["revision"],
                    "evidence": "Synthetic source mapping for isolated recovery acceptance",
                },
                "operator",
            )
            self.event(source, subject, 1, attributes)
        return key

    def propose(self, key, action="sync_profile"):
        result = self.request(
            "/proposals",
            {
                "request_id": str(uuid4()),
                "customer_id": key,
                "expected_revision": self.customer(key)["revision"],
                "action": action,
                "rationale": "Synthetic recovery acceptance; no external communication",
            },
        )
        if result.get("decision") == "approval":
            approval = self.request(
                "/proposals/" + result["id"] + "/approve",
                {"expected_revision": self.customer(key)["revision"]},
                "approver",
            )
            assert approval["approved"]
        return result

    def plan(self, key, modes):
        r = httpx.post(
            self.remote_url + "/failure-plans",
            json={"key": key, "modes": modes},
            headers={"Authorization": "Bearer " + self.injector},
            trust_env=False,
            timeout=5,
        )
        r.raise_for_status()

    def once(self, mode="once", key=None, token=None, lease=1, wait=True):
        code = """import json,os
from uuid import UUID
from controlplane import db,worker
mode=os.environ['PROBE_MODE']
if mode=='once':result=worker.once()
elif mode=='claim':
 row=worker.claim(int(os.environ['LEASE_SECONDS']));result={'id':str(row['id']),'token':str(row['lease_token'])} if row else None
elif mode=='kill':worker.execute(worker.claim(int(os.environ['LEASE_SECONDS'])));result=True
else:
 with db.connect() as c:row=c.execute('SELECT * FROM cp.jobs WHERE id=%s',(os.environ['PROBE_KEY'],)).fetchone()
 if mode=='fence':row['lease_token']=UUID(os.environ['PROBE_TOKEN']);result=worker.finish(row,'succeeded','stale_probe')
 else:worker.execute(row);result=True
if os.environ.get('PROBE_OUTPUT'):
 with open(os.environ['PROBE_OUTPUT'],'w',encoding='utf-8') as f:json.dump(result,f)
"""
        output = self.folder / ("probe-" + uuid4().hex + ".json")
        extra = {
            "PROBE_MODE": mode,
            "PROBE_KEY": key or "",
            "PROBE_TOKEN": token or "",
            "LEASE_SECONDS": str(lease),
            "PROBE_OUTPUT": str(output),
        }
        if mode == "kill":
            extra["REMOTE_TIMEOUT"] = "5"
        p = self.spawn("worker", ["-c", code], extra)
        if not wait:
            return p
        p.wait(timeout=20)
        if p.returncode:
            raise RuntimeError("worker_probe_failed")
        return json.loads(output.read_bytes())


class LocalDatabases:
    def __init__(self, admin):
        info = conninfo_to_dict(admin)
        if not info.get("dbname", "").endswith("_test") or info.get("host") not in {
            "127.0.0.1",
            "localhost",
            "db",
            "postgres",
        }:
            raise ValueError("isolated_local_test_cluster_required")
        self.admin = make_conninfo(admin, dbname="postgres")
        self.prefix, self.created = "cr_" + uuid4().hex[:12] + "_", []
        self.role_names = {
            k: self.prefix + k for k in ["api", "worker", "reader", "coordinator", "remote", "remote_reader"]
        }
        self.created_roles = []

    def dsn(self, name, user=None):
        return make_conninfo(
            self.admin, dbname=name, **({"user": user, "password": "local-recovery-only"} if user else {})
        )

    def create(self, suffix, template=None):
        name = self.prefix + suffix
        if template is not None and template not in self.created:
            raise ValueError("unowned_snapshot")
        with psycopg.connect(self.admin, autocommit=True) as c:
            query = sql.SQL("CREATE DATABASE {}" if template is None else "CREATE DATABASE {} TEMPLATE {}")
            c.execute(
                query.format(*[sql.Identifier(x) for x in ([name] if template is None else [name, template])])
            )
        self.created.append(name)
        return name

    def freeze(self, name):
        if name not in self.created:
            raise ValueError("unowned_snapshot")
        with psycopg.connect(self.admin, autocommit=True) as c:
            c.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(sql.Identifier(name)))

    def setup(self, control, remote):
        with db.connect(self.admin) as c:
            # These migration prerequisites may be shared by the surrounding test
            # suite. Never reset an existing role's password or memberships.
            for name, password in [
                ("control_app", "local-app-only"),
                ("synthetic_remote", "local-remote-only"),
            ]:
                if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
                    cloud.role(c, name, password)
            for name in self.role_names.values():
                if c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
                    raise ValueError("run_role_collision")
                cloud.role(c, name, "local-recovery-only")
                self.created_roles.append(name)
        for name in (control, remote):
            db.migrate(self.dsn(name))
        role = self.role_names
        with db.connect(self.dsn(control)) as c:
            for name in (role["api"], role["worker"], role["reader"]):
                for statement in [
                    "GRANT USAGE ON SCHEMA cp TO {}",
                    "GRANT SELECT ON ALL TABLES IN SCHEMA cp TO {}",
                ]:
                    c.execute(sql.SQL(statement).format(sql.Identifier(name)))
            for statement in [
                "GRANT INSERT,UPDATE ON cp.customers,cp.identities,cp.proposals,cp.jobs,cp.exceptions TO {}",
                "GRANT INSERT ON cp.events,cp.audit TO {}",
                "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA cp TO {}",
                "GRANT EXECUTE ON FUNCTION cp.execution_open() TO {}",
            ]:
                c.execute(sql.SQL(statement).format(sql.Identifier(role["api"])))
            for statement in [
                "GRANT UPDATE ON cp.jobs TO {}",
                "GRANT UPDATE(revision) ON cp.customers TO {}",
                "GRANT INSERT ON cp.audit,cp.exceptions TO {}",
                "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA cp TO {}",
                "GRANT EXECUTE ON FUNCTION cp.execution_open() TO {}",
            ]:
                c.execute(sql.SQL(statement).format(sql.Identifier(role["worker"])))
        with db.connect(self.dsn(remote)) as c:
            for name in (role["remote"], role["remote_reader"]):
                c.execute(sql.SQL("GRANT USAGE ON SCHEMA remote TO {}").format(sql.Identifier(name)))
                c.execute(sql.SQL("GRANT SELECT ON remote.effects TO {}").format(sql.Identifier(name)))
            c.execute(sql.SQL("GRANT INSERT ON remote.effects TO {}").format(sql.Identifier(role["remote"])))
            c.execute(
                sql.SQL("GRANT SELECT,INSERT,UPDATE ON remote.plans TO {}").format(
                    sql.Identifier(role["remote"])
                )
            )
        recovery.grants(self.dsn(control), role["coordinator"])

    def roles(self, control, remote):
        return {
            name: self.dsn(remote if name in {"remote", "remote_reader"} else control, role)
            for name, role in self.role_names.items()
        }

    def cleanup(self):
        with psycopg.connect(self.admin, autocommit=True) as c:
            for name in reversed(self.created):
                if not name.startswith(self.prefix):
                    raise ValueError("cleanup_scope_mismatch")
                c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            remaining = c.execute(
                "SELECT count(*) FROM pg_database WHERE datname LIKE %s", (self.prefix + "%",)
            ).fetchone()[0]
            for name in reversed(self.created_roles):
                if not name.startswith(self.prefix):
                    raise ValueError("role_cleanup_scope_mismatch")
                c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))
            roles_remaining = c.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname LIKE %s", (self.prefix + "%",)
            ).fetchone()[0]
        if remaining or roles_remaining:
            raise RuntimeError("local_cleanup_incomplete")
        return {
            "created_databases": len(self.created),
            "remaining_generated_databases": remaining,
            "created_roles": len(self.created_roles),
            "remaining_generated_roles": roles_remaining,
        }


def rows(dsn):
    with db.connect(dsn) as c:
        return c.execute("SELECT key,hash,body,receipt FROM remote.effects ORDER BY key").fetchall()


def refresh(runtime, subjects, sequence):
    runtime.start("api")
    for index, (subject, _) in enumerate(subjects):
        runtime.event("consent", subject, sequence, {"consent": index != 1})
        runtime.event("privacy", subject, sequence, {"suppressed": False})
    runtime.stop("api")


def controller(databases, control, remote, source_identity, point, revision, runtime):
    dsns = databases.roles(control, remote)
    binding = {
        "environment": "isolated-local",
        "deployment_revision": revision,
        "source_database": source_identity,
        "restored_database": recovery.database_identity(dsns["reader"]),
        "recovery_point": point,
    }
    return recovery.Recovery(
        dsns["coordinator"], dsns["reader"], dsns["remote_reader"], binding, runtime.witness
    )


def expect_rejected(action):
    try:
        action()
    except ValueError as exc:
        return str(exc)
    raise AssertionError("unsafe_recovery_was_not_rejected")


def execute_local(admin, private):
    """Eight failure/recovery scenarios over real persisted facts; no AWS calls."""
    databases, runtimes, scenarios = LocalDatabases(admin), [], {}
    revision = digest({p.name: p.read_bytes().hex() for p in sorted(Path(__file__).parent.glob("*.py"))})
    result = {
        "schema": "cloud-recovery-local.v1",
        "synthetic": True,
        "aws_execution": False,
        "snapshot_mechanism": "PostgreSQL CREATE DATABASE TEMPLATE; not WAL or native RDS PITR",
        "deployment_revision": revision,
        "scenarios": scenarios,
    }
    started = time.perf_counter()

    def runtime(control, remote, label):
        r = Runtime(Path(private) / label, databases.roles(control, remote))
        runtimes.append(r)
        return r

    try:
        source, remote = databases.create("source"), databases.create("receipts")
        databases.setup(source, remote)
        live = runtime(source, remote, "source")
        live.start("remote")
        live.start("api")
        subjects = [("synthetic" + str(i), live.seed("synthetic" + str(i))) for i in range(3)]
        normal = live.propose(subjects[0][1])["id"]
        assert live.once()
        lost = live.propose(subjects[1][1], "send_marketing")["id"]
        live.plan(lost, ["timeout_after"])
        assert live.once()
        killed_key = live.propose(subjects[2][1])["id"]
        live.plan(killed_key, ["timeout_after"])
        killed = live.once(mode="kill", wait=False)
        for _ in range(200):
            if any(str(r["key"]) == killed_key for r in rows(live.dsns["remote_reader"])):
                break
            if killed.poll() is not None:
                raise AssertionError("worker_exited_before_kill")
            time.sleep(0.015)
        else:
            raise TimeoutError("independent_commit_not_observed")
        with db.connect(databases.dsn(source)) as c:
            old_token = str(
                c.execute("SELECT lease_token FROM cp.jobs WHERE id=%s", (killed_key,)).fetchone()[
                    "lease_token"
                ]
            )
        assert killed.poll() is None
        killed.terminate()
        killed.wait(timeout=10)
        live.stop("api")
        source_id = recovery.database_identity(live.dsns["reader"])
        first_at = datetime.now(UTC).isoformat()
        point1 = databases.create("point1", source)
        databases.freeze(point1)
        live.start("api")
        after = []
        for _ in range(2):
            after.append(live.propose(subjects[0][1])["id"])
            # Prioritize this new fixture item without changing the killed worker's
            # lease or uncertain intent. Its original one-second lease expires naturally.
            with db.connect(databases.dsn(source)) as c:
                c.execute(
                    "UPDATE cp.jobs SET available_at=clock_timestamp()-interval '1 day' WHERE id=%s",
                    (after[-1],),
                )
            assert live.once()
        live.stop()
        final_at = datetime.now(UTC).isoformat()
        point2 = databases.create("point2", source)
        databases.freeze(point2)
        before = rows(live.dsns["remote_reader"])
        assert len(before) == 5
        remote_identity = recovery.database_identity(live.dsns["remote_reader"])

        restored = databases.create("behind", point1)
        bad = runtime(restored, remote, "behind")
        gate = controller(databases, restored, remote, source_id, first_at, revision, bad)
        session = gate.begin()
        refresh(bad, subjects, 2)
        proof = gate.verify(session)
        missing = [f["key"] for f in proof["findings"] if f["reason"] == "remote_ahead_missing_intent"]
        assert set(missing) == set(after) and proof["safe_to_resume"] is False
        assert bad.once() is False
        refusal = expect_rejected(lambda: gate.resume(session, proof["proof_id"]))
        scenarios["A_remote_ahead"] = {
            "status": "passed",
            "verification": proof,
            "resume_refused": refusal,
            "worker_claimed": False,
            "missing_restored_intents": len(missing),
        }
        assert rows(live.dsns["remote_reader"]) == before

        # A corrupt CONTROL copy cannot overwrite the original independent fact.
        conflict = databases.create("conflict", point2)
        cr = runtime(conflict, remote, "conflict")
        cg = controller(databases, conflict, remote, source_id, final_at, revision, cr)
        cs = cg.begin()
        refresh(cr, subjects, 2)
        with db.connect(databases.dsn(conflict)) as c:
            value = c.execute("SELECT mutation FROM cp.jobs WHERE id=%s", (normal,)).fetchone()["mutation"]
            value["payload"]["email"] = "fault-injected@example.test"
            c.execute("UPDATE cp.jobs SET mutation=%s WHERE id=%s", (Jsonb(value), normal))
        cp = cg.verify(cs)
        assert not cp["safe_to_resume"] and "remote_identity_conflict" in {
            f["reason"] for f in cp["findings"]
        }
        scenarios["D_mutation_conflict"] = {
            "status": "passed",
            "verification": cp,
            "independent_original_preserved": rows(live.dsns["remote_reader"]) == before,
        }

        receipt_loss, receipt_control = (
            databases.create("receipt_loss", remote),
            databases.create("acknowledged", point2),
        )
        er = runtime(receipt_control, receipt_loss, "missing-receipt")
        eg = controller(databases, receipt_control, receipt_loss, source_id, final_at, revision, er)
        es = eg.begin()
        refresh(er, subjects, 2)
        with db.connect(databases.dsn(receipt_loss)) as c:
            c.execute("DELETE FROM remote.effects WHERE key=%s", (normal,))
        ep = eg.verify(es)
        assert not ep["safe_to_resume"] and "acknowledged_receipt_missing" in {
            f["reason"] for f in ep["findings"]
        }
        scenarios["E_missing_acknowledged_receipt"] = {
            "status": "passed",
            "verification": ep,
            "fault_scope": "Deleted one receipt only from a separate negative-test copy; original receiver unchanged",
        }
        assert rows(live.dsns["remote_reader"]) == before

        good_control = databases.create("matching", point2)
        good = runtime(good_control, remote, "matching")
        gg = controller(databases, good_control, remote, source_id, final_at, revision, good)
        gs = gg.begin()
        unrefreshed = gg.verify(gs)
        assert not unrefreshed["safe_to_resume"]
        refresh(good, subjects, 2)
        gp = gg.verify(gs)
        assert gp["safe_to_resume"]
        gg.approve(gs, gp["proof_id"])
        good.start("api")
        good.event("crm", subjects[0][0], 2, {"owner": "changed_owner"})
        good.stop("api")
        stale = expect_rejected(lambda: gg.resume(gs, gp["proof_id"]))
        assert stale == "recovery_evidence_changed" and good.once() is False
        scenarios["H_stale_approval"] = {
            "status": "passed",
            "prior_proof": gp["proof_id"],
            "refusal": stale,
            "gate_remained_closed": True,
        }
        gp = gg.verify(gs)
        assert gp["safe_to_resume"]
        gg.approve(gs, gp["proof_id"])
        resumed_at = time.perf_counter()
        opened = gg.resume(gs, gp["proof_id"])
        good.start("remote")
        good.start("api")
        fenced = None
        for _ in range(10):
            claimed = good.once("claim")
            if not claimed:
                break
            if claimed["id"] == killed_key:
                fenced = good.once("fence", killed_key, old_token)
                assert fenced is False and claimed["token"] != old_token
            good.once("execute", claimed["id"])
        assert fenced is False
        with db.connect(databases.dsn(good_control)) as c:
            saved = c.execute("SELECT reason,uncertain,status FROM cp.jobs WHERE id=%s", (lost,)).fetchone()
            killed_state = c.execute(
                "SELECT reason,status FROM cp.jobs WHERE id=%s", (killed_key,)
            ).fetchone()
        assert saved["reason"] == "reconciled_committed_effect" and saved["status"] == "succeeded"
        assert killed_state["status"] == "succeeded" and rows(live.dsns["remote_reader"]) == before
        scenarios["B_lost_acknowledgment"] = {
            "status": "passed",
            "key": lost,
            "sender": saved,
            "original_effect_count": 1,
            "independent_receipt_recovered": True,
        }
        scenarios["C_killed_worker"] = {
            "status": "passed",
            "key": killed_key,
            "terminated_pid": killed.pid,
            "old_completion_accepted": fenced,
            "replacement": killed_state,
            "original_effect_count": 1,
            "lease_boundary": "Original process was terminated; its real one-second lease expired without rewriting the deadline",
        }
        denied = good.propose(subjects[1][1], "send_marketing")
        assert denied["decision"] == "deny" and denied["reason"] == "consent_missing_or_revoked"
        scenarios["F_current_authority"] = {
            "status": "passed",
            "restored_permission_refresh_required": unrefreshed,
            "new_dispatch": denied,
            "historical_committed_effect_still_recognized": True,
        }
        fresh = good.propose(subjects[0][1])
        assert fresh["decision"] == "allow" and good.once()
        good.stop()
        final = operations.recovery_snapshot(good.dsns["reader"], good.dsns["remote_reader"])
        after_rows = rows(good.dsns["remote_reader"])
        assert final["safe_to_resume"] and len(after_rows) == 6
        assert {str(r["key"]) for r in after_rows} == {str(r["key"]) for r in before} | {fresh["id"]}
        assert recovery.database_identity(good.dsns["remote_reader"]) == remote_identity
        scenarios["G_safe_resumption"] = {
            "status": "passed",
            "approval_proof": gp,
            "resume": opened,
            "post_resume": final,
            "independent_effects_before": 5,
            "independent_effects_after": 6,
            "duplicate_business_effects": 0,
            "independent_database_identity_unchanged": True,
            "resumption_to_verified_seconds": time.perf_counter() - resumed_at,
        }
        result.update(
            status="passed",
            original_intents=5,
            original_independent_effects=5,
            independent_final_effects=6,
            elapsed_seconds=time.perf_counter() - started,
            checkpoints=[{"at": first_at, "local_intents": 3}, {"at": final_at, "local_intents": 5}],
            independent_receipts=after_rows,
            boundary="Isolated PostgreSQL snapshots and HTTP processes; no native AWS or production RPO/RTO claim",
        )
    finally:
        for r in runtimes:
            r.stop()
        result["cleanup"] = databases.cleanup()
    return result
