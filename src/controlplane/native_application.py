"""Two sequential retained-backup restores, never more than two RDS instances.

Original SQL-only evidence remains separate. This controller accepts neither RDS
availability nor a task's assertion without independent read-only reconciliation.
"""

import json
import time
import hashlib
from datetime import UTC, datetime, timedelta

from .native_retained import RetainedRecovery


class ApplicationRecovery(RetainedRecovery):
    def claim_execution(self):
        """An interrupted controller cannot silently restart this run from seed."""
        raw = json.dumps({"run": self.m["run"], "cleanup_at": self.m["cleanup_at"],
                          "image": self.m["image"], "commit": self.m["commit"]}, sort_keys=True).encode()
        key = "execution/application-start.json"
        self.c["s3"].put_object(Bucket=self.m["guard_bucket"], Key=key, Body=raw, IfNoneMatch="*",
                               ServerSideEncryption="aws:kms", SSEKMSKeyId=self.m["guard_key"])
        with self.c["s3"].get_object(Bucket=self.m["guard_bucket"], Key=key)["Body"] as body:
            if body.read(len(raw) + 1) != raw:
                raise RuntimeError("Application execution claim readback failed")
        self.checkpoint("application-execution-claimed", {"claim_sha256": hashlib.sha256(raw).hexdigest()})

    def select_quiescent_point(self, observed_at, label):
        """Choose actual restorable time after commits, not a pre-backup timestamp.

        No writer may run while selecting a later point. Whole seconds plus a
        two-second commit margin avoid a service truncating fractional time
        before the observed transaction. Original task timestamps stay intact.
        """
        if observed_at.tzinfo is None:
            raise ValueError("Aware post-commit observation required")
        minimum = observed_at.astimezone(UTC).replace(microsecond=0) + timedelta(seconds=2)
        for _ in range(60):
            self.r.gate()
            self.r.drained()
            source = self.r.database(self.control)
            response = self.rds.describe_db_instance_automated_backups(DbiResourceId=source["DbiResourceId"])
            rows = response.get("DBInstanceAutomatedBackups", [])
            if len(rows) > 1:
                raise RuntimeError("Ambiguous recovery-point backup identity")
            earliest = rows[0].get("RestoreWindow", {}).get("EarliestTime") if rows else None
            if earliest is not None:
                if not isinstance(earliest, datetime) or earliest.tzinfo is None:
                    raise RuntimeError("Aware service recovery window required")
                point = max(minimum, earliest.astimezone(UTC).replace(microsecond=0) + timedelta(seconds=1))
                window = self.backup(source, "active", point)
                if window:
                    self.checkpoint(
                        label + "-quiescent-point-selected",
                        {
                            "application_observed_at": observed_at.isoformat(),
                            "selected_point": point.isoformat(),
                            "backup_window": window["metadata"]["RestoreWindow"],
                            "backup_request_id": window["request_id"],
                            "source_resource_id": source["DbiResourceId"],
                            "quiescent": True,
                            "time_selection": "whole-second point after commit observation and earliest usable backup",
                        },
                    )
                    return point, source, window
            time.sleep(15)
        raise RuntimeError("Active backup window not established for a quiescent post-commit recovery point")

    def require_headroom(self, seconds):
        self.r.gate()
        if (datetime.fromisoformat(self.m["cleanup_at"]) - datetime.now(UTC)).total_seconds() < seconds:
            raise RuntimeError("Insufficient fixed-deadline recovery headroom")

    def remember(self, database):
        """Keep every managed owner secret even when the same target name is reused."""
        secret = database.get("MasterUserSecret", {}).get("SecretArn")
        if not secret:
            # PostgreSQL PITR does not support the Oracle-only managed-password
            # restore flags. Existing scoped application logins are restored;
            # owner access is not needed to run the recovery coordinator.
            if database["DBInstanceIdentifier"] == self.target:
                return
            raise RuntimeError("Managed original owner secret required before source deletion")
        response = self.c["s3"].get_object(Bucket=self.m["guard_bucket"], Key="inventory/registry.json")
        with response["Body"] as stream:
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError("Registry budget exceeded")
        rows = json.loads(raw)
        key = ("secret", secret)
        merged = {(r["kind"], r["id"]): r for r in rows}
        merged[key] = {
            "kind": "secret",
            "id": secret,
            "managed": True,
            "database_resource_id": database["DbiResourceId"],
        }
        self.c["s3"].put_object(
            Bucket=self.m["guard_bucket"],
            Key="inventory/registry.json",
            Body=json.dumps(list(merged.values())).encode(),
            IfMatch=response["ETag"],
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=self.m["guard_key"],
        )
        # Durable exact readback precedes any deletion/restore of this generation.
        with self.c["s3"].get_object(Bucket=self.m["guard_bucket"], Key="inventory/registry.json")[
            "Body"
        ] as body:
            verified = json.loads(body.read(2_000_001))
        if merged[key] not in verified:
            raise RuntimeError("Database-generation registry readback failed")

    def delete_owned(self, database, *, retain, points=()):
        name = database["DBInstanceIdentifier"]
        if name not in {self.control, self.target} or (retain and name != self.control):
            raise ValueError("Only disposable exact control generations may be deleted")
        self.r.gate()
        self.r.drained()
        self.tags(database["DBInstanceArn"])
        current = self.r.database(name)
        if current["DbiResourceId"] != database["DbiResourceId"]:
            raise RuntimeError("Physical source changed before deletion")
        self.remember(current)
        self.checkpoint(
            "delete-intent-" + current["DbiResourceId"],
            {
                "database": current,
                "retain_automated_backups": retain,
                "scope": "disposable_synthetic_control_generation_only",
            },
        )
        self.rds.modify_db_instance(
            DBInstanceIdentifier=name, DeletionProtection=False, ApplyImmediately=True
        )
        for _ in range(30):
            self.r.gate()
            row = self.rds.describe_db_instances(DBInstanceIdentifier=name)["DBInstances"][0]
            if row["DbiResourceId"] != database["DbiResourceId"]:
                raise RuntimeError("Physical source changed while removing deletion protection")
            if row["DBInstanceStatus"] == "available" and row["DeletionProtection"] is False:
                break
            time.sleep(5)
        else:
            raise RuntimeError("Deletion protection transition unverified")
        self.r.drained()
        if retain:
            if len(points) != 2:
                raise RuntimeError("Both exact source recovery windows required immediately before deletion")
            for point in points:
                window = self.backup(row, "active", point)
                if not window:
                    raise RuntimeError("Exact source recovery window lost immediately before deletion")
                self.tags(window["metadata"]["DBInstanceAutomatedBackupsArn"])
        self.rds.delete_db_instance(
            DBInstanceIdentifier=name, SkipFinalSnapshot=True, DeleteAutomatedBackups=not retain
        )
        for _ in range(90):
            self.r.gate()
            try:
                self.rds.describe_db_instances(DBInstanceIdentifier=name)
            except self.rds.exceptions.DBInstanceNotFoundFault:
                self.instances({self.remote})
                return
            time.sleep(15)
        raise RuntimeError("Control-generation deletion incomplete; no next restore authorized")

    def restore(self, source, remote, point, label):
        self.require_headroom(1500)
        self.r.drained()
        self.instances({self.remote})
        retained = self.backup(source, "retained", point)
        if not retained:
            raise RuntimeError("Exact retained window lost; no latest/snapshot fallback")
        self.tags(retained["metadata"]["DBInstanceAutomatedBackupsArn"])
        if (
            self.r.database(self.remote)["DbiResourceId"] != remote["DbiResourceId"]
            or self.c["kms"].describe_key(KeyId=source["KmsKeyId"])["KeyMetadata"]["KeyState"] != "Enabled"
        ):
            raise RuntimeError("Independent receipt identity or original encryption key changed")
        started = time.perf_counter()
        response = self.rds.restore_db_instance_to_point_in_time(
            SourceDbiResourceId=source["DbiResourceId"],
            TargetDBInstanceIdentifier=self.target,
            RestoreTime=point,
            DBInstanceClass="db.t4g.micro",
            MultiAZ=False,
            PubliclyAccessible=False,
            DBSubnetGroupName=source["DBSubnetGroup"]["DBSubnetGroupName"],
            VpcSecurityGroupIds=[g["VpcSecurityGroupId"] for g in source["VpcSecurityGroups"]],
            DBParameterGroupName=source["DBParameterGroups"][0]["DBParameterGroupName"],
            AutoMinorVersionUpgrade=False,
            DeletionProtection=True,
            Tags=[{"Key": "AcceptanceRun", "Value": self.m["run"]}],
        )
        for _ in range(100):
            self.r.gate()
            self.instances({self.remote, self.target})
            row = self.rds.describe_db_instances(DBInstanceIdentifier=self.target)["DBInstances"][0]
            if row["DBInstanceStatus"] == "available":
                break
            time.sleep(15)
        else:
            raise RuntimeError("Retained restore did not become available within bounded wait")
        if row.get("MaxAllocatedStorage") not in (None, 0):
            raise RuntimeError("Unexpected restored storage autoscaling")
        if row.get("BackupRetentionPeriod") != 1:
            self.rds.modify_db_instance(
                DBInstanceIdentifier=self.target, BackupRetentionPeriod=1, ApplyImmediately=True
            )
            for _ in range(60):
                self.r.gate()
                row = self.rds.describe_db_instances(DBInstanceIdentifier=self.target)["DBInstances"][0]
                if row["DBInstanceStatus"] == "available" and row.get("BackupRetentionPeriod") == 1:
                    break
                time.sleep(10)
            else:
                raise RuntimeError("One-day restored backup retention unverified")
        restored = self.r.database(self.target)
        if (
            restored["DbiResourceId"] in {source["DbiResourceId"], remote["DbiResourceId"]}
            or restored["KmsKeyId"] != source["KmsKeyId"]
        ):
            raise RuntimeError("Restored generation is not independent and correctly encrypted")
        self.remember(restored)
        self.checkpoint(
            label + "-rds-available",
            {
                "restored": restored,
                "restore_point": point.isoformat(),
                "request_id": response["ResponseMetadata"]["RequestId"],
                "database_restore_seconds": time.perf_counter() - started,
                "application_recovery": "not_yet_verified",
            },
        )
        return restored

    def verify_result(self, result, local, remote, opened, fingerprint):
        if (
            type(result.get("local_intents")) is not int
            or result["local_intents"] != local
            or type(result.get("remote_effects")) is not int
            or result["remote_effects"] != remote
            or result.get("safe_to_resume") is not opened
            or result.get("execution_gate", {}).get("mode") != ("open" if opened else "closed")
        ):
            raise RuntimeError("Independent typed application comparison failed")
        self.verify_receipts(result, fingerprint)
        if not opened:
            reasons = [r["reason"] for r in result["findings"]]
            if (
                reasons.count("remote_ahead_missing_intent") != 2
                or reasons.count("execution_gate_closed") != 1
            ):
                raise RuntimeError("Independent remote-ahead and closed-gate evidence required")
        elif result["findings"]:
            raise RuntimeError("Opened recovery still has unresolved findings")

    def execute(self):
        if not self.r.allow_source_deletion or not self.r.checkpoint_dir:
            raise RuntimeError("Approved disposable source deletion and durable checkpoints required")
        self.require_headroom(9000)
        self.claim_execution()
        self.r.drained()
        self.instances({self.control, self.remote})
        source, remote = self.r.database(self.control), self.r.database(self.remote)
        if any(not row.get("MasterUserSecret", {}).get("SecretArn") for row in (source, remote)):
            raise RuntimeError("Both initial managed owner-secret identities required")
        self.r.task("bootstrap")
        preflight = self.r.task("receipt", extra={"RECOVERY_PHASE": "source-preflight"})
        if (preflight.get("stage") != "source_preflight_passed"
                or preflight.get("recovery_operations_rolled_back") is not True
                or preflight.get("write_denials") != 2):
            raise RuntimeError("Source-side recovery permission preflight required before PITR")
        self.checkpoint("source-recovery-permissions-verified", {"preflight": preflight})
        first = self.r.task("baseline")
        first_observed = datetime.fromisoformat(first["recovery_point"])
        if first.get("terminated_worker") is not True or first["comparison"]["remote_effects"] != 3:
            raise RuntimeError("Actual application baseline/worker interruption required")
        point1, _, _ = self.select_quiescent_point(first_observed, "behind")
        second = self.r.task("control")
        point2, source, backup = self.select_quiescent_point(
            datetime.fromisoformat(second["recovery_point"]), "matching"
        )
        if not point1 < point2 or second["comparison"]["remote_effects"] != 5:
            raise RuntimeError("Strictly later complete application checkpoint required")
        if not self.backup(source, "active", point1):
            raise RuntimeError("Earlier application checkpoint not in source PITR window")
        initial = self.r.task("verify")
        if (
            initial.get("safe_to_resume") is not True
            or initial.get("local_intents") != 5
            or initial.get("remote_effects") != 5
        ):
            raise RuntimeError("Independent source reconciliation failed")
        fingerprint = self.verify_receipts(initial, second["remote_ledger_sha256"])
        self.r.writes_frozen = True
        self.require_headroom(6900)
        deadline = datetime.fromisoformat(self.m["cleanup_at"])
        if self.c["kms"].describe_key(KeyId=source["KmsKeyId"])["KeyMetadata"]["KeyState"] != "Enabled":
            raise RuntimeError("Original backup key must remain enabled")
        if (
            source.get("ReadReplicaDBInstanceIdentifiers")
            or source.get("ReadReplicaSourceDBInstanceIdentifier")
            or source.get("DBClusterIdentifier")
        ):
            raise RuntimeError("Only standalone disposable source allowed")
        snapshots = [
            s
            for page in self.rds.get_paginator("describe_db_snapshots").paginate(
                DBInstanceIdentifier=self.control, SnapshotType="automated"
            )
            for s in page["DBSnapshots"]
        ]
        if not any(
            s["Status"] == "available"
            and s["DbiResourceId"] == source["DbiResourceId"]
            and s["Encrypted"]
            and s["KmsKeyId"] == source["KmsKeyId"]
            and s["SnapshotCreateTime"] <= point1
            and s["SnapshotCreateTime"] + timedelta(days=1) > deadline + timedelta(hours=1)
            for s in snapshots
        ):
            raise RuntimeError("Available earlier snapshot and conservative retention headroom required")
        arn = backup["metadata"]["DBInstanceAutomatedBackupsArn"]
        self.rds.add_tags_to_resource(
            ResourceName=arn, Tags=[{"Key": "AcceptanceRun", "Value": self.m["run"]}]
        )
        self.tags(arn)
        self.register(source, remote, backup)
        self.checkpoint(
            "application-before-source-deletion",
            {
                "source": source,
                "remote": remote,
                "backup": backup["metadata"],
                "first": first,
                "second": second,
                "independent_comparison": initial,
            },
        )
        # Recheck both exact recovery times immediately before irreversible deletion.
        current = self.r.database(self.control)
        if current["DbiResourceId"] != source["DbiResourceId"] or any(
            not self.backup(current, "active", point) for point in (point1, point2)
        ):
            raise RuntimeError("Exact source or recovery window changed before deletion")
        self.delete_owned(source, retain=True, points=(point1, point2))
        for _ in range(40):
            self.r.gate()
            if all(self.backup(source, "retained", point) for point in (point1, point2)):
                break
            time.sleep(15)
        else:
            raise RuntimeError("Both retained recovery windows unverified; no fallback")
        restored = self.restore(source, remote, point1, "behind")
        binding = {
            "RESTORED_HOST": restored["Endpoint"]["Address"],
            "SOURCE_DATABASE": first["source_database"],
            "RECOVERY_POINT": point1.isoformat(),
            "RECOVERY_PHASE": "behind",
        }
        blocked = self.r.task("receipt", extra=binding)
        if (
            blocked.get("stage") != "behind_blocked"
            or blocked.get("worker_claimed") is not False
            or blocked["verification"]["safe_to_resume"] is not False
            or blocked["remote_ledger_sha256"] != fingerprint
        ):
            raise RuntimeError("Actual blocked application recovery required")
        independent = self.r.task(
            "verify", extra={"RESTORED_HOST": binding["RESTORED_HOST"]}, expected_exit=2
        )
        self.verify_result(independent, 3, 5, False, fingerprint)
        self.checkpoint(
            "application-behind-independently-blocked", {"result": blocked, "independent": independent}
        )
        self.require_headroom(3600)
        self.delete_owned(restored, retain=False)
        matching = self.restore(source, remote, point2, "matching")
        if matching["DbiResourceId"] == restored["DbiResourceId"]:
            raise RuntimeError("Second restore must have a different physical generation")
        binding.update(
            RESTORED_HOST=matching["Endpoint"]["Address"],
            RECOVERY_POINT=point2.isoformat(),
            RECOVERY_PHASE="matching",
        )
        resumed = self.r.task("receipt", extra=binding)
        if (
            resumed.get("stage") != "matching_resumed"
            or resumed.get("old_completion_accepted") is not False
            or type(resumed.get("duplicate_business_effects")) is not int
            or resumed["duplicate_business_effects"] != 0
            or resumed.get("independent_database_identity_unchanged") is not True
        ):
            raise RuntimeError("Fresh application approval, resumption and receipt recovery required")
        final = self.r.task("verify", extra={"RESTORED_HOST": binding["RESTORED_HOST"]})
        self.verify_result(final, 6, 6, True, resumed["remote_ledger_sha256"])
        if self.r.database(self.remote)["DbiResourceId"] != remote["DbiResourceId"]:
            raise RuntimeError("Independent receipt database was replaced")
        result = {
            "status": "NATIVE_APPLICATION_RECOVERY_ACCEPTANCE_PASSED",
            "workflow": "application-recovery-v1",
            "synthetic": True,
            "full_production_application_acceptance": False,
            "maximum_concurrent_instances": 2,
            "first_recovery_point": point1.isoformat(),
            "matching_recovery_point": point2.isoformat(),
            "blocked": blocked,
            "resumed": resumed,
            "independent_final": final,
            "records": self.r.records,
            "phases": self.phases,
        }
        self.checkpoint("application-recovery-independently-accepted", {"result": result})
        return result
