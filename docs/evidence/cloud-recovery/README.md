# Cloud recovery acceptance

This package separates new recovery verification from historical AWS observations.

The recovery harness executes all eight scenarios against real PostgreSQL databases and independent HTTP/worker processes. The versioned native application tasks also execute against local database restores. AWS controller tests use service contracts/mocks; they are not native cloud results. The [October 28 native application attempt](native-2026-10-28/README.md) reached an available PITR restore but failed the next task's acceptance check. Infrastructure availability is never substituted for application recovery.

**Latest local regression: 401 passed, zero failures/errors/skips.** This includes pre-provisioning deadline admission, legal PostgreSQL role expiries, rollback-only source preflight, interrupted waits, expired authority, durable restart rejection, bounded diagnostic redaction and preservation of failed native evidence.

| Record | Executed scope |
|---|---|
| [Deployment admission abort](native-admission-2026-10-28/README.md) | Separate approved guard/runtime run; stopped during partial provisioning when approval wait left insufficient controller headroom; no application task or restore executed; cleanup observations recorded separately |
| [Failure investigation and continuation checks](continuation-review-2026-10-28/README.md) | Actual local PostgreSQL compatibility reproduction and read-only AWS/CloudTrail observations; historical task cause still unresolved; no replacement runtime provisioned |
| [Native application attempt and cleanup](native-2026-10-28/README.md) | Actual AWS source application: five intents/effects and two reader write denials; one retained-backup restore observed available; post-restore acceptance failed, second restore unexecuted; independent cleanup and pending deletions recorded |
| [Latest admission regression](timing-admission-regression-2026-10-28/verification.json) | 401 tests, actual PostgreSQL/HTTP plus offline AWS contracts; rejects stale runtime approvals before Terraform provisioning |
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

Native source application execution and one retained-backup restore are recorded; **native post-restore reconciliation and safe resumption remain incomplete**. The reduced one-off task lane does not establish full production ECS/HTTPS/Multi-AZ acceptance. The guard was retired after private archival and independent active-resource absence checks. Pending keys/secrets/task definitions and delayed account-level credits are explicitly separate from billing clearance.

The [preservation comparison](preservation-2026-10-28.json) verifies that the previous 41 record/source/capture files retain their exact bytes. Only this current index changed; the older package manifest was regenerated to include new artifacts.

Recheck this package with `python scripts/check_recovery.py`. It validates the complete [artifact manifest](artifact-manifest.json), pinned executed Git sources, standalone browser provenance and full regression counts. The infrastructure CI job fetches complete history and runs it separately from current-code tests. Existing October 19 native evidence and its limitations remain unchanged.
