"""Validate a private, owner-approved destroy plan. Never discover deletion targets by prefix."""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def validate_plan(plan, inventory):
    if inventory.get("environment") != "stage" or inventory.get("region") != "ca-central-1":
        raise ValueError("Only the isolated stage inventory is eligible")
    if inventory.get("disposition") != "discard_isolated_synthetic_state":
        raise ValueError("Explicit synthetic-state disposition required")
    expected = inventory["resources"]
    if not expected or len(expected) > 300:
        raise ValueError("Bounded exact resource inventory required")
    seen = set()
    for change in plan.get("resource_changes", []):
        if change.get("mode") == "data":
            continue
        actions = change["change"]["actions"]
        if actions == ["no-op"]:
            continue
        address = change["address"]
        before = change["change"].get("before") or {}
        approved = expected.get(address)
        if (
            actions != ["delete"]
            or not approved
            or address in seen
            or before.get("id") != approved["id"]
            or change["type"] != approved["type"]
        ):
            raise ValueError("Plan is not an exact approved deletion inventory")
        seen.add(address)
    if seen != set(expected):
        raise ValueError("Plan omits approved resources; no partial cleanup pass")
    if plan.get("errored") or plan.get("complete") is False:
        raise ValueError("Incomplete or errored plan")
    return len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform", default="terraform")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--approved-inventory-sha256", required=True)
    parser.add_argument("--approved-plan-sha256", required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.record.exists():
        parser.error("Use a fresh evidence path")
    inventory_bytes, plan_bytes = args.inventory.read_bytes(), args.plan.read_bytes()
    if (
        hashlib.sha256(inventory_bytes).hexdigest() != args.approved_inventory_sha256
        or hashlib.sha256(plan_bytes).hexdigest() != args.approved_plan_sha256
    ):
        parser.error("Private approval hashes do not match")
    inventory = json.loads(inventory_bytes)
    # Expired cleanup authority is not silently extended by rerunning a workflow.
    expiry = datetime.fromisoformat(inventory["cleanup_authority_expires_at"])
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        parser.error("Fresh timezone-aware cleanup authority required")
    output = subprocess.check_output(
        [args.terraform, f"-chdir={args.root.resolve()}", "show", "-json", str(args.plan.resolve())]
    )
    count = validate_plan(json.loads(output), inventory)
    record = {
        "scope": "reviewed_terraform_destroy_plan",
        "resources": count,
        "plan_sha256": args.approved_plan_sha256,
        "inventory_sha256": args.approved_inventory_sha256,
        "status": "REVIEWED_NOT_EXECUTED",
        "independent_absence": "not_established",
    }
    if args.execute:
        import boto3

        account = boto3.client("sts", region_name="ca-central-1").get_caller_identity()["Account"]
        if account != inventory["account"]:
            parser.error("Authenticated account differs from approved inventory")
        if args.plan.read_bytes() != plan_bytes or args.inventory.read_bytes() != inventory_bytes:
            parser.error("Reviewed inputs changed")
        args.record.parent.mkdir(parents=True, exist_ok=True)
        # Raw provider diagnostics stay in a private adjacent log, never public evidence.
        with args.record.with_suffix(".private.log").open("xb") as log:
            try:
                result = subprocess.run(
                    [
                        args.terraform,
                        f"-chdir={args.root.resolve()}",
                        "apply",
                        "-input=false",
                        str(args.plan.resolve()),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=3600,
                )
                record["status"] = "APPLY_RETURNED_ZERO" if result.returncode == 0 else "CLEANUP_FAILED"
            except subprocess.TimeoutExpired:
                record["status"] = "CLEANUP_TIMEOUT_STATE_UNKNOWN"
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))
    return 0 if record["status"] in ("REVIEWED_NOT_EXECUTED", "APPLY_RETURNED_ZERO") else 1


if __name__ == "__main__":
    raise SystemExit(main())
