"""Archive and retire only an independently emptied acceptance guard, after explicit approval.

The local output is private. A subsequent native_post_teardown read is still mandatory.
"""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import boto3

from controlplane.native_inventory import Inventory, identity, public_report


def finalize(manifest, session, output, known):
    prefix = identity(manifest["run"], manifest["account"])
    clients = {
        s: session.client(s)
        for s in ("sts", "rds", "ecs", "ec2", "ecr", "logs", "kms", "iam", "secretsmanager")
    }
    runtime = Inventory(manifest["run"], manifest["account"], clients, known).collect()
    if not public_report(runtime)["active_resources_absent"]:
        raise RuntimeError("Independent runtime absence required before retiring guard")
    if output.exists():
        raise ValueError("Fresh private archive directory required")
    bucket = f"{prefix}-guard-{manifest['account']}"
    if manifest["guard_bucket"] != bucket:
        raise ValueError("Guard bucket identity mismatch")
    kms = clients["kms"]
    key = kms.describe_key(KeyId=manifest["guard_key"])["KeyMetadata"]
    tags = kms.list_resource_tags(KeyId=manifest["guard_key"])["Tags"]
    if (
        key["AWSAccountId"] != manifest["account"]
        or {t["TagKey"]: t["TagValue"] for t in tags}.get("AcceptanceGuard") != manifest["run"]
    ):
        raise ValueError("Guard key ownership mismatch")
    s3 = session.client("s3")
    versions = []
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
        versions.extend({**v, "marker": False} for v in page.get("Versions", []))
        versions.extend({**v, "marker": True} for v in page.get("DeleteMarkers", []))
        if len(versions) > 2000 or sum(v.get("Size", 0) for v in versions) > 100_000_000:
            raise RuntimeError("Private archive budget exceeded; leave guard armed for operator review")
    output.mkdir(parents=True)
    archived = []
    for number, version in enumerate(versions):
        row = {"key": version["Key"], "version": version["VersionId"], "marker": version["marker"]}
        if not version["marker"]:
            with s3.get_object(Bucket=bucket, Key=version["Key"], VersionId=version["VersionId"])[
                "Body"
            ] as body:
                data = body.read(100_000_001)
            if len(data) != version["Size"]:
                raise RuntimeError("Incomplete archive; no deletion authorized")
            target = output / f"{number:04}.bin"
            target.write_bytes(data)
            if target.read_bytes() != data:
                raise RuntimeError("Archive read-back mismatch")
            row.update(file=target.name, sha256=hashlib.sha256(data).hexdigest())
        archived.append(row)
    (output / "private-archive.json").write_text(json.dumps(archived, indent=2) + "\n")
    # Stop new scheduled invocations, then allow running bounded functions to finish before bucket drain.
    schedule = session.client("scheduler")
    schedule.delete_schedule(Name=prefix + "-cleanup", GroupName="default")
    function = session.client("lambda")
    for name in ("watchdog", "checker"):
        function.put_function_concurrency(FunctionName=prefix + "-" + name, ReservedConcurrentExecutions=0)
    import time

    time.sleep(125)
    # A new archive requires a new approval attempt; never silently delete an unarchived late write.
    current = []
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
        current.extend(
            (v["Key"], v["VersionId"]) for key in ("Versions", "DeleteMarkers") for v in page.get(key, [])
        )
    if set(current) != {(v["Key"], v["VersionId"]) for v in versions}:
        raise RuntimeError("Late guard evidence detected; archive again before finalization")
    for start in range(0, len(versions), 1000):
        result = s3.delete_objects(
            Bucket=bucket,
            Delete={
                "Objects": [
                    {"Key": v["Key"], "VersionId": v["VersionId"]} for v in versions[start : start + 1000]
                ]
            },
        )
        if result.get("Errors"):
            raise RuntimeError("Version deletion incomplete")
    for page in s3.get_paginator("list_multipart_uploads").paginate(Bucket=bucket):
        for upload in page.get("Uploads", []):
            s3.abort_multipart_upload(Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"])
    s3.delete_bucket(Bucket=bucket)
    for name in ("watchdog", "checker"):
        function.delete_function(FunctionName=prefix + "-" + name)
        clients["logs"].delete_log_group(logGroupName="/aws/lambda/" + prefix + "-" + name)
    queue = session.client("sqs")
    url = queue.get_queue_url(QueueName=prefix + "-cleanup-failures")["QueueUrl"]
    queue.delete_queue(QueueUrl=url)
    session.client("cloudwatch").delete_alarms(AlarmNames=[prefix + "-cleanup-failures"])
    iam = clients["iam"]
    for name in ("watchdog", "checker", "schedule"):
        role = iam.get_role(RoleName=prefix + "-" + name)["Role"]
        if role["Path"] != f"/csa-native-guard/{manifest['run']}/":
            raise ValueError("Guard role scope mismatch")
        for page in iam.get_paginator("list_role_policies").paginate(RoleName=role["RoleName"]):
            for policy in page["PolicyNames"]:
                iam.delete_role_policy(RoleName=role["RoleName"], PolicyName=policy)
        iam.delete_role(RoleName=role["RoleName"])
    kms.schedule_key_deletion(KeyId=manifest["guard_key"], PendingWindowInDays=7)
    return {
        "status": "GUARD_RETIREMENT_REQUESTED_INDEPENDENT_RECHECK_REQUIRED",
        "at": datetime.now(UTC).isoformat(),
        "key_deletion_days": 7,
        "billing_clearance": False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--approved-sha256", required=True)
    p.add_argument("--private-archive", type=Path, required=True)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    raw = a.manifest.read_bytes()
    if not a.execute or hashlib.sha256(raw).hexdigest() != a.approved_sha256:
        p.error("Explicit approved guard manifest digest required")
    print(
        json.dumps(
            finalize(
                json.loads(raw),
                boto3.Session(region_name="us-east-2"),
                a.private_archive,
                json.loads(a.registry.read_text()),
            )
        )
    )


if __name__ == "__main__":
    main()
