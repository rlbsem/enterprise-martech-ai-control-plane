# AWS metadata review and recovery evidence hardening

**October 20, 2026.** Current main was `575de15`, which includes merged [PR 4](https://github.com/rlbsem/enterprise-martech-ai-control-plane/pull/4) at `c786b73`. This review makes no new native recovery claim. The [October 19 native outcome](../aws-readiness/retained-two-instance/native-outcome/README.md) remains authoritative and unchanged.

## Fresh native observations

Authenticated AWS Core calls succeeded against the existing account. Free Tier metadata reported `FREE/ACTIVE` with credits remaining; exact account identity and financial balance are retained privately. The [normalized observation](observation.json) records successful regional metadata reads and their actual observation times.

In **us-east-2**, no RDS instances, snapshots or automated backups, ECS clusters/task definitions in any lifecycle state, ECR repositories, VPC endpoints, network interfaces, cleanup functions/schedules, native-run IAM roles or custom RDS subnet/parameter groups were observed. The pre-existing default VPC and default PostgreSQL parameter group remain. Four experiment-owned customer-managed keys remain `PendingDeletion` with scheduled dates on October 25–26 UTC; two AWS-managed keys are excluded from experiment cleanup. This is a dated observation of the listed services, not account-wide or cross-region resource absence.

Secrets Manager pending-deletion state, S3 object/bucket absence, SQS queues, CloudWatch alarms and final billed usage were **not freshly verified**. Request-based or fee-uncertain checks were excluded from further inspection under the owner's financial restriction. No Cost Explorer, CloudTrail Lake, S3 object, secret-value, KMS cryptographic, workload-execution or mutating operation was performed. KMS list/describe metadata operations were performed; this record is not a zero-request-cost certificate. The Free Tier usage endpoint returned no records, which is not a billing clearance or experiment cost attribution. AWS [Free Tier state](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_freetier_GetAccountPlanState.html), [KMS pricing](https://aws.amazon.com/kms/pricing/) and [Secrets Manager pricing](https://aws.amazon.com/secrets-manager/pricing/) describe these separate boundaries.

No AWS resource was created, restored, deleted or reconfigured; no account upgrade or recurring monitoring was started. Prior pending-secret observations must not be relabeled as current absence. Pending KMS deletion also remains distinct from completed deletion; AWS documents no key-storage charge while scheduled deletion remains in effect, and cancellation would change that treatment.

## The remaining native acceptance gap

The preserved experiment established real independent SQL receipt reconciliation, replay safety, SQL permission checks, retained backup behavior and restored-instance availability. It did **not** record the final read-only reconciliation against restored control and the unchanged receipt database. The required result is exactly `remote_ahead_missing_intent`, zero local intents, one remote effect, `safe_to_resume=false`, two effective write denials and unchanged receipt-content fingerprint, observed twice.

There is no surviving RDS instance or retained backup in the checked region to finish that old check. Reproducing it would require another chargeable native experiment, including replacement databases and an independent receipt ledger. None is authorized or attempted here. Full ECS/production application recovery also remains unexecuted. Metadata reads cannot establish either missing result.

The already merged compatibility fixes were reviewed: accept RDS's `use2` Ohio alias while pinning full ARNs; permit omitted retention only for an otherwise pinned retained backup with validated one-day source retention; omit the service-rejected zero `MaxAllocatedStorage` restore argument and reject unexpected inherited autoscaling. Their regression tests passed locally again. This still does not mean those committed fixes have been rerun in AWS.

## Correctness changes

The existing [retained controller](../../../src/controlplane/native_retained.py) previously tested truthiness and numeric equality at its reconciliation boundary. Python considers `False == 0` and `True == 1`; a null restored verdict was also falsy. A malformed task result could therefore satisfy the final recovery gate without an explicit boolean verdict. The pre-deletion gate likewise accepted truthy substitutes. The controller now requires exact boolean verdicts, integer counts, integer write-denial count, valid fingerprint and internally consistent findings before deleting control or recording acceptance.

After validating restored-instance availability, configuration and distinct physical identity, the controller now writes and independently reads back `restored-instance-available` **before** dispatching a restored verification task. This checkpoint explicitly says reconciliation is not yet verified. Each of the two fully validated read-only observations gets its own checkpoint. Only both observations permit `restored-fail-closed-verified`. Task interruption, malformed results or checkpoint read-back failure cannot be promoted to final success; an earlier valid stage remains available for diagnosis.

The same private encrypted checkpoint mechanism and existing permissions are reused. The future native sequence writes additional objects under the existing recovery prefix; that is not executed or financially authorized by this change. No production Terraform, IAM policy, source economics, receipt identity, cleanup deadline or resource count was changed.

## Actual local validation

[Local verification](local-verification.json) records **298 passing tests**, no failures/errors/skips, against PostgreSQL 17.11 and separate local HTTP processes. The focused [retained recovery suite](../../../tests/test_retained_recovery.py) passed all **50 tests**. It includes the reconstructed AWS contracts, malformed original/restored verdicts, first/second verification-task interruption and checkpoint read-back failure. These SDK state-machine tests use fakes; real PostgreSQL tests cover receipt content and reconciliation locally. Neither is native RDS execution.

The original AWS evidence, historical local/static artifacts and their manifests are unchanged. Repository integrity checks validate those artifacts against their original Git revision; they are not rebound to this new controller. Current GitHub CI independently tests this branch. Consult the PR checks for hosted status rather than treating this local record as a GitHub execution certificate.

To reproduce without AWS access, install `requirements.lock` and the package in an isolated Python 3.12 environment, provide `TEST_ADMIN_DSN` for a disposable local PostgreSQL database whose name ends in `_test`, then run:

```sh
python scripts/verify.py work/review-proof
python scripts/repo_check.py
```

The tests destroy only that explicitly supplied local test database's schemas. Do not point them at RDS or production data. The first command captures new local proof under ignored `work/`; neither command provisions AWS resources.
