"""Private exact/tagged native inventory. Unsupported/denied reads never mean absence."""

from datetime import UTC, datetime
import hashlib
import json
import os
import re

import boto3


def identity(run, account):
    if not re.fullmatch("[a-z0-9]{8,16}", run) or not re.fullmatch("[0-9]{12}", account):
        raise ValueError("Explicit isolated run/account required")
    return "csa-native-" + run


class Inventory:
    def __init__(self, run, account, clients, known=None):
        self.run, self.account, self.c = run, account, clients
        self.prefix = identity(run, account)
        self.rows = []
        self.known = known or []

    def pages(self, service, operation, key, **params):
        client = self.c[service]
        if client.can_paginate(operation):
            pages = client.get_paginator(operation).paginate(**params)
        else:
            pages = [getattr(client, operation)(**params)]
        rows = []
        for page in pages:
            rows.extend(page.get(key, []))
            if len(rows) > 1000:
                raise RuntimeError("Inventory budget exceeded; incomplete is not empty")
        return rows

    def add(self, kind, identifier, **attributes):
        self.rows.append({"kind": kind, "id": identifier, **attributes})

    def collect(self):
        self.rows = []
        if self.c["sts"].get_caller_identity()["Account"] != self.account:
            raise RuntimeError("Inventory account mismatch")
        names = {self.prefix + "-" + name for name in ("control", "receipts", "restore")}
        for db in self.pages("rds", "describe_db_instances", "DBInstances"):
            if db["DBInstanceIdentifier"] in names:
                self.add(
                    "database",
                    db["DBInstanceIdentifier"],
                    arn=db["DBInstanceArn"],
                    state=db["DBInstanceStatus"],
                    protected=db.get("DeletionProtection", False),
                    resource_id=db["DbiResourceId"],
                    owner_secret=db.get("MasterUserSecret", {}).get("SecretArn"),
                )
        for snap in self.pages("rds", "describe_db_snapshots", "DBSnapshots", SnapshotType="manual"):
            if snap["DBInstanceIdentifier"] in names:
                self.add("snapshot", snap["DBSnapshotIdentifier"], state=snap["Status"])
        for backup in self.pages(
            "rds", "describe_db_instance_automated_backups", "DBInstanceAutomatedBackups"
        ):
            if backup["DBInstanceIdentifier"] in names:
                arn = backup["DBInstanceAutomatedBackupsArn"]
                verified = False
                if backup["Status"] == "retained":
                    tags = self.c["rds"].list_tags_for_resource(ResourceName=arn)["TagList"]
                    verified = {t["Key"]: t["Value"] for t in tags}.get("AcceptanceRun") == self.run
                self.add("backup", backup["DbiResourceId"], state=backup["Status"], arn=arn,
                         run_tag_verified=verified)
        cluster = f"arn:aws:ecs:us-east-2:{self.account}:cluster/{self.prefix}"
        clusters = self.c["ecs"].describe_clusters(clusters=[cluster]).get("clusters", [])
        for value in clusters:
            if value["status"] != "INACTIVE":
                self.add("cluster", cluster)
                for task in self.pages("ecs", "list_tasks", "taskArns", cluster=cluster):
                    self.add("task", task, cluster=cluster)
        for status in ("ACTIVE", "INACTIVE", "DELETE_IN_PROGRESS"):
            # ListTaskDefinitions requires a full family, despite its familyPrefix name.
            # The namespace alone returns no revisions and would falsely imply absence.
            for name in ("bootstrap", "baseline", "control", "receipt", "verify"):
                family = self.prefix + "-" + name
                for definition in self.pages(
                    "ecs", "list_task_definitions", "taskDefinitionArns", familyPrefix=family, status=status
                ):
                    if definition.split("task-definition/")[1].split(":")[0] != family:
                        raise RuntimeError("Task definition family mismatch")
                    self.add("task_definition", definition, state=status)
        for repo in self.pages("ecr", "describe_repositories", "repositories"):
            if repo["repositoryName"] == self.prefix:
                images = self.pages("ecr", "describe_images", "imageDetails", repositoryName=self.prefix)
                self.add("repository", self.prefix, image_digests=[i["imageDigest"] for i in images])
        owner_secrets = {r.get("owner_secret") for r in self.known + self.rows if r["kind"] == "database"} - {
            None
        }
        for secret in self.pages("secretsmanager", "list_secrets", "SecretList", IncludePlannedDeletion=True):
            tags = {t["Key"]: t["Value"] for t in secret.get("Tags", [])}
            if tags.get("AcceptanceRun") == self.run or secret["ARN"] in owner_secrets:
                self.add(
                    "secret",
                    secret["ARN"],
                    managed=secret["ARN"] in owner_secrets,
                    state="pending_deletion" if secret.get("DeletedDate") else "active",
                )
        # Managed RDS owner secrets are recorded before deletion and archived in the guard bucket.
        for prefix in (f"/csa-native/{self.run}/", f"/aws/rds/instance/{self.prefix}-"):
            for log in self.pages("logs", "describe_log_groups", "logGroups", logGroupNamePrefix=prefix):
                if log["logGroupName"].startswith(f"/csa-native/{self.run}/") or log["logGroupName"] in {
                    f"/aws/rds/instance/{name}/postgresql" for name in names
                }:
                    self.add("log_group", log["logGroupName"])
        for key in self.pages("kms", "list_keys", "Keys"):
            metadata = self.c["kms"].describe_key(KeyId=key["KeyId"])["KeyMetadata"]
            if metadata.get("KeyManager") != "CUSTOMER":
                continue
            tags = self.c["kms"].list_resource_tags(KeyId=key["KeyId"]).get("Tags", [])
            if {t["TagKey"]: t["TagValue"] for t in tags}.get("AcceptanceRun") == self.run:
                self.add("key", key["KeyId"], state=metadata["KeyState"], arn=metadata["Arn"])
        filters = [{"Name": "tag:AcceptanceRun", "Values": [self.run]}]
        owned_vpcs = self.pages("ec2", "describe_vpcs", "Vpcs", Filters=filters)
        vpc_ids = [v["VpcId"] for v in owned_vpcs]
        for operation, key, kind, identifier in [
            ("describe_vpc_endpoints", "VpcEndpoints", "endpoint", "VpcEndpointId"),
            ("describe_network_interfaces", "NetworkInterfaces", "eni", "NetworkInterfaceId"),
            ("describe_route_tables", "RouteTables", "route_table", "RouteTableId"),
            ("describe_security_groups", "SecurityGroups", "security_group", "GroupId"),
            ("describe_subnets", "Subnets", "subnet", "SubnetId"),
            ("describe_vpcs", "Vpcs", "vpc", "VpcId"),
        ]:
            query = [{"Name": "vpc-id", "Values": vpc_ids}] if kind == "eni" and vpc_ids else filters
            for row in self.pages("ec2", operation, key, Filters=query):
                if kind == "vpc" and row.get("IsDefault"):
                    raise RuntimeError("Never operate on a default VPC")
                self.add(
                    kind,
                    row[identifier],
                    state=row.get("State", row.get("Status", "present")),
                    vpc=row.get("VpcId"),
                    associations=row.get("Associations", []),
                    requester_managed=row.get("RequesterManaged", False),
                    ingress=row.get("IpPermissions", []),
                    egress=row.get("IpPermissionsEgress", []),
                )
        for group in self.pages("rds", "describe_db_subnet_groups", "DBSubnetGroups"):
            if group["DBSubnetGroupName"] == self.prefix:
                self.add("db_subnet_group", self.prefix)
        for group in self.pages("rds", "describe_db_parameter_groups", "DBParameterGroups"):
            if group["DBParameterGroupName"] == self.prefix:
                self.add("db_parameter_group", self.prefix)
        for role in self.pages("iam", "list_roles", "Roles", PathPrefix=f"/csa-native/{self.run}/"):
            self.add("role", role["RoleName"])
        return self.rows


def public_report(rows):
    runtime_can_execute = any(r["kind"] in {"cluster", "task"} for r in rows)
    residuals = [
        {
            "kind": row["kind"],
            "reference": hashlib.sha256(row["id"].encode()).hexdigest(),
            "state": str(row.get("state", "present")),
        }
        for row in rows
    ]
    live = [
        r
        for r in rows
        if not (r["kind"] == "key" and r.get("state") == "PendingDeletion")
        and not (r["kind"] == "secret" and r.get("state") == "pending_deletion")
        # AWS can retain deleting definition metadata after compute is gone. It cannot
        # launch new tasks; never ignore it while an existing cluster/task remains.
        and not (r["kind"] == "task_definition" and r.get("state") == "DELETE_IN_PROGRESS"
                 and not runtime_can_execute)
    ]
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "scope": "isolated_runtime_inventory_not_billing_bill",
        "active_resources_absent": not live,
        "all_resources_absent": not rows,
        "residuals": residuals,
        "billing_clearance": "requires_delayed_usage_and_guard_inventory_review",
    }


def handler(event, context):
    run, account = os.environ["RUN_ID"], os.environ["ACCOUNT_ID"]
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
    report = public_report(Inventory(run, account, clients, known).collect())
    # Independent checker cannot mutate runtime resources. It can write only its evidence prefix.
    s3.put_object(
        Bucket=os.environ["GUARD_BUCKET"],
        Key="checks/" + datetime.now(UTC).isoformat() + ".json",
        Body=json.dumps(report).encode(),
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=os.environ["GUARD_KEY_ARN"],
    )
    return report
