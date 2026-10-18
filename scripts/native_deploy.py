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
    return len(plan["resource_changes"])


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
        armed_gate(manifest, clients)
        if document["variables"]["guard_code_sha256"]["value"] != manifest["guard_code_sha256"]:
            p.error("Approved guard code mismatch")
    else:
        account_gate(manifest, clients)
        from datetime import UTC, datetime

        if (
            not 0
            < (datetime.fromisoformat(manifest["cleanup_at"]) - datetime.now(UTC)).total_seconds()
            <= 10800
        ):
            p.error("Guard deadline must be within three hours")
    subprocess.run(
        [a.terraform, f"-chdir={root}", "apply", "-input=false", str(a.plan.resolve())], check=True
    )


if __name__ == "__main__":
    main()
