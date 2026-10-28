"""Bounded application recovery tasks; separate from the historical SQL-only fixture.

The driver is trusted and runs no public listener. API, worker and independent
receiver run as separate processes with distinct SQL credentials over loopback
HTTP. This is not the full production ECS/HTTPS deployment topology.
"""

import json
import os
import re
import secrets
import sys
import time
import traceback
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from psycopg.conninfo import make_conninfo

from . import cloud, db, operations, recovery
from .native_tasks import permission_denials, receipt_fingerprint, safe_failure
from .policy import digest
from .recovery_lab import Runtime, expect_rejected, refresh, rows

WORKFLOW = "application-recovery-v1"


def bootstrap(client):
    """Runtime-only secret handling; never returns owner credentials."""
    arns = json.loads(os.environ["RUNTIME_SECRET_ARNS"])
    if set(arns) != set(cloud.RUNTIME_KEYS) | {"recovery"}:
        raise ValueError("exact_application_secret_topology_required")
    os.environ["RUNTIME_SECRET_ARNS"] = json.dumps({k: v for k, v in arns.items() if k != "recovery"})
    try:
        cloud.bootstrap(client, database_name="csaacceptance")
    finally:
        os.environ["RUNTIME_SECRET_ARNS"] = json.dumps(arns)
    master = cloud.read_secret(client, json.loads(os.environ["DB_MASTER_SECRET_ARNS"])["control"])
    owner = make_conninfo(
        host=json.loads(os.environ["DB_HOSTS"])["control"],
        dbname="csaacceptance",
        user=master["username"],
        password=master["password"],
        sslmode="verify-full",
        sslrootcert="/app/certs/rds-global-bundle.pem",
    )
    try:
        document = cloud.read_secret(client, arns["recovery"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        document = {"password": secrets.token_urlsafe(36)}
        client.put_secret_value(SecretId=arns["recovery"], SecretString=json.dumps(document))
    if set(document) != {"password"} or not isinstance(document["password"], str) or not document["password"]:
        raise ValueError("exact_coordinator_secret_required")
    with db.connect(owner) as c:
        cloud.role(c, "control_recovery", document["password"])
    recovery.grants(owner)
    return {"bootstrap": "complete", "workflow": WORKFLOW, "owner_credentials_in_runtime_children": False}


def load_dsns(client, *, verifier=False, host=None):
    arns = json.loads(os.environ["APP_RUNTIME_SECRET_ARNS"])
    expected = {"verifier"} if verifier else {"api", "worker", "remote", "verifier", "recovery"}
    if set(arns) != expected:
        raise ValueError("scoped_application_secret_topology_required")
    documents = {k: cloud.read_secret(client, v) for k, v in arns.items()}
    for name, document in documents.items():
        fields = {"password"} if name == "recovery" else cloud.RUNTIME_KEYS[name]
        if set(document) != fields or any(not isinstance(v, str) or not v for v in document.values()):
            raise ValueError("unexpected_application_secret_content")
    dsns = {
        "reader": documents["verifier"]["CONTROL_DSN"],
        "remote_reader": documents["verifier"]["REMOTE_DSN"],
    }
    if not verifier:
        dsns.update(
            api=documents["api"]["CONTROL_DSN"],
            worker=documents["worker"]["CONTROL_DSN"],
            remote=documents["remote"]["REMOTE_DSN"],
            coordinator=make_conninfo(
                dsns["reader"], user="control_recovery", password=documents["recovery"]["password"]
            ),
        )
    if host:
        if not re.fullmatch(r"[a-z0-9.-]+[.]us-east-2[.]rds[.]amazonaws[.]com", host):
            raise ValueError("pinned_ohio_restored_endpoint_required")
        for key in set(dsns) - {"remote", "remote_reader"}:
            dsns[key] = make_conninfo(dsns[key], host=host)
    return dsns


def subjects(dsns):
    with db.connect(dsns["reader"]) as c:
        found = c.execute(
            "SELECT subject,customer_id FROM cp.identities WHERE source='crm' ORDER BY subject"
        ).fetchall()
    if len(found) != 3:
        raise ValueError("exact_three_synthetic_customers_required")
    return [(r["subject"], str(r["customer_id"])) for r in found]


def clock(dsns):
    with db.connect(dsns["reader"]) as c:
        return c.execute("SELECT clock_timestamp() AS at").fetchone()["at"].isoformat()


def seed(dsns, folder, run):
    if operations.recovery_snapshot(dsns["reader"], dsns["remote_reader"])["local_intents"] or rows(
        dsns["remote_reader"]
    ):
        raise ValueError("fresh_synthetic_ledgers_required")
    runtime = Runtime(folder, dsns)
    try:
        runtime.start("remote")
        runtime.start("api")
        customers = [(run + "-" + str(i), runtime.seed(run + "-" + str(i))) for i in range(3)]
        normal = runtime.propose(customers[0][1])["id"]
        assert runtime.once()
        lost = runtime.propose(customers[1][1], "send_marketing")["id"]
        runtime.plan(lost, ["timeout_after"])
        assert runtime.once()
        killed_key = runtime.propose(customers[2][1])["id"]
        runtime.plan(killed_key, ["timeout_after"])
        killed = runtime.once(mode="kill", wait=False)
        for _ in range(300):
            if any(str(r["key"]) == killed_key for r in rows(dsns["remote_reader"])):
                break
            if killed.poll() is not None:
                raise RuntimeError("worker_exited_before_independent_commit")
            time.sleep(0.025)
        else:
            raise TimeoutError("independent_commit_timeout")
        if killed.poll() is not None:
            raise RuntimeError("worker_not_alive_at_termination")
        killed.terminate()
        killed.wait(timeout=10)
    finally:
        runtime.stop()
    comparison = operations.recovery_snapshot(dsns["reader"], dsns["remote_reader"])
    if (
        comparison["local_intents"] != 3
        or comparison["remote_effects"] != 3
        or not comparison["safe_to_resume"]
    ):
        raise RuntimeError("baseline_application_comparison_failed")
    return {
        "workflow": WORKFLOW,
        "stage": "three_effects",
        "comparison": comparison,
        "recovery_point": clock(dsns),
        "source_database": recovery.database_identity(dsns["reader"]),
        "terminated_worker": True,
        "normal_key": normal,
        "lost_ack_key": lost,
        "killed_key": killed_key,
        "remote_ledger_sha256": receipt_fingerprint(dsns["remote_reader"]),
    }


def advance(dsns, folder):
    if len(rows(dsns["remote_reader"])) != 3:
        raise ValueError("three_effect_baseline_required")
    runtime = Runtime(folder, dsns)
    new_keys = []
    try:
        runtime.start("remote")
        runtime.start("api")
        target = subjects(dsns)[0][1]
        for _ in range(2):
            key = runtime.propose(target)["id"]
            # Prioritize only this new synthetic job; uncertain lease deadlines are not edited.
            with db.connect(dsns["worker"]) as c:
                c.execute(
                    "UPDATE cp.jobs SET available_at=clock_timestamp()-interval '1 day' WHERE id=%s", (key,)
                )
            assert runtime.once()
            new_keys.append(key)
    finally:
        runtime.stop()
    result = operations.recovery_snapshot(dsns["reader"], dsns["remote_reader"])
    if not result["safe_to_resume"] or result["local_intents"] != 5 or result["remote_effects"] != 5:
        raise RuntimeError("post_checkpoint_effects_not_established")
    return {
        "workflow": WORKFLOW,
        "stage": "five_effects",
        "comparison": result,
        "new_keys": new_keys,
        "recovery_point": clock(dsns),
        "remote_ledger_sha256": receipt_fingerprint(dsns["remote_reader"]),
    }


def recover(dsns, folder, binding, phase):
    if phase not in {"behind", "matching"}:
        raise ValueError("exact_recovery_phase_required")
    runtime = Runtime(folder, dsns)
    before = rows(dsns["remote_reader"])
    if len(before) != 5:
        raise ValueError("unchanged_five_effect_independent_database_required")
    identity = recovery.database_identity(dsns["remote_reader"])
    gate = recovery.Recovery(
        dsns["coordinator"], dsns["reader"], dsns["remote_reader"], binding, runtime.witness
    )
    session = gate.begin()
    try:
        unrefreshed = gate.verify(session)
        if unrefreshed["safe_to_resume"]:
            raise RuntimeError("restored_permissions_accepted_without_current_source")
        customers = subjects(dsns)
        refresh(runtime, customers, 2)
        proof = gate.verify(session)
        if phase == "behind":
            missing = [f for f in proof["findings"] if f["reason"] == "remote_ahead_missing_intent"]
            if proof["safe_to_resume"] or proof["local_intents"] != 3 or len(missing) != 2:
                raise RuntimeError("native_remote_ahead_not_detected")
            refusal = expect_rejected(lambda: gate.resume(session, proof["proof_id"]))
            if runtime.once() is not False or rows(dsns["remote_reader"]) != before:
                raise RuntimeError("unsafe_restored_worker_dispatch")
            return {
                "workflow": WORKFLOW,
                "stage": "behind_blocked",
                "verification": proof,
                "resume_refused": refusal,
                "worker_claimed": False,
                "remote_ledger_sha256": receipt_fingerprint(dsns["remote_reader"]),
            }
        if not proof["safe_to_resume"] or proof["local_intents"] != 5:
            raise RuntimeError("matching_recovery_not_established")
        gate.approve(session, proof["proof_id"])
        runtime.start("api")
        runtime.event("crm", customers[0][0], 2, {"owner": "synthetic-new-owner"})
        runtime.stop("api")
        stale = expect_rejected(lambda: gate.resume(session, proof["proof_id"]))
        if stale != "recovery_evidence_changed" or runtime.once() is not False:
            raise RuntimeError("stale_approval_not_rejected")
        proof = gate.verify(session)
        gate.approve(session, proof["proof_id"])
        opened = gate.resume(session, proof["proof_id"])
        with db.connect(dsns["reader"]) as c:
            original = c.execute("SELECT id,lease_token FROM cp.jobs WHERE status='leased'").fetchall()
        if len(original) != 1:
            raise ValueError("actual_terminated_worker_lease_required")
        old = original[0]
        runtime.start("remote")
        runtime.start("api")
        fenced = None
        for _ in range(10):
            claimed = runtime.once("claim")
            if claimed is None:
                break
            if claimed["id"] == str(old["id"]):
                fenced = runtime.once("fence", str(old["id"]), str(old["lease_token"]))
            runtime.once("execute", claimed["id"])
        if fenced is not False or rows(dsns["remote_reader"]) != before:
            raise RuntimeError("receipt_recovery_or_worker_fence_failed")
        with db.connect(dsns["reader"]) as c:
            recovered = c.execute("SELECT reason,uncertain,status FROM cp.jobs ORDER BY id").fetchall()
        if any(r["status"] != "succeeded" or r["uncertain"] for r in recovered):
            raise RuntimeError("uncertain_jobs_not_reconciled")
        denied = runtime.propose(customers[1][1], "send_marketing")
        if denied.get("decision") != "deny" or denied.get("reason") != "consent_missing_or_revoked":
            raise RuntimeError("current_consent_not_enforced")
        new = runtime.propose(customers[0][1])
        assert new["decision"] == "allow" and runtime.once()
    finally:
        runtime.stop()
    final = operations.runtime_acceptance_snapshot(dsns["reader"], dsns["remote_reader"])
    after = rows(dsns["remote_reader"])
    if (
        not final["safe_to_resume"]
        or len(after) != 6
        or {str(r["key"]) for r in after} != {str(r["key"]) for r in before} | {new["id"]}
        or recovery.database_identity(dsns["remote_reader"]) != identity
    ):
        raise RuntimeError("post_resume_independent_verification_failed")
    return {
        "workflow": WORKFLOW,
        "stage": "matching_resumed",
        "resume": opened,
        "stale_approval_refusal": stale,
        "revoked_action": denied,
        "old_completion_accepted": fenced,
        "recovered_jobs": recovered,
        "post_resume": final,
        "duplicate_business_effects": 0,
        "independent_database_identity_unchanged": True,
        "remote_ledger_sha256": receipt_fingerprint(dsns["remote_reader"]),
    }


def main():
    run = os.environ["ACCEPTANCE_RUN"]
    if not re.fullmatch("[a-z0-9]{8,16}", run):
        raise ValueError("pinned_run_required")
    action = sys.argv[1]
    client = boto3.client("secretsmanager", region_name="us-east-2")
    if action == "bootstrap":
        result = bootstrap(client)
    else:
        dsns = load_dsns(client, verifier=action == "verify", host=os.environ.get("RESTORED_HOST"))
        folder = Path("/recovery-work") / (action + "-" + secrets.token_hex(8))
        if action == "baseline":
            result = seed(dsns, folder, run)
        elif action == "control":
            result = advance(dsns, folder)
        elif action == "receipt":
            revision = digest(
                {
                    p.name: p.read_bytes().hex()
                    for p in sorted(Path(__file__).parent.iterdir())
                    if p.suffix in {".py", ".sql", ".json"}
                }
            )
            binding = {
                "environment": "isolated-aws-us-east-2",
                "deployment_revision": revision,
                "source_database": os.environ["SOURCE_DATABASE"],
                "restored_database": recovery.database_identity(dsns["reader"]),
                "recovery_point": os.environ["RECOVERY_POINT"],
            }
            result = recover(dsns, folder, binding, os.environ["RECOVERY_PHASE"])
        elif action == "verify":
            result = operations.runtime_acceptance_snapshot(dsns["reader"], dsns["remote_reader"])
            result["write_denials"] = permission_denials(dsns["reader"], dsns["remote_reader"])
            result["remote_ledger_sha256"] = receipt_fingerprint(dsns["remote_reader"])
        else:
            raise ValueError("unknown_application_task")
    print(json.dumps({"native_fixture": result}, default=str), flush=True)
    return 2 if action == "verify" and not result["safe_to_resume"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        failure = safe_failure(exc)
        failure["locations"] += [
            {"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
            for f in traceback.extract_tb(exc.__traceback__)
            if Path(f.filename).name in {"native_app_tasks.py", "recovery.py", "recovery_lab.py"}
        ]
        print(json.dumps({"native_failure": failure}), flush=True)
        raise SystemExit("Application task failed closed; private operator diagnostics required") from None
