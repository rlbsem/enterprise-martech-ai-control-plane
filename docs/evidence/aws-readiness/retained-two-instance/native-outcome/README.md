# Native retained-backup recovery — bounded AWS execution closeout

**Execution date:** October 19, 2026 (UTC) · **Environment:** isolated AWS us-east-2, synthetic PostgreSQL data · **Result:** native restore reached `available`; the required post-restore fail-closed application verification **was not recorded**.

This report reconciles the *second* AWS experiment with the previously checked-in two-instance candidate. It does not replace the earlier [native SQL experiment and three-instance quota rejection](../../native-sql/README.md). The first experiment's `InstanceQuotaExceeded` failure remains historical evidence, not a failure of the two-instance strategy.

## Evidence boundary

| Verification boundary | Observed outcome |
|---|---|
| Authenticated private Single-AZ RDS runtime | Two encrypted PostgreSQL micro databases became available; no more than two instances were observed in the bounded run |
| Database fixture and permissions | The work-session execution record reports an empty-ledger bootstrap, read-only permission denials, and 1-intent/1-independent-receipt reconciliation |
| Duplicate replay | Work-session record reports one independent effect after receipt replay |
| Source deletion and backup retention | Source-control deletion accepted with `DeleteAutomatedBackups=False`; the private checkpoint series includes `before-source-deletion` and `retained-backup-observed` |
| Actual retained-backup PITR | Restore was accepted from the deleted source's retained backup; the independent guard inventory shows **two available RDS instances** at 2026-10-19T06:14:57Z, consistent with an available restored control and the independent receipt database |
| Native restored-control reconciliation | **Unverified.** No durable `restored-fail-closed-verified` checkpoint and no verified read-only comparison reporting `remote_ahead_missing_intent`, `local_intents=0`, `remote_effects=1`, `safe_to_resume=false` |
| Full application acceptance | **Not executed.** No claim of production API, continuous worker, HTTP ingress, Multi-AZ failover, release rollout, or production traffic acceptance |

The native controller encountered and stopped on real service-contract edge cases: the automated-backup `Region` representation (`use2` versus `us-east-2`), omission of `BackupRetentionPeriod` on retained-backup metadata, and the RDS restore API rejecting `MaxAllocatedStorage=0`. The work-session continued with corrective edits and ultimately obtained an available restored instance. **Those host-side fixes were not committed back to this PR before Work was interrupted**; this branch's pre-run controller and local mocks should not be represented as the exact executable that succeeded in AWS. The omitted final reconciliation also means the recovery acceptance gate did not pass.

## Recovery evidence provenance

The owner preserved **private**, individually versioned AWS evidence before retiring the guard. A verified off-cloud archive contains 728 object versions and two delete markers, including four retained-recovery checkpoint objects, AWS independent inventory reports and private Terraform state. For example, the guard report at 2026-10-19T06:14:57Z recorded both database instances as `available`, and reports starting 2026-10-19T06:15:48Z observed database deletion in progress. These records support restore availability and subsequent cleanup, **not** the missing application-level reconciliation result.

A separate private supplement preserves 60 dead-letter cleanup messages and 1,263 Lambda log events. These evidence archives intentionally are **not committed to GitHub** because they include sensitive infrastructure metadata and state. Raw account IDs, instance identifiers, full ARNs, AWS request IDs, secret values, private plan/state, and presigned links are not public proof artifacts.

## Independent cleanup and cost boundary

On October 19, independent AWS reads confirmed zero RDS instances, snapshots/automated backups, ECS clusters, ECR repositories, test VPC endpoints or active task definitions. The expired cleanup schedule was removed; both disabled guard Lambdas, their log groups, guard IAM roles, queue, alarm and versioned evidence bucket were deleted. The guard encryption key was scheduled for deletion on October 26. Ten secrets and four acceptance-related customer-managed keys remained in AWS's regular **pending-deletion** state at the final check. The only remaining VPC in the regional inventory was the account's pre-existing default VPC, not the test network.

AWS reported the account as `FREE/ACTIVE` with promotional credits remaining. **Delayed usage and final billing clearance were not established.** No Paid-plan upgrade or out-of-pocket AWS authorization was granted. No automated AWS monitoring or new infrastructure execution is requested by this report.

## Code, testing, and review status

The committed candidate at head `adc8496` previously passed 273 local tests, 16 Terraform mock runs and both hosted CI workflows. Those outcomes apply to that committed source and its historical artifacts, not the uncommitted native host-side corrections. The existing [candidate record](../README.md) and its artifact manifest are deliberately left as historical, hash-bound, **pre-execution** evidence.

**Acceptance disposition:** PASS — original native SQL/independent receipts and real retained-RDS restore availability. NOT VERIFIED — native restored-state fail-closed gate. NOT EXECUTED — production application/release acceptance. Runtime and guard teardown independently observed; pending deletions and delayed billing excluded from any zero-cost assertion.

**Merge review requirement:** reconcile the real AWS-discovered service-contract fixes into source, add regression tests, regenerate affected local verification evidence and content hashes, run hosted CI, and ensure no public material misstates the native boundary. Do not provision cloud resources solely to complete the PR.
