"""Approval-gated native fixture/PITR controller. Never provisions the Terraform roots."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import time

import boto3

from .native_arming import armed_gate
from .native_retained import RetainedRecovery, STRATEGY


def validate_manifest(m):
    if m.get("acceptance_workflow", "sql-fixture-v1") not in {"sql-fixture-v1", "application-recovery-v1"}:
        raise ValueError("Unknown versioned acceptance workflow")
    if m.get("acceptance_workflow") == "application-recovery-v1" and m.get("recovery_strategy") != STRATEGY:
        raise ValueError("Application recovery requires the retained two-instance strategy")
    if m.get("acceptance_workflow") == "application-recovery-v1" and not re.fullmatch("[a-f0-9]{40}", m.get("commit", "")):
        raise ValueError("Application recovery requires an exact source commit")
    if m["region"] != "us-east-2" or not re.fullmatch("[a-z0-9]{8,16}", m["run"]):
        raise ValueError("Ohio acceptance run required")
    if not re.fullmatch("[0-9]{12}", m["account"]):
        raise ValueError("Account pin required")
    if m.get("backup_retention_days") != 1:
        raise ValueError("Isolated Free-plan acceptance requires one-day automated backups")
    if m.get("database_name") != "csaacceptance":
        raise ValueError("Isolated acceptance database name required")
    prefix = "csa-native-" + m["run"]
    if m.get("recovery_strategy") is not None:
        if (
            m["recovery_strategy"] != STRATEGY
            or m.get("maximum_concurrent_instances") != 2
            or m.get("source_deletion_scope") != "disposable_synthetic_control_only"
            or m.get("verification_contract") != "receipt-ledger-sha256-v1"
            or m.get("guard_bucket") != f"{prefix}-guard-{m['account']}"
            or not re.fullmatch(
                f"arn:aws:kms:us-east-2:{m['account']}:key/[a-f0-9-]{{36}}", m.get("guard_key", "")
            )
        ):
            raise ValueError(
                "Explicit retained-backup protocol, private guard and two-instance bound required"
            )
    if m["control_db"] != prefix + "-control" or m["downstream_db"] != prefix + "-receipts":
        raise ValueError("Independent exact database identities required")
    if m["cluster"] != f"arn:aws:ecs:us-east-2:{m['account']}:cluster/{prefix}":
        raise ValueError("Cluster pin mismatch")
    if set(m["tasks"]) != {"bootstrap", "baseline", "control", "receipt", "verify"}:
        raise ValueError("Exact one-off topology required")
    for name, arn in m["tasks"].items():
        if not re.fullmatch(
            f"arn:aws:ecs:us-east-2:{m['account']}:task-definition/{prefix}-{name}:[1-9][0-9]*", arn
        ):
            raise ValueError("Exact task revision required")
    if not re.fullmatch(
        f"{m['account']}[.]dkr[.]ecr[.]us-east-2[.]amazonaws[.]com/{prefix}@sha256:[a-f0-9]{{64}}", m["image"]
    ):
        raise ValueError("Immutable image pin required")
    deadline = datetime.fromisoformat(m["cleanup_at"])
    if deadline.tzinfo is None or not 0 < (deadline - datetime.now(UTC)).total_seconds() <= 10800:
        raise ValueError("Cleanup must start within three hours; expired authority rejected")
    return prefix


class NativeRun:
    def __init__(self, manifest, clients, *, allow_source_deletion=False, checkpoint_dir=None):
        self.m, self.c = manifest, clients
        self.prefix = validate_manifest(manifest)
        self.records = []
        self.allow_source_deletion, self.checkpoint_dir = allow_source_deletion, checkpoint_dir
        self.writes_frozen = False
        if manifest.get("recovery_strategy") == STRATEGY and (
            not allow_source_deletion
            or checkpoint_dir is None
            or not Path(checkpoint_dir).resolve().is_relative_to(Path.cwd().resolve() / ".local")
        ):
            raise ValueError(
                "Retained recovery requires separate deletion approval and private .local checkpoints"
            )

    def gate(self):
        return armed_gate(self.m, self.c)

    def drained(self):
        ecs = self.c["ecs"]
        for status in ("RUNNING", "PENDING"):
            if any(
                page.get("taskArns")
                for page in ecs.get_paginator("list_tasks").paginate(
                    cluster=self.m["cluster"], desiredStatus=status
                )
            ):
                raise RuntimeError("Write-capable tasks are not quiescent")

    def task(self, name, *, extra=None, expected_exit=0):
        application = self.m.get("acceptance_workflow") == "application-recovery-v1"
        maintenance = (
            application
            and name == "receipt"
            and set(extra or {}) == {"RESTORED_HOST", "SOURCE_DATABASE", "RECOVERY_POINT", "RECOVERY_PHASE"}
        )
        if self.writes_frozen and name != "verify" and not maintenance:
            raise RuntimeError("Writers are frozen for retained recovery")
        self.gate()
        self.drained()
        ecs = self.c["ecs"]
        td = ecs.describe_task_definition(taskDefinition=self.m["tasks"][name])["taskDefinition"]
        containers = td["containerDefinitions"]
        if (
            len(containers) != 1
            or containers[0]["image"] != self.m["image"]
            or containers[0].get("entryPoint")
            or containers[0]["name"] != name
            or containers[0]["command"]
            != [
                "python",
                "-m",
                "controlplane.native_app_tasks" if application else "controlplane.native_tasks",
                name,
            ]
        ):
            raise RuntimeError("Task content differs from approved image/command")
        allowed = {"FIXTURE_JSON"} if name == "receipt" else {"RESTORED_HOST"} if name == "verify" else set()
        if application:
            allowed = (
                {"RESTORED_HOST", "SOURCE_DATABASE", "RECOVERY_POINT", "RECOVERY_PHASE"}
                if name == "receipt"
                else {"RESTORED_HOST"}
                if name == "verify"
                else set()
            )
            if name == "receipt" and (
                not maintenance
                or extra["RECOVERY_PHASE"] not in {"behind", "matching"}
                or not re.fullmatch("[a-f0-9]{64}", extra["SOURCE_DATABASE"])
            ):
                raise ValueError("Exact application recovery maintenance binding required")
            if extra and "RESTORED_HOST" in extra:
                if self.database(self.prefix + "-restore")["Endpoint"]["Address"] != extra["RESTORED_HOST"]:
                    raise ValueError("Restored endpoint differs from independently observed RDS target")
            if name == "receipt":
                point = datetime.fromisoformat(extra["RECOVERY_POINT"])
                if point.tzinfo is None or point > datetime.now(UTC):
                    raise ValueError("Aware historical recovery point required")
        if set(extra or {}) - allowed:
            raise ValueError("Unexpected task override")
        kwargs = dict(
            cluster=self.m["cluster"],
            taskDefinition=self.m["tasks"][name],
            launchType="FARGATE",
            platformVersion="1.4.0",
            count=1,
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": [self.m["task_subnet"]],
                    "securityGroups": [self.m["task_groups"][name]],
                    "assignPublicIp": "DISABLED",
                }
            },
        )
        if extra:
            kwargs["overrides"] = {
                "containerOverrides": [
                    {"name": name, "environment": [{"name": k, "value": v} for k, v in extra.items()]}
                ]
            }
        launched = ecs.run_task(**kwargs)
        tasks = launched.get("tasks", [])
        if launched.get("failures") or len(tasks) != 1:
            for task in tasks:
                ecs.stop_task(
                    cluster=self.m["cluster"], task=task["taskArn"], reason="Partial native launch rejected"
                )
            raise RuntimeError("Native launch incomplete")
        arn = tasks[0]["taskArn"]
        try:
            ecs.get_waiter("tasks_stopped").wait(
                cluster=self.m["cluster"], tasks=[arn], WaiterConfig={"Delay": 10, "MaxAttempts": 60}
            )
        except Exception:
            ecs.stop_task(cluster=self.m["cluster"], task=arn, reason="Bounded native task wait expired")
            raise RuntimeError("Task stop requested; independently confirm termination") from None
        task = ecs.describe_tasks(cluster=self.m["cluster"], tasks=[arn])["tasks"][0]
        if (
            task["taskArn"] != arn
            or task["lastStatus"] != "STOPPED"
            or task["taskDefinitionArn"] != self.m["tasks"][name]
            or len(task["containers"]) != 1
            or task["containers"][0]["exitCode"] != expected_exit
            or task["containers"][0]["imageDigest"] != self.m["image"].split("@")[1]
        ):
            raise RuntimeError("Stopped task identity/image/exit mismatch")
        events = self.c["logs"].get_log_events(
            logGroupName=f"/csa-native/{self.m['run']}/{name}",
            logStreamName=f"task/{name}/" + arn.rsplit("/", 1)[-1],
            startFromHead=True,
            limit=1000,
        )["events"]
        results = []
        for event in events:
            try:
                record = json.loads(event["message"])
                if "native_fixture" in record:
                    results.append(record["native_fixture"])
            except (ValueError, TypeError):
                continue
        if len(events) >= 1000 or len(results) != 1:
            raise RuntimeError("One complete actual task result required")
        self.records.append(
            {
                "task": name,
                "task_reference": hashlib.sha256(arn.encode()).hexdigest(),
                "exit_code": expected_exit,
                "result": results[0],
            }
        )
        return results[0]

    def database(self, identifier):
        db = self.c["rds"].describe_db_instances(DBInstanceIdentifier=identifier)["DBInstances"][0]
        if (
            db["DBInstanceIdentifier"] != identifier
            or db["DBInstanceStatus"] != "available"
            or db["DBInstanceClass"] != "db.t4g.micro"
            or db["MultiAZ"]
            or db["PubliclyAccessible"]
            or not db["StorageEncrypted"]
            or db["Engine"] != "postgres"
            or db["EngineVersion"] != "17.11"
            or db["AllocatedStorage"] != 20
            or db["BackupRetentionPeriod"] != self.m["backup_retention_days"]
            or db.get("DBName") != self.m["database_name"]
        ):
            raise RuntimeError("Private Single-AZ acceptance database not established")
        return db

    def restorable_window(self, source):
        response = self.c["rds"].describe_db_instance_automated_backups(DbiResourceId=source["DbiResourceId"])
        rows = response.get("DBInstanceAutomatedBackups", [])
        if not rows:
            return None
        if (
            len(rows) != 1
            or rows[0]["DbiResourceId"] != source["DbiResourceId"]
            or rows[0]["DBInstanceIdentifier"] != source["DBInstanceIdentifier"]
        ):
            raise RuntimeError("Automated backup identity mismatch")
        backup = rows[0]
        if backup["Status"] != "active":
            return None
        window = backup.get("RestoreWindow", {})
        earliest, latest = window.get("EarliestTime"), window.get("LatestTime")
        instance_latest = source.get("LatestRestorableTime")
        if earliest is None or latest is None or instance_latest is None:
            return None
        if any(not isinstance(t, datetime) or t.tzinfo is None for t in (earliest, latest, instance_latest)):
            raise RuntimeError("Aware service restore timestamps required")
        latest = min(latest, instance_latest)
        if earliest > latest:
            return None
        return {
            "earliest": earliest,
            "latest": latest,
            "backup_reference": hashlib.sha256(backup["DBInstanceAutomatedBackupsArn"].encode()).hexdigest(),
            "request_id": response["ResponseMetadata"]["RequestId"],
        }

    def run(self):
        if self.m.get("acceptance_workflow") == "application-recovery-v1":
            from .native_application import ApplicationRecovery

            return ApplicationRecovery(self).execute()
        self.gate()
        original = self.database(self.m["control_db"])
        independent = self.database(self.m["downstream_db"])
        if original["DbiResourceId"] == independent["DbiResourceId"]:
            raise RuntimeError("Independent database identities required before bootstrap")
        self.task("bootstrap")
        initial = self.task("verify")
        if not initial["safe_to_resume"] or initial["local_intents"] != 0 or initial["remote_effects"] != 0:
            raise RuntimeError("Fresh isolated ledgers required; do not reuse a partially executed run")
        baseline = datetime.fromisoformat(self.task("baseline")["baseline_at"])
        if self.m.get("recovery_strategy") == STRATEGY:
            RetainedRecovery(self).wait_window(baseline)
        fixture = self.task("control")
        receipt = self.task("receipt", extra={"FIXTURE_JSON": json.dumps(fixture)})
        replay = self.task("receipt", extra={"FIXTURE_JSON": json.dumps(fixture)})
        if receipt != replay or receipt["effects"] != 1:
            raise RuntimeError("Idempotent exact receipt not established")
        good = self.task("verify")
        if not good["safe_to_resume"] or good["local_intents"] != 1 or good["remote_effects"] != 1:
            raise RuntimeError("Original independent ledger comparison failed")
        return self._recover_from_baseline(baseline)

    def _recover_from_baseline(self, baseline):
        # The normal run captures this timestamp before intent creation. An operator
        # continuation must independently revalidate its original executed checkpoint.
        if self.m.get("recovery_strategy") == STRATEGY:
            return RetainedRecovery(self).execute(baseline)
        self.gate()
        self.drained()
        source, remote = self.database(self.m["control_db"]), self.database(self.m["downstream_db"])
        if source["DbiResourceId"] == remote["DbiResourceId"]:
            raise RuntimeError("Independent database identities required")
        # Wait for real WAL-backed recoverability, not a synthetic local row deletion.
        for _ in range(90):
            self.gate()
            source = self.database(self.m["control_db"])
            window = self.restorable_window(source)
            if window and window["earliest"] <= baseline <= window["latest"]:
                break
            time.sleep(30)
        else:
            raise RuntimeError("Real restorable window unavailable within bounded wait")
        self.drained()
        target = self.prefix + "-restore"
        self.c["rds"].restore_db_instance_to_point_in_time(
            SourceDBInstanceIdentifier=self.m["control_db"],
            TargetDBInstanceIdentifier=target,
            RestoreTime=baseline,
            DBInstanceClass="db.t4g.micro",
            MultiAZ=False,
            PubliclyAccessible=False,
            DBSubnetGroupName=source["DBSubnetGroup"]["DBSubnetGroupName"],
            VpcSecurityGroupIds=[g["VpcSecurityGroupId"] for g in source["VpcSecurityGroups"]],
            DBParameterGroupName=source["DBParameterGroups"][0]["DBParameterGroupName"],
            DeletionProtection=True,
            Tags=[{"Key": "AcceptanceRun", "Value": self.m["run"]}],
        )
        for _ in range(90):
            self.gate()
            row = self.c["rds"].describe_db_instances(DBInstanceIdentifier=target)["DBInstances"][0]
            if row["DBInstanceStatus"] == "available":
                break
            time.sleep(30)
        else:
            raise RuntimeError("PITR availability deadline exceeded")
        if row.get("BackupRetentionPeriod") != self.m["backup_retention_days"]:
            self.c["rds"].modify_db_instance(
                DBInstanceIdentifier=target,
                BackupRetentionPeriod=self.m["backup_retention_days"],
                ApplyImmediately=True,
            )
        for _ in range(60):
            self.gate()
            observed = self.c["rds"].describe_db_instances(DBInstanceIdentifier=target)["DBInstances"][0]
            if (
                observed["DBInstanceStatus"] == "available"
                and observed.get("BackupRetentionPeriod") == self.m["backup_retention_days"]
            ):
                break
            time.sleep(15)
        else:
            raise RuntimeError("Restored backup configuration not established within deadline")
        restored = self.database(target)
        if restored["DbiResourceId"] in (source["DbiResourceId"], remote["DbiResourceId"]):
            raise RuntimeError("Restore did not create a distinct resource")
        blocked = self.task(
            "verify", extra={"RESTORED_HOST": restored["Endpoint"]["Address"]}, expected_exit=2
        )
        if (
            blocked["safe_to_resume"]
            or blocked["local_intents"] != 0
            or blocked["remote_effects"] != 1
            or {f["reason"] for f in blocked["findings"]} != {"remote_ahead_missing_intent"}
        ):
            raise RuntimeError("Real remote-ahead fail-closed condition not established")
        self.task("verify")  # Original control is retained, never repaired by forging intents.
        return {
            "status": "NATIVE_SQL_PITR_ACCEPTANCE_PASSED",
            "full_application_acceptance": False,
            "restore_baseline": baseline.isoformat(),
            "records": self.records,
            "database_references": {
                name: hashlib.sha256(row["DbiResourceId"].encode()).hexdigest()
                for name, row in [("control", source), ("receipts", remote), ("restored_control", restored)]
            },
            "restorable_window": {
                "earliest": window["earliest"].isoformat(),
                "latest": window["latest"].isoformat(),
                "backup_reference": window["backup_reference"],
                "request_id": window["request_id"],
            },
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--approved-sha256", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--approve-retained-source-deletion", action="store_true")
    p.add_argument("--private-checkpoints", type=Path)
    a = p.parse_args()
    raw = a.manifest.read_bytes()
    if not a.execute or hashlib.sha256(raw).hexdigest() != a.approved_sha256 or a.output.exists():
        p.error("Explicit approval digest/execution and a fresh private output are required")
    m = json.loads(raw)
    validate_manifest(m)
    s = boto3.Session(region_name="us-east-2")
    c = {
        name: s.client(name, region_name="us-east-1" if name == "freetier" else "us-east-2")
        for name in ("sts", "freetier", "ecs", "rds", "logs", "scheduler", "lambda", "s3", "kms")
    }
    runner = NativeRun(
        m, c, allow_source_deletion=a.approve_retained_source_deletion, checkpoint_dir=a.private_checkpoints
    )
    if a.private_checkpoints:
        if not a.private_checkpoints.resolve().is_relative_to(Path.cwd().resolve() / ".local"):
            p.error("Request receipt logs require a private .local directory")
        a.private_checkpoints.mkdir(parents=True, exist_ok=True)

        def receipt(http_response, parsed, model, **kwargs):
            row = {
                "at": datetime.now(UTC).isoformat(),
                "service": model.service_model.service_name,
                "operation": model.name,
                "request_id": parsed.get("ResponseMetadata", {}).get("RequestId"),
                "http_status": http_response.status_code,
                "error_code": parsed.get("Error", {}).get("Code"),
            }
            with (a.private_checkpoints / "aws-request-receipts.jsonl").open("a") as stream:
                stream.write(json.dumps(row) + "\n")

        for client in c.values():
            client.meta.events.register("after-call.*.*", receipt)
    try:
        result = runner.run()
    except Exception as exc:
        result = {
            "status": "FAILED_NATIVE_ACCEPTANCE",
            "error_type": type(exc).__name__,
            "records": runner.records,
            "cleanup": "independent_guard_state_and_cleanup_must_be_checked",
            "aws_error_code": getattr(exc, "response", {}).get("Error", {}).get("Code"),
            "aws_request_id": getattr(exc, "response", {}).get("ResponseMetadata", {}).get("RequestId"),
        }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"]}))
    return (
        0
        if result["status"]
        in {
            "NATIVE_SQL_PITR_ACCEPTANCE_PASSED",
            "NATIVE_RETAINED_SQL_PITR_ACCEPTANCE_PASSED",
            "NATIVE_APPLICATION_RECOVERY_ACCEPTANCE_PASSED",
        }
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
