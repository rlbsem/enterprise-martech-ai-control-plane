"""Initialize an empty guard registry without expanding either Lambda's S3 permissions."""

import argparse
import hashlib
import json
from pathlib import Path

import boto3

from controlplane.native_arming import armed_gate
from controlplane.native_inventory import Inventory, public_report


def initialize_registry(manifest, session):
    gate_clients = {
        name: session.client(name, region_name="us-east-1" if name == "freetier" else "us-east-2")
        for name in ("sts", "freetier", "scheduler", "lambda")
    }
    armed_gate(manifest, gate_clients)
    bucket = f"csa-native-{manifest['run']}-guard-{manifest['account']}"
    if manifest["guard_bucket"] != bucket:
        raise ValueError("Exact guard bucket identity required")
    s3 = session.client("s3")
    try:
        with s3.get_object(Bucket=bucket, Key="inventory/registry.json")["Body"] as body:
            data = body.read(2_000_001)
        if len(data) > 2_000_000 or not isinstance(json.loads(data), list):
            raise ValueError("Invalid existing registry; never replace it")
        return {"status": "EXISTING_REGISTRY_PRESERVED"}
    except s3.exceptions.NoSuchKey:
        pass
    clients = {
        name: session.client(name)
        for name in ("sts", "rds", "ecs", "ec2", "ecr", "logs", "kms", "iam", "secretsmanager")
    }
    rows = Inventory(manifest["run"], manifest["account"], clients, []).collect()
    if rows:
        raise RuntimeError("Fresh runtime namespace must be empty before registry initialization")
    # Missing keys return AccessDenied to restricted readers without ListBucket. Seed only
    # after independent discovery, without granting either function broader bucket access.
    response = s3.put_object(
        Bucket=bucket, Key="inventory/registry.json", Body=b"[]", IfNoneMatch="*",
        ServerSideEncryption="aws:kms", SSEKMSKeyId=manifest["guard_key"],
    )
    with s3.get_object(
        Bucket=bucket, Key="inventory/registry.json", VersionId=response["VersionId"]
    )["Body"] as body:
        if body.read(3) != b"[]":
            raise RuntimeError("Registry exact-content read-back failed")
    return {"status": "EMPTY_REGISTRY_INITIALIZED", "inventory": public_report(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approved-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    if not args.execute or hashlib.sha256(raw).hexdigest() != args.approved_sha256:
        parser.error("Explicit reviewed guard-manifest digest required")
    print(json.dumps(initialize_registry(json.loads(raw), boto3.Session(region_name="us-east-2"))))


if __name__ == "__main__":
    main()
