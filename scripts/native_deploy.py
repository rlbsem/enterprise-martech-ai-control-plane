"""Apply only an approved create-only acceptance plan after independent live gates.

Never supplies credentials or upgrades the account. Saved plans/private manifests stay local.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import boto3

from controlplane.native_arming import account_gate, armed_gate

ROOT = Path(__file__).resolve().parents[1]


def review_plan(plan, phase):
    if phase not in ("guard", "runtime") or not plan.get("resource_changes"):
        raise ValueError("Nonempty isolated acceptance plan required")
    forbidden = []
    for resource in plan["resource_changes"]:
        if resource.get("module_address") or resource["mode"] not in ("data", "managed"):
            forbidden.append(resource["address"])
        if resource["mode"] == "managed" and resource["change"]["actions"] not in (["create"], ["no-op"]):
            forbidden.append(resource["address"])
    if forbidden:
        raise ValueError("Only reviewed root creates/no-ops allowed; never update/delete existing resources")
    databases = [r for r in plan["resource_changes"] if r.get("type") == "aws_db_instance"]
    if phase == "runtime" and databases:
        expected = {'aws_db_instance.store["control"]', 'aws_db_instance.store["receipts"]'}
        if {r["address"] for r in databases} != expected:
            raise ValueError("Exactly two planned native databases required")
        output = plan["output_changes"]["manifest"]["after"]
        if (output.get("recovery_strategy") != "retained-two-instance-v1"
                or output.get("maximum_concurrent_instances") != 2
                or output.get("source_deletion_scope") != "disposable_synthetic_control_only"):
            raise ValueError("Plan must explicitly bind the two-instance destructive recovery protocol")
    return len(plan["resource_changes"])


def guard_deadline_gate(document, manifest):
    """Bind deferred clocks to the approved plan; explicit deadlines retain the old bound."""
    from datetime import UTC, datetime

    deadline = document["variables"]["cleanup_at"]["value"]
    if deadline is None:
        expected = "three_hours_from_guard_apply_start"
        policy = document["output_changes"]["guard_manifest"]["after"]["deadline_policy"]
        if manifest.get("deadline_policy") != expected or policy != expected:
            raise ValueError("Explicit deployment-anchored clock approval required")
        unknown = document["output_changes"]["guard_manifest"]["after_unknown"]
        if not unknown.get("cleanup_at") or not unknown.get("deployment_started_at"):
            raise ValueError("Deployment timestamps must resolve only at apply")
    elif not 0 < (datetime.fromisoformat(deadline) - datetime.now(UTC)).total_seconds() <= 10800:
        raise ValueError("Guard deadline must be within three hours")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=["guard", "runtime"], required=True)
    p.add_argument("--terraform", default="terraform")
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--approved-sha256", required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    if not a.execute or hashlib.sha256(a.plan.read_bytes()).hexdigest() != a.approved_sha256:
        p.error("Explicit approved saved-plan digest required")
    manifest = json.loads(a.manifest.read_text())
    session = boto3.Session(region_name="us-east-2")
    clients = {
        name: session.client(name, region_name="us-east-1" if name == "freetier" else "us-east-2")
        for name in ("sts", "freetier", "scheduler", "lambda")
    }
    root = ROOT / ("infra/acceptance-" + a.phase)
    document = json.loads(
        subprocess.check_output([a.terraform, f"-chdir={root}", "show", "-json", str(a.plan.resolve())])
    )
    review_plan(document, a.phase)
    # The saved plan variables must agree with live gates, not just a separately editable manifest.
    for key, expected in [
        ("account_id", manifest["account"]),
        ("run_id", manifest["run"]),
        ("cleanup_at", manifest["cleanup_at"]),
    ]:
        if document["variables"][key]["value"] != expected:
            p.error("Plan/manifest identity mismatch")
    if a.phase == "runtime":
        workflow = document["variables"].get("acceptance_workflow", {}).get("value", "sql-fixture-v1")
        if workflow not in {"sql-fixture-v1", "application-recovery-v1"}:
            p.error("Unknown saved-plan acceptance workflow")
        # The guard manifest predates the runtime topology. Derive the stronger
        # credit gate from the approved plan itself, not an editable side report.
        armed_gate({**manifest, "acceptance_workflow": workflow}, clients)
        if document["variables"]["guard_code_sha256"]["value"] != manifest["guard_code_sha256"]:
            p.error("Approved guard code mismatch")
    else:
        account_gate(manifest, clients)
        guard_deadline_gate(document, manifest)
    subprocess.run(
        [a.terraform, f"-chdir={root}", "apply", "-input=false", str(a.plan.resolve())], check=True
    )


if __name__ == "__main__":
    main()
