"""Independent post-teardown reader. No deletion, secret retrieval or billing-clearance claim."""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

import boto3

from controlplane.native_inventory import Inventory, identity, public_report


def guard_inventory(m, session):
    prefix = identity(m["run"], m["account"])
    rows = []

    def add(kind, identifier, **attributes):
        rows.append({"kind": kind, "id": identifier, **attributes})

    scheduler, function = session.client("scheduler"), session.client("lambda")
    try:
        scheduler.get_schedule(Name=prefix + "-cleanup", GroupName="default")
        add("guard_schedule", prefix + "-cleanup")
    except scheduler.exceptions.ResourceNotFoundException:
        pass
    for name in ("watchdog", "checker"):
        try:
            function.get_function_configuration(FunctionName=prefix + "-" + name)
            add("guard_function", prefix + "-" + name)
        except function.exceptions.ResourceNotFoundException:
            pass
    logs = session.client("logs")
    for page in logs.get_paginator("describe_log_groups").paginate(
        logGroupNamePrefix="/aws/lambda/" + prefix + "-"
    ):
        for log in page["logGroups"]:
            if log["logGroupName"] in {"/aws/lambda/" + prefix + "-" + n for n in ("watchdog", "checker")}:
                add("guard_log_group", log["logGroupName"])
    iam = session.client("iam")
    for page in iam.get_paginator("list_roles").paginate(PathPrefix=f"/csa-native-guard/{m['run']}/"):
        for role in page["Roles"]:
            add("guard_role", role["RoleName"])
    queue = session.client("sqs")
    try:
        url = queue.get_queue_url(QueueName=prefix + "-cleanup-failures")["QueueUrl"]
        add(
            "guard_queue",
            url,
            attributes=queue.get_queue_attributes(
                QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
            )["Attributes"],
        )
    except queue.exceptions.QueueDoesNotExist:
        pass
    for alarm in session.client("cloudwatch").describe_alarms(AlarmNames=[prefix + "-cleanup-failures"])[
        "MetricAlarms"
    ]:
        add("guard_alarm", alarm["AlarmName"], state=alarm["StateValue"])
    key = session.client("kms")
    try:
        metadata = key.describe_key(KeyId=m["guard_key"])["KeyMetadata"]
        add("guard_key", metadata["KeyId"], state=metadata["KeyState"])
    except key.exceptions.NotFoundException:
        pass
    s3 = session.client("s3")
    # ListBuckets is metadata-only and avoids mistaking a 403 HeadBucket for absence.
    buckets = [b["Name"] for page in s3.get_paginator("list_buckets").paginate() for b in page["Buckets"]]
    bucket = f"{prefix}-guard-{m['account']}"
    if bucket in buckets:
        versions = sum(
            len(p.get("Versions", [])) + len(p.get("DeleteMarkers", []))
            for p in s3.get_paginator("list_object_versions").paginate(Bucket=bucket)
        )
        uploads = sum(
            len(p.get("Uploads", []))
            for p in s3.get_paginator("list_multipart_uploads").paginate(Bucket=bucket)
        )
        add("guard_bucket", bucket, versions_and_markers=versions, multipart_uploads=uploads)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument(
        "--registry",
        type=Path,
        required=True,
        help="Downloaded private inventory/registry.json, never public",
    )
    p.add_argument("--private-output", type=Path, required=True)
    p.add_argument("--public-output", type=Path, required=True)
    a = p.parse_args()
    if a.private_output.exists() or a.public_output.exists():
        p.error("Use fresh evidence paths")
    m, known = json.loads(a.manifest.read_text()), json.loads(a.registry.read_text())
    session = boto3.Session(region_name="us-east-2")
    clients = {
        s: session.client(s)
        for s in ("sts", "rds", "ecs", "ec2", "ecr", "logs", "kms", "iam", "secretsmanager")
    }
    runtime = Inventory(m["run"], m["account"], clients, known).collect()
    guard = guard_inventory(m, session)
    raw = {"observed_at": datetime.now(UTC).isoformat(), "runtime": runtime, "guard": guard}
    report = public_report(runtime)
    report["guard_residuals"] = public_report(guard)["residuals"]
    report["guard_resources_absent"] = not guard
    report["status"] = (
        "RESIDUALS_REMAIN" if runtime or guard else "RESOURCE_ABSENCE_OBSERVED_USAGE_REVIEW_PENDING"
    )
    for target, value in ((a.private_output, raw), (a.public_output, report)):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2, default=str) + "\n")
    print(report["status"])
    return int(bool(runtime or guard))


if __name__ == "__main__":
    raise SystemExit(main())
