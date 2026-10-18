"""Read-only native AWS witness. Does not apply Terraform, launch, stop or restore resources.

SDK stubs establish this collector's contract, not a deployed environment. Runtime
collection requires separately approved, isolated resources and authorized credentials.
"""

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import boto3

from .release import reference, validate_manifest


class AcceptanceBlocked(RuntimeError):
    pass


def pinned_manifest(manifest):
    validate_manifest(manifest)
    account = manifest["image"].split(".", 1)[0]
    environment = manifest.get("environment")
    if environment not in ("dev", "stage"):
        raise AcceptanceBlocked("Isolated dev or stage environment required")
    name = "control-" + environment
    if manifest["cluster"] != f"arn:aws:ecs:ca-central-1:{account}:cluster/{name}":
        raise AcceptanceBlocked("Cluster identity does not match isolated release")
    for service, value in manifest["services"].items():
        if value != f"{name}-{service}":
            raise AcceptanceBlocked("Service outside the pinned environment")
    for service, value in manifest["tasks"].items():
        if not re.fullmatch(
            f"arn:aws:ecs:ca-central-1:{account}:task-definition/{name}-{service}:[1-9][0-9]*", value
        ):
            raise AcceptanceBlocked("Task revision outside the pinned environment")
    for key in ("control_db", "downstream_db"):
        value = manifest.get(key, "")
        allowed = (f"{name}-control",) if key == "control_db" else (f"{name}-downstream",)
        if value not in allowed and not (
            key == "control_db" and re.fullmatch(f"{name}-restore-[a-z0-9-]{{1,30}}", value)
        ):
            raise AcceptanceBlocked("Database outside the pinned environment")
    if (
        set(manifest.get("groups", {})) != set(manifest["tasks"])
        or any(not re.fullmatch(r"sg-[0-9a-f]{8,17}", v) for v in manifest["groups"].values())
        or len(manifest["subnets"]) != 2
        or len(set(manifest["subnets"])) != 2
        or any(not re.fullmatch(r"subnet-[0-9a-f]{8,17}", v) for v in manifest["subnets"])
    ):
        raise AcceptanceBlocked("Pinned private network inventory required")
    return account


class Witness:
    """Only descriptive SDK methods; private identifiers never enter the public report."""

    def __init__(self, manifest, clients):
        self.m, self.clients = manifest, clients
        self.account = pinned_manifest(manifest)
        self.calls = []
        self.quiescence_verified = False

    def call(self, service, operation, **kwargs):
        allowed = {
            "sts": {"get_caller_identity"},
            "ecs": {"describe_services", "describe_tasks", "describe_task_definition", "list_tasks"},
            "rds": {"describe_db_instances"},
            "logs": {"get_log_events"},
        }
        if operation not in allowed.get(service, set()) or len(self.calls) >= 40:
            raise AcceptanceBlocked("Read-only request budget or operation boundary exceeded")
        event = {"service": service, "operation": operation, "status": "failed"}
        try:
            result = getattr(self.clients[service], operation)(**kwargs)
            event["status"] = "succeeded"
            request = result.get("ResponseMetadata", {}).get("RequestId")
            if request:
                event["request_reference"] = reference(request)
            return result
        finally:
            self.calls.append(event)

    def identity(self):
        observed = self.call("sts", "get_caller_identity")
        if observed.get("Account") != self.account:
            raise AcceptanceBlocked("Authenticated account does not match manifest")
        return {"account_reference": reference(self.account), "account_pin_verified": True}

    def runtime(self, *, quiesced=False):
        result = self.call(
            "ecs", "describe_services", cluster=self.m["cluster"], services=list(self.m["services"].values())
        )
        services = result.get("services", [])
        if result.get("failures") or len(services) != 4:
            raise AcceptanceBlocked("Service observation incomplete")
        seen, roles = set(), set()
        for name, expected in self.m["services"].items():
            matches = [s for s in services if s.get("serviceName") == expected]
            if len(matches) != 1:
                raise AcceptanceBlocked("Ambiguous service identity")
            s = matches[0]
            desired = 0 if quiesced else self.m["replicas"][name]
            if (
                s.get("status") != "ACTIVE"
                or s.get("clusterArn") != self.m["cluster"]
                or s.get("desiredCount") != desired
                or s.get("runningCount") != desired
                or s.get("pendingCount") != 0
                or s.get("taskDefinition") != self.m["tasks"][name]
            ):
                raise AcceptanceBlocked("Service counts or exact release revision not established")
            network = s.get("networkConfiguration", {}).get("awsvpcConfiguration", {})
            if (
                network.get("assignPublicIp") != "DISABLED"
                or set(network.get("subnets", [])) != set(self.m["subnets"])
                or network.get("securityGroups") != [self.m["groups"][name]]
            ):
                raise AcceptanceBlocked("Service network boundary differs from manifest")
            if not quiesced:
                listed = self.call(
                    "ecs",
                    "list_tasks",
                    cluster=self.m["cluster"],
                    serviceName=expected,
                    desiredStatus="RUNNING",
                )
                if listed.get("nextToken") or len(listed.get("taskArns", [])) != desired:
                    raise AcceptanceBlocked("Running task inventory incomplete")
                tasks = self.call(
                    "ecs", "describe_tasks", cluster=self.m["cluster"], tasks=listed["taskArns"]
                )
                if tasks.get("failures") or len(tasks.get("tasks", [])) != desired:
                    raise AcceptanceBlocked("Task observation incomplete")
                for task in tasks["tasks"]:
                    if (
                        task.get("taskArn") not in listed["taskArns"]
                        or task["taskArn"] in seen
                        or task.get("lastStatus") != "RUNNING"
                        or task.get("taskDefinitionArn") != self.m["tasks"][name]
                        or len(task.get("containers", [])) != 1
                        or task["containers"][0].get("imageDigest") != self.m["image"].rsplit("@", 1)[1]
                    ):
                        raise AcceptanceBlocked("Running image or task identity mismatch")
                    seen.add(task["taskArn"])
        for name, arn in self.m["tasks"].items():
            definition = self.call("ecs", "describe_task_definition", taskDefinition=arn)["taskDefinition"]
            containers = definition.get("containerDefinitions", [])
            if (
                len(containers) != 1
                or containers[0].get("image") != self.m["image"]
                or containers[0].get("entryPoint")
                or containers[0].get("command") != ["python", "-m", "controlplane.cloud", name]
                or {v["name"]: v["value"] for v in containers[0].get("environment", [])}.get("SOURCE_COMMIT")
                != self.m["commit"]
            ):
                raise AcceptanceBlocked("Observed task definition differs from approved source")
            role = definition.get("taskRoleArn")
            if (
                not role
                or role in roles
                or not role.startswith(f"arn:aws:iam::{self.account}:role/")
                or definition.get("networkMode") != "awsvpc"
                or "FARGATE" not in definition.get("requiresCompatibilities", [])
            ):
                raise AcceptanceBlocked("Distinct service IAM role or Fargate boundary not established")
            roles.add(role)
        if quiesced:
            tasks = self.call("ecs", "list_tasks", cluster=self.m["cluster"], desiredStatus="RUNNING")
            if tasks.get("taskArns") or tasks.get("nextToken"):
                raise AcceptanceBlocked("One-off tasks prevent a quiescent recovery boundary")
        self.quiescence_verified = quiesced
        return {
            "services_checked": 4,
            "task_roles_distinct": 6,
            "running_tasks_checked": len(seen),
            "quiesced": quiesced,
            "private_network_matches_manifest": True,
            "iam_policy_effectiveness": "requires_separate_denial_experiments",
        }

    def databases(self):
        references = set()
        for key in ("control_db", "downstream_db"):
            response = self.call("rds", "describe_db_instances", DBInstanceIdentifier=self.m[key])
            rows = response.get("DBInstances", [])
            if len(rows) != 1:
                raise AcceptanceBlocked("Database observation incomplete")
            db = rows[0]
            if (
                db.get("DBInstanceIdentifier") != self.m[key]
                or db.get("DBInstanceStatus") != "available"
                or db.get("Engine") != "postgres"
                or not str(db.get("EngineVersion", "")).startswith("17.")
                or db.get("PubliclyAccessible") is not False
                or db.get("StorageEncrypted") is not True
                or db.get("MultiAZ") is not True
                or db.get("DeletionProtection") is not True
                or db.get("BackupRetentionPeriod", 0) < 14
                or not db.get("MasterUserSecret", {}).get("SecretArn")
                or not db.get("DbiResourceId")
                or db["DbiResourceId"] in references
            ):
                raise AcceptanceBlocked("Independent private managed database boundary not established")
            references.add(db["DbiResourceId"])
        return {
            "independent_databases": 2,
            "postgres_major": 17,
            "private_encrypted_multi_az": True,
            "managed_owner_secrets": True,
            "backup_retention_at_least_days": 14,
            "native_pitr": "not_established_by_configuration",
        }

    def recovery(self, task_arn):
        # Obtain the report from the actual task's exact log stream, never a supplied JSON pass.
        if not self.quiescence_verified:
            raise AcceptanceBlocked("Fresh quiescence observation required")
        response = self.call("ecs", "describe_tasks", cluster=self.m["cluster"], tasks=[task_arn])
        tasks = response.get("tasks", [])
        if response.get("failures") or len(tasks) != 1:
            raise AcceptanceBlocked("Verifier task unavailable")
        task = tasks[0]
        containers = task.get("containers", [])
        if (
            task.get("taskArn") != task_arn
            or task.get("taskDefinitionArn") != self.m["tasks"]["verifier"]
            or task.get("lastStatus") != "STOPPED"
            or len(containers) != 1
            or containers[0].get("exitCode") != 0
            or containers[0].get("imageDigest") != self.m["image"].rsplit("@", 1)[1]
        ):
            raise AcceptanceBlocked("Exact stopped verifier image and success not established")
        group = self.m.get("log_groups", {}).get("verifier")
        if group != "/enterprise/control-" + self.m["environment"] + "/verifier":
            raise AcceptanceBlocked("Verifier log group not pinned")
        records = self.call(
            "logs",
            "get_log_events",
            logGroupName=group,
            logStreamName="task/verifier/" + task_arn.rsplit("/", 1)[-1],
            startFromHead=True,
            limit=1000,
        )
        if len(records.get("events", [])) >= 1000:
            raise AcceptanceBlocked("Verifier log budget exceeded; no truncated acceptance")
        proofs = []
        for event in records.get("events", []):
            try:
                body = json.loads(event["message"])
            except (ValueError, KeyError):
                continue
            if isinstance(body, dict) and "safe_to_resume" in body:
                proofs.append(body)
        if len(proofs) != 1:
            raise AcceptanceBlocked("One actual verifier result required")
        proof = proofs[0]
        if (
            proof.get("safe_to_resume") is not True
            or proof.get("findings") != []
            or type(proof.get("local_intents")) is not int
            or type(proof.get("remote_effects")) is not int
            or min(proof["local_intents"], proof["remote_effects"]) < 0
            or not re.fullmatch("[a-f0-9]{64}", proof.get("snapshot_hash", ""))
        ):
            raise AcceptanceBlocked("Native recovery verifier did not establish safe state")
        try:
            observed = datetime.fromisoformat(proof["observed_at"])
            age = (datetime.now(UTC) - observed).total_seconds()
            if not 0 <= age <= 300:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise AcceptanceBlocked(
                "Verifier snapshot must be timezone-aware and fresh within five minutes"
            ) from None
        return {
            "verifier_task_reference": reference(task_arn),
            "local_intents": proof["local_intents"],
            "remote_effects": proof["remote_effects"],
            "snapshot_hash": proof["snapshot_hash"],
            "snapshot_observed_at": observed.isoformat(),
            "safe_to_resume_at_observation": True,
            "authorization": "read_only_observation_never_permission_to_resume",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verifier-task")
    parser.add_argument("--quiesced", action="store_true")
    parser.add_argument("--approved-native-observation", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Preserve prior evidence; choose a new output path")
    if not args.approved_native_observation:
        parser.error("Native observation requires separately approved account/resources/costs")
    if args.verifier_task and not args.quiesced:
        parser.error("Recovery snapshot observation requires a quiesced runtime")
    record = {
        "status": "BLOCKED",
        "scope": "native_read_only_configuration_witness",
        "observed_at": datetime.now(UTC).isoformat(),
        "checks": {},
        "native_operations": [],
        "full_native_recovery_acceptance": "not_established_by_this_collector",
    }
    witness = None
    try:
        manifest = json.loads(args.manifest.read_text())
        session = boto3.Session(region_name="ca-central-1")
        if session.get_credentials() is None:
            raise AcceptanceBlocked("Authorized SDK credentials unavailable")
        witness = Witness(manifest, {s: session.client(s) for s in ("sts", "ecs", "rds", "logs")})
        record["commit"] = manifest["commit"]
        record["checks"]["identity"] = witness.identity()
        record["checks"]["runtime"] = witness.runtime(quiesced=args.quiesced)
        record["checks"]["databases"] = witness.databases()
        if args.verifier_task:
            record["checks"]["recovery_snapshot"] = witness.recovery(args.verifier_task)
        record["status"] = "CONFIGURATION_WITNESS_PASSED"
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        record["next_action"] = "Inspect private runtime configuration and authorized account state"
    record["native_operations"] = witness.calls if witness else []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"status": record["status"], "requests": len(record["native_operations"])}))
    return 0 if record["status"] == "CONFIGURATION_WITNESS_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
