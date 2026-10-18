"""Independent read-only checks of exact billable resource identities, not Terraform state."""

import argparse
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from .release import reference

# Only an explicit service-specific absence establishes deletion. AccessDenied never does.
PROBES = {
    "aws_db_instance": ("rds", "describe_db_instances", "DBInstanceIdentifier", "DBInstanceNotFound"),
    "aws_lb": ("elbv2", "describe_load_balancers", "LoadBalancerArns", "LoadBalancerNotFound"),
    "aws_ecr_repository": ("ecr", "describe_repositories", "repositoryNames", "RepositoryNotFoundException"),
    "aws_cloudwatch_log_group": ("logs", "describe_log_groups", "logGroupNamePrefix", None),
    "aws_vpc_endpoint": ("ec2", "describe_vpc_endpoints", "VpcEndpointIds", "InvalidVpcEndpointId.NotFound"),
}


def check(resources, clients):
    results = []
    for item in resources:
        kind, identity = item["type"], item["id"]
        status = "NOT_CHECKED_UNSUPPORTED"
        if kind in PROBES:
            service, method, key, missing = PROBES[kind]
            argument = (
                [identity] if key in ("LoadBalancerArns", "repositoryNames", "VpcEndpointIds") else identity
            )
            try:
                result = getattr(clients[service], method)(**{key: argument})
                if kind == "aws_cloudwatch_log_group":
                    if result.get("nextToken"):
                        status = "INCOMPLETE_PAGINATION"
                    else:
                        status = (
                            "PRESENT"
                            if any(g["logGroupName"] == identity for g in result.get("logGroups", []))
                            else "ABSENT"
                        )
                elif kind == "aws_vpc_endpoint" and not result.get("VpcEndpoints"):
                    status = "ABSENT"
                else:
                    status = "PRESENT"
            except ClientError as exc:
                status = "ABSENT" if exc.response["Error"]["Code"] == missing else "READ_FAILED"
        results.append({"type": kind, "resource_reference": reference(identity), "status": status})
    return {
        "scope": "exact_inventory_independent_reads_not_account_wide_billing_clearance",
        "results": results,
        "all_checked_resources_absent": bool(results) and all(r["status"] == "ABSENT" for r in results),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    if args.record.exists():
        parser.error("Preserve previous observations")
    inventory = json.loads(args.inventory.read_text())
    if inventory.get("environment") != "stage" or inventory.get("region") != "ca-central-1":
        parser.error("Only the isolated stage account is eligible")
    session = boto3.Session(region_name="ca-central-1")
    account = session.client("sts").get_caller_identity()["Account"]
    if account != inventory["account"]:
        parser.error("Authenticated account differs from inventory")
    clients = {s: session.client(s) for s in {p[0] for p in PROBES.values()}}
    result = check(list(inventory["resources"].values()), clients)
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"all_checked_resources_absent": result["all_checked_resources_absent"]}))
    return 0 if result["all_checked_resources_absent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
