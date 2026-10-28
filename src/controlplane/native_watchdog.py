"""Cloud-side bounded cleanup passes. Never accept a caller-supplied resource target."""

from datetime import UTC, datetime
import hashlib
import json
import os

import boto3
from botocore.exceptions import ClientError

from .native_inventory import Inventory, public_report


def archive_logs(logs, storage, group, bucket, key):
    """Read back a bounded private archive before deleting a run-owned log group."""
    streams = []
    for page in logs.get_paginator("describe_log_streams").paginate(logGroupName=group):
        streams.extend(page["logStreams"])
        if len(streams) > 20:
            raise RuntimeError("Private log stream archive budget exceeded")
    captured, used = [], 0
    for stream in streams:
        events, token = [], None
        for _ in range(100):
            response = logs.get_log_events(
                logGroupName=group, logStreamName=stream["logStreamName"], startFromHead=True,
                **({"nextToken": token} if token else {}),
            )
            used += len(json.dumps(response["events"]).encode())
            if used > 2_000_000:
                raise RuntimeError("Private log byte archive budget exceeded")
            events.extend(response["events"])
            next_token = response["nextForwardToken"]
            if next_token == token:
                break
            token = next_token
        else:
            raise RuntimeError("Private log page archive budget exceeded")
        captured.append({"stream": stream["logStreamName"], "events": events})
    raw = json.dumps({"group": group, "streams": captured}, sort_keys=True).encode()
    if len(raw) > 2_000_000:
        raise RuntimeError("Private log byte archive budget exceeded")
    digest = hashlib.sha256(raw).hexdigest()
    target = "task-logs/" + digest + ".json"
    try:
        storage.put_object(Bucket=bucket, Key=target, Body=raw, IfNoneMatch="*",
                           ServerSideEncryption="aws:kms", SSEKMSKeyId=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "PreconditionFailed":
            raise
    with storage.get_object(Bucket=bucket, Key=target)["Body"] as body:
        if body.read(len(raw) + 1) != raw:
            raise RuntimeError("Private log archive readback failed")
    return digest


def cleanup_pass(inventory, clients, archive_log_group=None):
    rows = inventory.collect()  # Complete independent discovery precedes every mutation pass.
    actions, failures, failure_details = [], [], []
    kinds = {r["kind"] for r in rows}
    for row in rows:
        kind, identifier = row["kind"], row["id"]
        try:
            if kind == "task":
                clients["ecs"].stop_task(
                    cluster=row["cluster"], task=identifier, reason="Approved acceptance deadline"
                )
            elif "task" in kinds:
                continue
            elif kind == "database":
                if row["state"] == "deleting":
                    continue
                if row["protected"]:
                    clients["rds"].modify_db_instance(
                        DBInstanceIdentifier=identifier, DeletionProtection=False, ApplyImmediately=True
                    )
                elif row["state"] == "available":
                    clients["rds"].delete_db_instance(
                        DBInstanceIdentifier=identifier, SkipFinalSnapshot=True, DeleteAutomatedBackups=True
                    )
            elif kind == "snapshot" and row["state"] == "available":
                clients["rds"].delete_db_snapshot(DBSnapshotIdentifier=identifier)
            elif kind == "backup" and row["state"] == "retained":
                if not row.get("run_tag_verified"):
                    raise RuntimeError("Retained backup cleanup ownership not established")
                clients["rds"].delete_db_instance_automated_backup(DbiResourceId=identifier)
            elif kind == "repository":
                clients["ecr"].delete_repository(repositoryName=identifier, force=True)
            elif kind == "secret" and row["state"] == "active" and not row.get("managed"):
                clients["secretsmanager"].delete_secret(SecretId=identifier, RecoveryWindowInDays=7)
            elif kind == "log_group" and "database" not in kinds:
                if archive_log_group is not None:
                    archive_log_group(identifier)
                clients["logs"].delete_log_group(logGroupName=identifier)
            elif kind == "endpoint":
                response = clients["ec2"].delete_vpc_endpoints(VpcEndpointIds=[identifier])
                if response.get("Unsuccessful"):
                    raise RuntimeError("Endpoint deletion incomplete")
            elif (
                kind == "eni"
                and not row["requester_managed"]
                and "database" not in kinds
                and "endpoint" not in kinds
            ):
                clients["ec2"].delete_network_interface(NetworkInterfaceId=identifier)
            elif kind == "route_table" and "endpoint" not in kinds:
                for association in row["associations"]:
                    if not association.get("Main"):
                        clients["ec2"].disassociate_route_table(
                            AssociationId=association["RouteTableAssociationId"]
                        )
                if not any(a.get("Main") for a in row["associations"]):
                    clients["ec2"].delete_route_table(RouteTableId=identifier)
            elif kind == "security_group" and not {"database", "endpoint", "eni"} & kinds:
                for direction, operation in [
                    ("ingress", "revoke_security_group_ingress"),
                    ("egress", "revoke_security_group_egress"),
                ]:
                    if row.get(direction):
                        getattr(clients["ec2"], operation)(GroupId=identifier, IpPermissions=row[direction])
                clients["ec2"].delete_security_group(GroupId=identifier)
            elif kind == "subnet" and not {"database", "endpoint", "eni"} & kinds:
                clients["ec2"].delete_subnet(SubnetId=identifier)
            elif kind == "vpc" and not {"database", "endpoint", "eni", "subnet", "security_group"} & kinds:
                clients["ec2"].delete_vpc(VpcId=identifier)
            elif kind == "db_subnet_group" and "database" not in kinds:
                clients["rds"].delete_db_subnet_group(DBSubnetGroupName=identifier)
            elif kind == "db_parameter_group" and "database" not in kinds:
                clients["rds"].delete_db_parameter_group(DBParameterGroupName=identifier)
            elif kind == "task_definition":
                if row["state"] == "DELETE_IN_PROGRESS":
                    continue
                if row["state"] == "ACTIVE":
                    clients["ecs"].deregister_task_definition(taskDefinition=identifier)
                else:
                    response = clients["ecs"].delete_task_definitions(taskDefinitions=[identifier])
                    if response.get("failures"):
                        raise RuntimeError("Inactive task definition deletion incomplete")
            elif kind == "cluster":
                clients["ecs"].delete_cluster(cluster=identifier)
            elif kind == "role":
                for policy in clients["iam"].list_role_policies(RoleName=identifier)["PolicyNames"]:
                    clients["iam"].delete_role_policy(RoleName=identifier, PolicyName=policy)
                clients["iam"].delete_role(RoleName=identifier)
            elif (
                kind == "key"
                and row["state"] != "PendingDeletion"
                and not {"database", "backup", "snapshot"} & kinds
            ):
                clients["kms"].schedule_key_deletion(KeyId=identifier, PendingWindowInDays=7)
            else:
                continue
            actions.append(kind)
        except Exception as exc:
            # Dependency races are retried by subsequent scheduled passes; a denied read never becomes absence.
            failures.append(kind)
            failure_details.append({
                "kind": kind,
                "resource_reference": hashlib.sha256(identifier.encode()).hexdigest(),
                "error_type": type(exc).__name__,
                "aws_code": getattr(exc, "response", {}).get("Error", {}).get("Code"),
                "request_id": getattr(exc, "response", {}).get("ResponseMetadata", {}).get("RequestId"),
                "operation": getattr(exc, "operation_name", None),
            })
    return {"attempted_kinds": actions, "failed_kinds": failures,
            "failure_details": failure_details, "before": public_report(rows)}


def handler(event, context):
    run, account = os.environ["RUN_ID"], os.environ["ACCOUNT_ID"]
    deadline = datetime.fromisoformat(os.environ["CLEANUP_AT"])
    if datetime.now(UTC) < deadline:
        return {"status": "ARMED_NO_MUTATION", "run": run}
    if os.environ["DISPOSITION"] != "discard_isolated_synthetic_state":
        raise ValueError("Explicit isolated synthetic cleanup disposition required")
    session = boto3.Session(region_name="us-east-2")
    clients = {
        s: session.client(s)
        for s in ("sts", "rds", "ecs", "ec2", "ecr", "logs", "kms", "iam", "secretsmanager")
    }
    s3 = session.client("s3")
    try:
        with s3.get_object(Bucket=os.environ["GUARD_BUCKET"], Key="inventory/registry.json")["Body"] as body:
            known = json.loads(body.read(2000000))
    except s3.exceptions.NoSuchKey:
        known = []
    inventory = Inventory(run, account, clients, known)
    rows = inventory.collect()
    registry = {(r["kind"], r["id"]): r for r in known + rows}
    s3.put_object(
        Bucket=os.environ["GUARD_BUCKET"],
        Key="inventory/registry.json",
        Body=json.dumps(list(registry.values()), default=str).encode(),
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=os.environ["GUARD_KEY_ARN"],
    )
    # Archive exact private inventory before mutations, including owner-secret and database resource IDs.
    s3.put_object(
        Bucket=os.environ["GUARD_BUCKET"],
        Key="inventory/" + datetime.now(UTC).isoformat() + ".json",
        Body=json.dumps(rows, default=str).encode(),
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=os.environ["GUARD_KEY_ARN"],
    )
    result = cleanup_pass(inventory, clients, lambda group: archive_logs(
        clients["logs"], s3, group, os.environ["GUARD_BUCKET"], os.environ["GUARD_KEY_ARN"]
    ))
    s3.put_object(
        Bucket=os.environ["GUARD_BUCKET"],
        Key="cleanup/" + datetime.now(UTC).isoformat() + ".json",
        Body=json.dumps(result).encode(),
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=os.environ["GUARD_KEY_ARN"],
    )
    # An independently scoped function observes effects, not this mutation function's return value.
    session.client("lambda").invoke(
        FunctionName=os.environ["CHECKER_ARN"], InvocationType="Event", Payload=b"{}"
    )
    if result["failed_kinds"]:
        raise RuntimeError("Cleanup incomplete; private evidence retained and scheduled retry required")
    return result
