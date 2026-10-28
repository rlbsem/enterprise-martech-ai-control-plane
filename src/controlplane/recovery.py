"""Durable recovery sessions. A stored pass or edited report never opens execution.

The caller must supply a live quiescence witness for its registered processes or
tasks. It is not proof against an untrusted infrastructure/database administrator.
"""

import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from . import db, operations
from .policy import POLICY, POLICY_HASH, digest

OPERATIONAL = {"recovery_gate", "recovery_sessions", "recovery_proofs"}


def database_identity(reader):
    with db.connect(reader) as c:
        row = c.execute(
            "SELECT current_database() AS database,inet_server_addr()::text AS address,"
            "inet_server_port() AS port,pg_postmaster_start_time() AS started,"
            "oid FROM pg_catalog.pg_database WHERE datname=current_database()"
        ).fetchone()
    return digest(row)


def grants(owner_dsn, coordinator_role="control_recovery"):
    """Migration-owner only; coordinator has no business-write privileges."""
    statements = [
        "GRANT USAGE ON SCHEMA cp TO {}",
        "GRANT SELECT ON ALL TABLES IN SCHEMA cp TO {}",
        "GRANT INSERT ON cp.recovery_sessions,cp.recovery_proofs TO {}",
        "GRANT UPDATE(mode,epoch,session_id,approved_proof,approved_at) ON cp.recovery_gate TO {}",
        "GRANT UPDATE(status) ON cp.recovery_sessions TO {}",
    ]
    with db.connect(owner_dsn) as c:
        for statement in statements:
            c.execute(sql.SQL(statement).format(sql.Identifier(coordinator_role)))


def fingerprint(c):
    """All persisted control business/configuration rows; operational proof logs excluded."""
    tables = c.execute(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='cp' ORDER BY tablename"
    ).fetchall()
    hashes = {}
    for item in tables:
        name = item["tablename"]
        if name in OPERATIONAL:
            continue
        rows = c.execute(
            sql.SQL("SELECT to_jsonb(t) AS body FROM cp.{} t").format(sql.Identifier(name))
        ).fetchall()
        hashes[name] = digest(sorted(digest(r["body"]) for r in rows))
    roles = c.execute(
        "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin,"
        "rolvaliduntil::text AS rolvaliduntil "
        "FROM pg_roles WHERE rolname NOT LIKE 'pg_%' "
        "ORDER BY rolname"
    ).fetchall()
    privileges = c.execute(
        "SELECT n.nspname,c.relname,c.relacl::text FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='cp' "
        "ORDER BY c.relname"
    ).fetchall()
    memberships = c.execute(
        "SELECT roleid,member,grantor,admin_option FROM pg_auth_members ORDER BY roleid,member,grantor"
    ).fetchall()
    return digest(
        {
            "tables": hashes,
            "runtime_policy": POLICY_HASH,
            "roles": roles,
            "memberships": memberships,
            "privileges": privileges,
        }
    )


def authority_findings(c, started):
    """Require fresh owner-source observations after recovery starts, not saved permission flags.

    Consent/privacy credentials still use normal authenticated source ingestion.
    An authoritative feed is trusted for truth; this cannot establish the truth of
    a compromised source or recall an already in-flight external action.
    """
    findings = []
    for row in c.execute("SELECT id,state,provenance FROM cp.customers ORDER BY id"):
        for field in ("consent", "suppressed"):
            p = row["provenance"].get(field, {})
            event = c.execute(
                "SELECT body,received_at FROM cp.events WHERE source=%s AND event_id=%s",
                (p.get("source"), p.get("event_id")),
            ).fetchone()
            if (
                p.get("source") != POLICY["owners"][field]
                or not event
                or event["received_at"] < started
                or field not in event["body"]["attributes"]
                or event["body"]["attributes"][field] is not row["state"].get(field)
            ):
                findings.append(
                    {"key": str(row["id"]), "reason": "current_authority_refresh_missing", "field": field}
                )
    return findings


class Recovery:
    def __init__(self, coordinator_dsn, control_reader, remote_reader, binding, quiescence):
        required = {
            "environment",
            "deployment_revision",
            "source_database",
            "restored_database",
            "recovery_point",
        }
        if set(binding) != required or binding["environment"] not in {
            "isolated-local",
            "isolated-aws-us-east-2",
        }:
            raise ValueError("exact_recovery_binding_required")
        if not re.fullmatch(r"[a-f0-9]{64}", binding["deployment_revision"]):
            raise ValueError("exact_source_or_release_fingerprint_required")
        if any(not isinstance(binding[k], str) or not binding[k] for k in required):
            raise ValueError("empty_recovery_binding")
        if binding["source_database"] == binding["restored_database"] or binding[
            "restored_database"
        ] != database_identity(control_reader):
            raise ValueError("restored_database_identity_mismatch")
        if database_identity(control_reader) == database_identity(remote_reader):
            raise ValueError("independent_receipt_database_required")
        if not callable(quiescence):
            raise ValueError("live_quiescence_witness_required")
        self.dsn, self.reader, self.remote = coordinator_dsn, control_reader, remote_reader
        self.binding, self.witness = dict(binding), quiescence
        self.remote_identity = database_identity(remote_reader)

    def _session(self, c, key):
        row = c.execute("SELECT * FROM cp.recovery_sessions WHERE id=%s", (key,)).fetchone()
        gate = c.execute("SELECT * FROM cp.recovery_gate WHERE singleton").fetchone()
        if not row or str(gate["session_id"]) != str(key) or gate["mode"] != "closed":
            raise ValueError("closed_current_recovery_session_required")
        if row["binding_hash"] != digest(self.binding) or row["binding"] != self.binding:
            raise ValueError("recovery_binding_changed")
        return row, gate

    def begin(self):
        # Close admission/dispatch before any recovery services can be started.
        key = uuid4()
        with db.connect(self.dsn) as c:
            gate = c.execute("SELECT * FROM cp.recovery_gate WHERE singleton FOR UPDATE").fetchone()
            if gate["mode"] == "closed":
                raise ValueError("recovery_already_active")
            c.execute(
                "INSERT INTO cp.recovery_sessions(id,binding,binding_hash,status) VALUES (%s,%s,%s,'closed')",
                (key, Jsonb(self.binding), digest(self.binding)),
            )
            c.execute(
                "UPDATE cp.recovery_gate SET mode='closed',epoch=epoch+1,session_id=%s,"
                "approved_proof=NULL,approved_at=NULL WHERE singleton",
                (key,),
            )
        return key

    def _observe(self, c, session):
        if (
            database_identity(self.reader) != self.binding["restored_database"]
            or database_identity(self.remote) != self.remote_identity
        ):
            raise ValueError("recovery_database_identity_changed")
        witness = self.witness()
        # The witness must query current state, not load a saved pass document.
        if (
            set(witness) != {"quiescent", "observed_at", "registered_runtime"}
            or witness["quiescent"] is not True
        ):
            raise ValueError("runtime_not_quiescent")
        at = datetime.fromisoformat(witness["observed_at"])
        if at.tzinfo is None or not timedelta(seconds=-2) <= datetime.now(UTC) - at <= timedelta(seconds=10):
            raise ValueError("stale_quiescence_witness")
        comparison = operations.recovery_snapshot(self.reader, self.remote)
        if "destinations" in comparison:
            raise ValueError("exact_multi_destination_recovery_not_configured")
        findings = comparison["findings"] + authority_findings(c, session["created_at"])
        return {
            **comparison,
            "safe_to_resume": not findings,
            "findings": findings,
            "control_fingerprint": fingerprint(c),
            "binding_hash": digest(self.binding),
            "quiescence": witness,
            "policy_hash": POLICY_HASH,
        }

    def verify(self, session_id):
        with db.connect(self.dsn) as c:
            c.execute("SELECT singleton FROM cp.recovery_gate WHERE singleton FOR UPDATE")
            session, _ = self._session(c, session_id)
            result = self._observe(c, session)
            key = uuid4()
            c.execute(
                "INSERT INTO cp.recovery_proofs(id,session_id,report,report_hash) VALUES (%s,%s,%s,%s)",
                (key, session_id, Jsonb(result), digest(result)),
            )
            return {"proof_id": str(key), **result}

    def _fresh_approval(self, c, session_id, proof_id):
        session, gate = self._session(c, session_id)
        proof = c.execute(
            "SELECT * FROM cp.recovery_proofs WHERE id=%s AND session_id=%s", (proof_id, session_id)
        ).fetchone()
        if (
            not proof
            or proof["report"]["safe_to_resume"] is not True
            or proof["report_hash"] != digest(proof["report"])
            or datetime.now(UTC) - proof["created_at"] > timedelta(seconds=90)
        ):
            raise ValueError("fresh_safe_proof_required")
        fresh = self._observe(c, session)
        if not fresh["safe_to_resume"] or any(
            fresh[k] != proof["report"][k]
            for k in ("control_fingerprint", "snapshot_hash", "binding_hash", "policy_hash")
        ):
            raise ValueError("recovery_evidence_changed")
        return fresh, gate

    def approve(self, session_id, proof_id):
        with db.connect(self.dsn) as c:
            c.execute("SELECT singleton FROM cp.recovery_gate WHERE singleton FOR UPDATE")
            self._fresh_approval(c, session_id, proof_id)
            c.execute(
                "UPDATE cp.recovery_gate SET approved_proof=%s,approved_at=clock_timestamp() WHERE singleton",
                (proof_id,),
            )
            c.execute("UPDATE cp.recovery_sessions SET status='approved' WHERE id=%s", (session_id,))

    def resume(self, session_id, proof_id):
        with db.connect(self.dsn) as c:
            c.execute("SELECT singleton FROM cp.recovery_gate WHERE singleton FOR UPDATE")
            fresh, gate = self._fresh_approval(c, session_id, proof_id)
            if str(gate["approved_proof"]) != str(proof_id):
                raise ValueError("exact_operator_approval_required")
            c.execute("UPDATE cp.recovery_gate SET mode='open',approved_proof=NULL WHERE singleton")
            c.execute("UPDATE cp.recovery_sessions SET status='resumed' WHERE id=%s", (session_id,))
            return {
                "resumed": True,
                "fresh_comparison": fresh,
                "deployment_revision": self.binding["deployment_revision"],
            }
