"""Approval-gated, same-region retained-backup PITR. No third concurrent instance.

Service response contracts are checked before source deletion and again after retention.
Offline tests do not establish Free-plan execution eligibility or usable native recovery.
"""

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import time


STRATEGY = "retained-two-instance-v1"


class RetainedRecovery:
    def __init__(self, runner):
        self.r, self.m, self.c = runner, runner.m, runner.c
        self.rds = self.c["rds"]
        self.control, self.remote = self.m["control_db"], self.m["downstream_db"]
        self.target = self.r.prefix + "-restore"
        self.phases = []

    def instances(self, names):
        rows = [row for page in self.rds.get_paginator("describe_db_instances").paginate()
                for row in page["DBInstances"]]
        if len(rows) > 2 or {r["DBInstanceIdentifier"] for r in rows} != set(names):
            raise RuntimeError("Exact regional instance set required; never create a third instance")
        return rows

    def backup(self, source, status, baseline):
        response = self.rds.describe_db_instance_automated_backups(DbiResourceId=source["DbiResourceId"])
        rows = response.get("DBInstanceAutomatedBackups", [])
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError("Ambiguous retained backup identity")
        row = rows[0]
        expected_arn = f"arn:aws:rds:us-east-2:{self.m['account']}:db:{self.control}"
        if (row["DbiResourceId"] != source["DbiResourceId"]
                or row["DBInstanceIdentifier"] != self.control or row["DBInstanceArn"] != expected_arn
                or row["Region"] not in {"us-east-2", "use2"} or row["Engine"] != "postgres"
                or row["EngineVersion"] != "17.11" or row["AllocatedStorage"] != 20
                or source.get("BackupRetentionPeriod") != 1
                or (status == "active" and row.get("BackupRetentionPeriod") != 1)
                or (status == "retained" and row.get("BackupRetentionPeriod") not in (None, 1))
                or not row["Encrypted"]
                or row["KmsKeyId"] != source["KmsKeyId"]
                or not re.fullmatch(f"arn:aws:rds:us-east-2:{self.m['account']}:auto-backup:[A-Za-z0-9-]+",
                                    row["DBInstanceAutomatedBackupsArn"])):
            raise RuntimeError("Backup metadata differs from pinned encrypted source")
        if row["Status"] != status:
            return None
        window = row.get("RestoreWindow", {})
        earliest, latest = window.get("EarliestTime"), window.get("LatestTime")
        if earliest is None or latest is None:
            return None
        if any(not isinstance(t, datetime) or t.tzinfo is None for t in (earliest, latest, baseline)):
            raise RuntimeError("Aware backup timestamps required")
        if status == "active":
            latest = min(latest, source["LatestRestorableTime"])
        if not earliest <= baseline < latest:
            return None
        return {"metadata": row, "earliest": earliest, "latest": latest,
                "request_id": response["ResponseMetadata"]["RequestId"]}

    def wait_window(self, baseline):
        for _ in range(60):
            self.r.gate()
            source = self.r.database(self.control)
            window = self.backup(source, "active", baseline)
            if window:
                return source, window
            time.sleep(15)
        raise RuntimeError("Active backup window not established before fixture/source deletion")

    def tags(self, arn):
        tags = self.rds.list_tags_for_resource(ResourceName=arn)["TagList"]
        if {t["Key"]: t["Value"] for t in tags}.get("AcceptanceRun") != self.m["run"]:
            raise RuntimeError("Retained backup cleanup tag not independently established")

    def checkpoint(self, phase, payload):
        document = {"phase": phase, "at": datetime.now(UTC).isoformat(),
                    "strategy": STRATEGY, "account": self.m["account"], "run": self.m["run"],
                    "cleanup_at": self.m["cleanup_at"], "records": self.r.records, **payload}
        raw = json.dumps(document, sort_keys=True, default=str).encode()
        sha = hashlib.sha256(raw).hexdigest()
        key = f"recovery/{phase}-{sha}.json"
        directory = Path(self.r.checkpoint_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{phase}-{sha}.json"
        with target.open("xb") as stream:
            stream.write(raw)
        if target.read_bytes() != raw:
            raise RuntimeError("Private checkpoint read-back mismatch")
        self.c["s3"].put_object(Bucket=self.m["guard_bucket"], Key=key, Body=raw,
                               IfNoneMatch="*", ServerSideEncryption="aws:kms",
                               SSEKMSKeyId=self.m["guard_key"])
        response = self.c["s3"].get_object(Bucket=self.m["guard_bucket"], Key=key)
        with response["Body"] as stream:
            if stream.read(2_000_001) != raw:
                raise RuntimeError("Cloud checkpoint read-back mismatch; source deletion refused")
        self.phases.append({"phase": phase, "checkpoint_sha256": sha,
                            "readback_request_id": response["ResponseMetadata"]["RequestId"]})

    def register(self, source, remote, backup):
        s3 = self.c["s3"]
        response = s3.get_object(Bucket=self.m["guard_bucket"], Key="inventory/registry.json")
        with response["Body"] as stream:
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError("Registry budget exceeded")
        known = json.loads(raw)
        rows = [{"kind": "database", "id": d["DBInstanceIdentifier"], "resource_id": d["DbiResourceId"],
                 "owner_secret": d.get("MasterUserSecret", {}).get("SecretArn")} for d in (source, remote)]
        rows.append({"kind": "backup", "id": source["DbiResourceId"],
                     "arn": backup["metadata"]["DBInstanceAutomatedBackupsArn"],
                     "source_identifier": self.control, "state": "active"})
        merged = {(r["kind"], r["id"]): r for r in known}
        for row in rows:
            previous = merged.get((row["kind"], row["id"]))
            if previous and row["kind"] == "database" and previous.get("resource_id") != row["resource_id"]:
                raise RuntimeError("Registry physical database identity changed")
            merged[(row["kind"], row["id"])] = {**(previous or {}), **row}
        s3.put_object(Bucket=self.m["guard_bucket"], Key="inventory/registry.json",
                      Body=json.dumps(list(merged.values()), default=str).encode(), IfMatch=response["ETag"],
                      ServerSideEncryption="aws:kms", SSEKMSKeyId=self.m["guard_key"])

    def verify_receipts(self, result, expected=None):
        fingerprint = result.get("remote_ledger_sha256", "")
        if not re.fullmatch("[a-f0-9]{64}", fingerprint) or result.get("write_denials") != 2:
            raise RuntimeError("Exact read-only receipt ledger fingerprint required")
        if expected is not None and fingerprint != expected:
            raise RuntimeError("Independent receipt database content changed")
        return fingerprint

    def execute(self, baseline):
        if not self.r.allow_source_deletion or not self.r.checkpoint_dir:
            raise RuntimeError("Separate source-deletion approval and private checkpoints required")
        self.r.gate()
        self.r.drained()
        self.instances({self.control, self.remote})
        source, before = self.wait_window(baseline)
        remote = self.r.database(self.remote)
        if (source.get("ReadReplicaDBInstanceIdentifiers") or source.get("ReadReplicaSourceDBInstanceIdentifier")
                or source.get("DBClusterIdentifier")):
            raise RuntimeError("Standalone source without replicas required")
        if source["DbiResourceId"] == remote["DbiResourceId"]:
            raise RuntimeError("Independent receipt recovery domain required")
        good = self.r.task("verify")
        if not good["safe_to_resume"] or good["local_intents"] != good["remote_effects"] or good["local_intents"] != 1:
            raise RuntimeError("Original 1/1 reconciliation required before destructive transition")
        fingerprint = self.verify_receipts(good)
        self.r.writes_frozen = True
        deadline = datetime.fromisoformat(self.m["cleanup_at"])
        if (deadline - datetime.now(UTC)).total_seconds() < 5400:
            raise RuntimeError("At least ninety minutes must remain before source deletion")
        key = self.c["kms"].describe_key(KeyId=source["KmsKeyId"])["KeyMetadata"]
        if key["KeyState"] != "Enabled" or key.get("DeletionDate"):
            raise RuntimeError("Source/backup encryption key must remain enabled")
        snapshots = [row for page in self.rds.get_paginator("describe_db_snapshots").paginate(
            DBInstanceIdentifier=self.control, SnapshotType="automated") for row in page["DBSnapshots"]]
        usable = [s for s in snapshots if s["Status"] == "available" and s["DbiResourceId"] == source["DbiResourceId"]
                  and s["Encrypted"] and s["KmsKeyId"] == source["KmsKeyId"]
                  and s["SnapshotCreateTime"] <= baseline
                  and s["SnapshotCreateTime"] + timedelta(days=1) > deadline + timedelta(hours=1)]
        if not usable:
            raise RuntimeError("Available source snapshot and conservative retention headroom required")
        backup_arn = before["metadata"]["DBInstanceAutomatedBackupsArn"]
        # Automated backup tags are independent of DB/snapshot tags. Never assume inheritance.
        self.rds.add_tags_to_resource(ResourceName=backup_arn,
                                      Tags=[{"Key": "AcceptanceRun", "Value": self.m["run"]}])
        self.tags(backup_arn)
        self.register(source, remote, before)
        payload = {"source": source, "remote_resource_id": remote["DbiResourceId"],
                   "backup": before["metadata"], "baseline": baseline.isoformat(),
                   "remote_ledger_sha256": fingerprint}
        self.checkpoint("before-source-deletion", payload)
        self.r.gate()
        self.r.drained()
        self.instances({self.control, self.remote})
        latest = self.r.database(self.control)
        current = self.backup(latest, "active", baseline)
        if (latest["DbiResourceId"] != source["DbiResourceId"] or not current
                or current["metadata"]["DBInstanceAutomatedBackupsArn"] != backup_arn):
            raise RuntimeError("Source/window changed after checkpoint; deletion refused")
        self.tags(backup_arn)
        self.rds.modify_db_instance(DBInstanceIdentifier=self.control, DeletionProtection=False, ApplyImmediately=True)
        for _ in range(20):
            self.r.gate()
            row = self.rds.describe_db_instances(DBInstanceIdentifier=self.control)["DBInstances"][0]
            if row["DbiResourceId"] != source["DbiResourceId"]:
                raise RuntimeError("Source identity changed while disabling protection")
            if row["DBInstanceStatus"] == "available" and not row["DeletionProtection"]:
                break
            time.sleep(5)
        else:
            raise RuntimeError("Deletion protection change not established")
        self.r.gate()
        self.r.drained()
        self.tags(backup_arn)
        if not self.backup(row, "active", baseline):
            raise RuntimeError("Restorable source window lost immediately before deletion")
        self.rds.delete_db_instance(DBInstanceIdentifier=self.control, SkipFinalSnapshot=True,
                                    DeleteAutomatedBackups=False)
        for _ in range(90):
            self.r.gate()
            try:
                self.rds.describe_db_instances(DBInstanceIdentifier=self.control)
            except self.rds.exceptions.DBInstanceNotFoundFault:
                break
            time.sleep(15)
        else:
            raise RuntimeError("Source deletion did not complete; no restore authorized")
        self.instances({self.remote})
        retained = None
        for _ in range(40):
            self.r.gate()
            retained = self.backup(source, "retained", baseline)
            if retained:
                break
            time.sleep(15)
        if not retained or retained["metadata"]["DBInstanceAutomatedBackupsArn"] != backup_arn:
            raise RuntimeError("Retained backup/window not established; never fall back to latest/snapshot")
        self.tags(backup_arn)
        if self.r.database(self.remote)["DbiResourceId"] != remote["DbiResourceId"]:
            raise RuntimeError("Independent receipt instance was replaced")
        self.checkpoint("retained-backup-observed", {**payload, "retained_backup": retained["metadata"]})
        self.r.gate()
        self.instances({self.remote})
        if self.c["kms"].describe_key(KeyId=source["KmsKeyId"])["KeyMetadata"]["KeyState"] != "Enabled":
            raise RuntimeError("Retained backup encryption key became unavailable")
        # Same-region deleted source: physical ID. The ARN selector is for replicated backups.
        response = self.rds.restore_db_instance_to_point_in_time(
            SourceDbiResourceId=source["DbiResourceId"], TargetDBInstanceIdentifier=self.target,
            RestoreTime=baseline, DBInstanceClass="db.t4g.micro", MultiAZ=False, PubliclyAccessible=False,
            DBSubnetGroupName=source["DBSubnetGroup"]["DBSubnetGroupName"],
            VpcSecurityGroupIds=[g["VpcSecurityGroupId"] for g in source["VpcSecurityGroups"]],
            DBParameterGroupName=source["DBParameterGroups"][0]["DBParameterGroupName"],
            AutoMinorVersionUpgrade=False,
            DeletionProtection=True, Tags=[{"Key": "AcceptanceRun", "Value": self.m["run"]}])
        for _ in range(90):
            self.r.gate()
            self.instances({self.remote, self.target})
            row = self.rds.describe_db_instances(DBInstanceIdentifier=self.target)["DBInstances"][0]
            if row["DBInstanceStatus"] == "available":
                break
            time.sleep(15)
        else:
            raise RuntimeError("Retained restore availability deadline exceeded")
        # Omission of MaxAllocatedStorage requests the service default, not autoscaling.
        # Fail closed if a restored instance unexpectedly inherits an autoscaling ceiling.
        if row.get("MaxAllocatedStorage") not in (None, 0):
            raise RuntimeError("Restored storage autoscaling unexpectedly enabled")
        # The pinned SDK does not accept BackupRetentionPeriod on RestoreDBInstanceToPointInTime.
        # Inspect inherited retention, then adjust only the disposable target if required.
        if row.get("BackupRetentionPeriod") != 1:
            self.rds.modify_db_instance(DBInstanceIdentifier=self.target, BackupRetentionPeriod=1,
                                       ApplyImmediately=True)
            for _ in range(60):
                self.r.gate()
                row = self.rds.describe_db_instances(DBInstanceIdentifier=self.target)["DBInstances"][0]
                if row["DBInstanceStatus"] == "available" and row.get("BackupRetentionPeriod") == 1:
                    break
                time.sleep(15)
            else:
                raise RuntimeError("Restored one-day backup configuration not established")
        restored = self.r.database(self.target)
        if (restored["DbiResourceId"] in {source["DbiResourceId"], remote["DbiResourceId"]}
                or restored["KmsKeyId"] != source["KmsKeyId"]):
            raise RuntimeError("Distinct restored physical identity and original encryption key required")
        for _ in range(2):
            if self.r.database(self.remote)["DbiResourceId"] != remote["DbiResourceId"]:
                raise RuntimeError("Receipt instance changed after restore")
            blocked = self.r.task("verify", extra={"RESTORED_HOST": restored["Endpoint"]["Address"]}, expected_exit=2)
            self.verify_receipts(blocked, fingerprint)
            if (blocked["safe_to_resume"] or blocked["local_intents"] != 0 or blocked["remote_effects"] != 1
                    or {f["reason"] for f in blocked["findings"]} != {"remote_ahead_missing_intent"}):
                raise RuntimeError("Native restored-control fail-closed proof not established")
        result = {"status": "NATIVE_RETAINED_SQL_PITR_ACCEPTANCE_PASSED", "strategy": STRATEGY,
                  "full_application_acceptance": False, "original_control_deleted": True,
                  "maximum_concurrent_instances": 2, "restore_baseline": baseline.isoformat(),
                  "restore_request_id": response["ResponseMetadata"]["RequestId"],
                  "remote_ledger_sha256": fingerprint, "records": self.r.records, "phases": self.phases}
        self.checkpoint("restored-fail-closed-verified", {**payload, "result": result})
        return result
