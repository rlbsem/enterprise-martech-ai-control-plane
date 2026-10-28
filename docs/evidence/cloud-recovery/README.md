# Cloud recovery acceptance

This package separates new recovery verification from historical AWS observations.

The recovery harness executes all eight local scenarios against real PostgreSQL databases and independent HTTP/worker processes. Separately, the [completed native application acceptance](native-application-2026-10-28/README.md) executed two AWS RDS PITR restores, independently verified the early blocked 3/5 state and safely resumed to six matching intents/effects. Offline AWS controller tests remain distinct from that actual native execution. Earlier failed observations remain visible and unchanged.

**Latest local regression: 407 passed, zero failures/errors/skips.** This includes native-record integrity and tampering rejection, pre-provisioning deadline admission, legal PostgreSQL role expiries, rollback-only source preflight, interrupted waits, expired authority, durable restart rejection, bounded diagnostic redaction and preservation of failed native evidence. These software tests are separate from the nine actual native task executions.

| Record | Executed scope |
|---|---|
| [Completed native application acceptance](native-application-2026-10-28/README.md) | Actual two-restore AWS experiment, nine source-bound tasks, independent 3/5 blocked and 6/6 safe comparisons, fresh approval, current consent, stale lease fencing and zero duplicate effects |
| [Independent native teardown](native-application-2026-10-28/teardown.json) | Active runtime and guard absent; 73 archived object versions rehashed; pending secrets, keys and task-definition deletions remain explicit; no final billing clearance |
| [Deployment admission abort](native-admission-2026-10-28/README.md) | Separate approved guard/runtime run; stopped during partial provisioning when the operator check missed insufficient headroom; no application task or restore executed; [independently checked cleanup](native-admission-2026-10-28/teardown.json) |
| [Failure investigation and continuation checks](continuation-review-2026-10-28/README.md) | Actual local PostgreSQL compatibility reproduction and read-only AWS/CloudTrail observations; historical task cause still unresolved; no replacement runtime provisioned |
| [Native application attempt and cleanup](native-2026-10-28/README.md) | Actual AWS source application: five intents/effects and two reader write denials; one retained-backup restore observed available; post-restore acceptance failed, second restore unexecuted; independent cleanup and pending deletions recorded |
| [Latest native-record regression](native-final-regression-2026-10-28/verification.json) | 407 tests, actual local PostgreSQL/HTTP plus offline AWS contracts and six checks of the genuine native record; no new AWS experiment |
| [Earlier admission regression](timing-admission-regression-2026-10-28/verification.json) | Original 401-test record, unchanged; rejects stale runtime approvals before Terraform provisioning |
| [Earlier continuation regression](continuation-regression-2026-10-28/verification.json) | Original 397-test record, unchanged; earlier 368/370/385-test records also preserved |
| [Latest infrastructure checks](continuation-infrastructure-2026-10-28/verification.json) | 14 successful commands, including the Ohio-only deregistration exception and source-preflight protocol; mocked/static verification only |
| [Earlier diagnostic regression](diagnostic-regression-2026-10-28/verification.json) | Original 385-test record, unchanged |
| [Structured recovery proof](local-2026-10-27/acceptance.json) | Eight actual local snapshot/HTTP/process scenarios; exact source `5720f56276b208c823e1e7383b1a74c468c6c8e8`; five original effects, two missing early-restored intents, six effects after one new authorized action |
| [Standalone operator report](local-2026-10-27/operator-report-v2.html) | Download/open as HTML; read-only expandable evidence, not a live cloud deployment |
| [Current desktop/mobile captures](local-2026-10-27/browser-03/provenance.json) | Actual Chromium 156 rendering, both widths, evidence drilldowns and keyboard access; no external requests or page errors; earlier rendering retained unchanged |
| [Regression verification](regression-2026-10-27/verification.json) | 368 tests, real PostgreSQL and separate HTTP servers; [source binding](regression-2026-10-27/execution-binding.json) pins `bfa3e00ea17871b163f90d8874ddf42bceb348b7` and 98 source files |
| [Controller regression](regression-2026-10-28/verification.json) | 370 tests, zero failures/errors/skips; [source binding](regression-2026-10-28/execution-binding.json) pins `58fc9837e3fd05e0d0fab2a1029b31d56c842e4d`; actual local PostgreSQL/HTTP with mocked AWS controller contracts |
| [Fresh regression recovery output](regression-2026-10-27/cloud-recovery.json) | Another actual execution of all eight scenarios under that regression source; separate from the earlier standalone capture |
| [Infrastructure verification](infrastructure-2026-10-27/verification.json) | 14 successful commands: four Terraform roots, mock-provider assertions, HIGH/CRITICAL configuration checks and secret scan; [21-file source binding](infrastructure-2026-10-27/execution-binding.json) |
| [Integrity rejection checks](integrity-negatives-2026-10-27.json) | Three controlled private-copy failures: changed artifact bytes, an unlisted file and a false source binding all rejected; originals unchanged |
| [Reduced native plan](native-plan-2026-10-27/README.md) | Authenticated read-only AWS preflight and USD 11.2868 conservative estimate; no new resources or native recovery claimed |

The matching-state procedure explicitly calls the restricted coordinator's approval operation with the exact fresh proof. In the synthetic acceptance, the finite driver exercises this operator authority; it does not claim a human approval UI or a staffed production process.

![Executed recovery operator report](local-2026-10-27/browser-03/desktop.png)

[Implementation and reproduction](../../cloud-recovery.md) · [Original native outcome](../aws-readiness/retained-two-instance/native-outcome/README.md) · [Prior runtime source bindings](prior-runtime/README.md)

The recovery gate and versioned native application lane change source code previously referenced by immutable workload records. The [original nine-file source archive](prior-runtime/README.md) remains unchanged. One [additional watchdog source archive](diagnostic-source-archive/manifest.json) preserves its exact former bytes after the diagnostic correction. Workload observations, earlier source maps and hashes are unchanged. Earlier workload timings do not measure the new gate. New verification identifies its own source revision and file hashes.

The new native run closes the post-restore reconciliation and safe-resumption gap within the reduced one-off task lane. It does not establish full production ECS-service/HTTPS/Multi-AZ acceptance. Independent observations confirm active runtime absence and guard retirement. Pending keys/secrets/task definitions and delayed account-level credits remain separate from billing clearance. No automatic experiment retry or recurring account monitoring is configured.

The [preservation comparison](preservation-2026-10-28.json) verifies that the previous 41 record/source/capture files retain their exact bytes. Only this current index changed; the older package manifest was regenerated to include new artifacts.

Recheck this package with `python scripts/check_recovery.py`. It validates the complete [artifact manifest](artifact-manifest.json), pinned executed Git sources, standalone browser provenance and full regression counts. The infrastructure CI job fetches complete history and runs it separately from current-code tests. Existing October 19 native evidence and its limitations remain unchanged.
