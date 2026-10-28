# Operational recovery and business integrity

A database can restore successfully while its application is unsafe to reopen. Independent downstream effects may be newer than restored intents, a saved approval may be stale, and a restored consent record may no longer describe current permission.

The recovery coordinator adds durable sessions and a closed admission/dispatch gate to the existing control plane. It reuses canonical source ingestion, the leased worker, independent HTTP receiver and receipt reconciler. It does not insert missing intents to make a comparison pass.

## Executed local acceptance

The executable acceptance creates separate PostgreSQL control and receipt databases. It stops control writers and captures actual PostgreSQL database copies using `CREATE DATABASE ... TEMPLATE`, then allows more effects to commit. It terminates a real worker after the independent receiver commits, restores the earlier control copy into a new database and starts recovery with work closed.

This is a real local database snapshot/restore with actual HTTP and process boundaries. It is **not WAL replay, native RDS PITR, or a production RPO/RTO measurement**.

| Scenario | Required and executed behavior |
|---|---|
| Restored control behind downstream | Three restored intents versus five independent effects; two missing intents keep recovery closed; no dispatch |
| Lost acknowledgment | Stable original key and matching independent receipt recover the historical effect without another mutation |
| Worker terminated after commit | Actual process termination, naturally expired lease, replacement claim and rejected completion using the old lease token |
| Conflicting mutation | A deliberately corrupted control copy is blocked; the original independent receiver is unchanged |
| Missing acknowledged receipt | A separate negative-test copy loses one receipt; the original receiver remains intact; recovery is blocked |
| Authorization changed | Fresh authenticated owner-source observations are required after recovery starts; revoked consent blocks new marketing while committed historical effects remain recognized |
| Valid controlled resumption | A later complete control snapshot matches the unchanged receiver; fresh verification and exact approval open the gate; existing uncertain work reconciles and one new authorized effect commits |
| Stale approval | A real source update after approval invalidates the earlier proof and prevents reopening |

The source-bound local run observes five original effects and six after controlled new work, with no rekeyed or duplicate business effects. Nine run-owned databases and six run-owned roles are removed. Negative findings remain in the structured result. Regression, browser checks and native AWS observations have separate records in the [evidence package](evidence/cloud-recovery/README.md).

## Durable gate

Migration 3 adds `cp.recovery_sessions`, append-only `cp.recovery_proofs` and `cp.recovery_gate`. Migrations 1 and 2 retain their original bytes. New work and dispatch authorization acquire the gate's shared transaction lock. Closing and reopening acquire its exclusive lock. SQL triggers also reject direct new proposal/job insertion while closed.

Source truth can still refresh through its existing authenticated authority boundary while work is closed. Source writes share the gate lock, so they cannot straddle the coordinator's reopen transaction. Fresh consent/privacy provenance must reference source events received after the recovery session began; an old restored permission flag is insufficient. This relies on the authoritative source's truth, not a claim of independent verification of a compromised source.

A proof binds the selected recovery point, source/restored database identities, exact deployment fingerprint, policy, all persisted control business/configuration rows, relevant role/table privileges and the independent receiver snapshot. A live process/task witness must establish registered-runtime quiescence. The coordinator re-reads state during approval and again during resumption, rejects changed or expired proofs and requires the exact operator-approved proof ID. An edited JSON report is not an execution authority.

The deployment verifier uses `runtime_acceptance_snapshot`: a closed gate is a failed deployment acceptance even if the separate receipt comparison is internally consistent. Receipt inspection while closed and permission to run services are deliberately different decisions.

## Authorities and limitations

- The recovery coordinator can write operational recovery metadata, not customer facts or downstream effects.
- The independent verifier reads control state and receiver rows. It cannot dispatch, edit proof history or reopen the gate.
- Runtime children receive their own API, worker or receiver credentials; owner credentials are excluded.
- Actual database connections are compared with the bound restored and independent database identities on every observation.
- Both stores must be quiesced before comparison. There is no cross-database atomic snapshot or protection against an untrusted database/infrastructure administrator.
- Resume approval authorizes the checked deployment fingerprint. Normal dispatch still rechecks current proposal, revision, policy, consent and suppression; a receipt only establishes historical execution.
- The independent receiver commits its synthetic business effect and receipt atomically. These results do not establish exactly-once behavior for an arbitrary external vendor.

## Reproduce without AWS

Use a disposable local PostgreSQL cluster and a database ending `_test`. The harness rejects external hostnames and never restores or deletes the supplied database. Only fresh run-owned database names are created/copied/dropped.

```bash
export CONTROL_RECOVERY_ADMIN_DSN='host=127.0.0.1 port=5432 user=postgres dbname=control_test'
python scripts/recovery_acceptance.py --confirm-isolated-postgres --output work/recovery-run-01
python scripts/verify.py work/recovery-regression
```

Install the locked dependencies as described in the main README. The harness refuses an existing output directory. Raw process logs remain private under `.local`; structured reports contain synthetic business records and hashes. The test suite also executes the complete harness. PostgreSQL itself performs database copies, so no mismatched `pg_dump` client is required.

Implementation: [coordinator](../src/controlplane/recovery.py), [additive migration](../src/controlplane/recovery.sql), [actual recovery harness](../src/controlplane/recovery_lab.py), [tests](../tests/test_recovery_acceptance.py), [independent comparisons](../src/controlplane/operations.py).

## Native AWS boundary

The [October 19 native record](evidence/aws-readiness/retained-two-instance/native-outcome/README.md) remains unchanged. The [October 28 application attempt](evidence/cloud-recovery/native-2026-10-28/README.md) independently reconciled five intents/effects before a real retained-backup restore reached `available`. The subsequent application task failed its stopped-task check, and its final metadata/logs were not retained by that controller before scheduled deletion. The precise task failure cause and restored-state verdict are not established. No second restore or safe native resumption was accepted. Local acceptance does not close this gap; full production-style ECS/HTTPS/Multi-AZ recovery also remains unexecuted.

The reduced native lane has a USD 20 planned-consumption ceiling, conditional on fresh Free-plan, credit, eligibility, quota, cost and cleanup checks. It does not authorize a Paid upgrade, billing changes, production use or out-of-pocket charges. Native execution, if feasible, requires a new source-bound record and independent cleanup/cost observations. No infrastructure is provisioned by this local harness or ordinary PR verification.

### Versioned native application lane

`infra/acceptance-runtime` defaults to the original `sql-fixture-v1`. Selecting `application-recovery-v1` runs the new [application tasks](../src/controlplane/native_app_tasks.py) and [two-restore controller](../src/controlplane/native_application.py). Production infrastructure and its region are unchanged.

The driver runs the actual API, worker and independent receiver in separate processes, with separate SQL logins. HTTP remains inside an isolated one-off task; both PostgreSQL connections verify the RDS CA and hostname. The driver is trusted and can obtain the finite non-owner runtime credentials. Only bootstrap reads managed database-owner secrets; the independent verifier reads only its own read-only credentials. This does not reproduce separate production ECS services, private HTTPS ingress or a production failure domain.

Restored endpoints are independently resolved from the exact RDS target before scoped connection strings are rebound. Existing application/coordinator SQL passwords come from the restored state and their unchanged runtime secrets. The [PITR managed-password option is Oracle-only](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_RestoreDBInstanceToPointInTime.html); PostgreSQL restore requests omit it and do not assume a new owner secret. Any owner-secret metadata actually returned is tracked for cleanup; the restored application does not need owner credentials or an administrative password reset.

The protocol creates three effects, terminates one real worker after receiver commit, establishes an earlier PITR point, then commits two additional effects. Both exact recovery times, an available earlier snapshot, encryption, retention, tags, quiescence and durable read-back checkpoints must pass before deleting the disposable original control instance with retained automated backups. The independent receipt instance is never deleted or restored during recovery.

An early restore must produce three local intents versus five remote effects, a closed gate and two missing-intent findings in an independent verifier task. Only after that result is durably captured may the first restored control instance be deleted and the same retained source restored to the later complete point. A new physical generation is required. Fresh source observations, rejected stale approval, exact fresh approval, receipt-first reconciliation, a fenced old completion and one new authorized effect precede the final separate read-only comparison of six intents and six effects. No third concurrent RDS instance is permitted and no missing intent is fabricated.

The fixed cleanup guard starts three hours after deployment starts and retries for one additional hour. It has a separate read-only checker and private versioned evidence archive. Every managed owner secret is retained in the registry across target-name reuse. Exact known secret metadata is checked directly when eventually consistent listings omit it; access denial is never absence. Pending secret/key deletion and delayed usage remain separate from completed cleanup and billing clearance.

Application runtime deployment reserves 165 minutes before Terraform can create resources: the unchanged 150-minute controller start requirement plus a 15-minute provisioning allowance. Approval wait consumes the already-running guard window. A late approval must be rejected before provisioning; the deadline cannot be extended. The [separate admission abort](evidence/cloud-recovery/native-admission-2026-10-28/README.md) records the operator check that originally failed to enforce this requirement.

Following the failed native task, the controller was changed to archive stopped-task metadata and logs before acceptance checks. Private checkpoint and encrypted cloud readbacks must match before acceptance can proceed. Scheduled cleanup now archives a bounded, paginated log-group observation before deletion; truncation, budget exhaustion or a failed readback preserves the log group and reports a cleanup failure. Raw logs stay private. Cleanup failure records retain service codes, request IDs and hashed resource references, excluding exception messages. The checker gained no log-body access. These changes are locally testable safeguards, not a rerun or an explanation of the historical failure.

The [subsequent investigation](evidence/cloud-recovery/continuation-review-2026-10-28/README.md) reproduced a legal PostgreSQL infinite-role-expiry decoding failure locally and established the old cleanup policy's deregistration denial through CloudTrail. Neither finding establishes the earlier container's unknown failure cause. The current application manifest requires `source-preflight-v2`: a rollback-only coordinator/permissions probe and real API/receiver/empty-worker check before the first business effect or control deletion. A conditional durable run-start claim rejects silent controller restarts. Task diagnostics are archived from launch onward, and authority is rechecked after completion. Only `ecs:DeregisterTaskDefinition` needs the guard's disclosed Ohio-only wildcard permission; the independent checker stays read-only. These corrections require fresh native execution and approval.

The [credit estimate](evidence/cloud-recovery/native-plan-2026-10-27/README.md) is generated by [bounded cost arithmetic](../src/controlplane/recovery_cost.py), including storage, retained backups, private endpoints, logs, secrets, keys and cleanup allowances. It is a conservative plan, not a hard billing cap. AWS account-plan/credit reads and the fixed resource/time envelope govern progress; ordinary CI never enables billing or invokes native deployment. The native lane requires its own executed acceptance; local tests and mock-provider plans do not establish cloud success.

[Evidence preservation and status](evidence/cloud-recovery/README.md) · [Existing production operations](cloud-operations.md)
