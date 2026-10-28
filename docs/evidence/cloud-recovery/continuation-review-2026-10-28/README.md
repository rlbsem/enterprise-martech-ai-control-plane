# Recovery failure investigation

The original [October 28 native result](../native-2026-10-28/README.md) remains failed and unchanged. Its first restored database reached `available`, but no post-restore application verdict was accepted. A new native run has not been provisioned.

## Findings

1. **The historical task failure is still unresolved.** Seven successful `DescribeTasks` calls are visible in CloudTrail, but their response bodies are null. They cannot establish the stopped container's exit code, image digest or error. The controller recorded its own rejection and subsequent failure-archive readback. Session interruption is not an established explanation. [Executed CloudTrail review](cloudtrail-review.json).
2. **A PostgreSQL compatibility defect was independently reproduced locally.** A legal role expiry of `infinity` caused psycopg's timestamp decoder to fail while fingerprinting permissions. Casting that catalog field to text preserves its value without timestamp conversion. Regression cases cover positive/negative infinity and a finite expiry. This proves a defect, **not the cause of the historical AWS failure**. [Reproduction](catalog-compatibility.json).
3. **The cleanup policy denial is established.** All 25 inspected deregistration attempts were denied because AWS evaluated `ecs:DeregisterTaskDefinition` on resource `*`; the policy allowed task-definition ARNs. The corrected guard grants only this action on `*`, restricted to Ohio. Other destructive actions retain their existing resource scopes. The guard still selects exact run-owned families. This regional permission is broader than an ARN scope and must be disclosed in the fresh deployment review. It has not been deployed. [Request receipts](cloudtrail-review.json).

## Corrections and execution boundary

The application controller now requires `source-preflight-v2`. After bootstrap, a finite task exercises coordinator permissions, real API/receiver startup, an empty worker poll and reader write denials. Recovery probe writes are rolled back; the gate, fingerprints and both empty ledgers must remain unchanged before the synthetic workload begins. This catches catalog and role failures before destructive recovery transitions.

Task launch context is archived before waiting. Launch denials, waiter/stop failures, stopped-task expectations and actual metadata retain request receipts. Bounded child-process diagnostics redact credentials and private addresses and explicitly report truncation. Failed archives cannot become acceptance. Stable task tokens limit accidental duplicate launches; a conditional durable run-start claim refuses silent restart of an interrupted application experiment. Expired authority rejects a completed task even after its diagnostics are preserved.

These are software corrections and local/offline contract checks. They do not close the native reconciliation or resumption gap. A fresh reviewed plan, actual two-restore execution and independent cleanup verification remain required.

The complete [local regression](../continuation-regression-2026-10-28/verification.json) executed **397 tests with zero failures, errors or skips** on PostgreSQL 17.11, including separate HTTP processes. All [14 infrastructure commands](../continuation-infrastructure-2026-10-28/verification.json) passed: Terraform validation, mocked deployment assertions, configuration security and secret scanning. These records identify the exact source hashes tested; AWS SDK contract tests do not provision or establish native acceptance.

## Current read-only observations

Short-lived local CLI/SDK access now works. AWS reports **FREE/ACTIVE with USD 139.52 remaining**. Independent inventory finds no active resources from the previous runtime; the five task-definition deletion records have disappeared. Six secrets, one runtime customer key and the separate guard key remain scheduled for deletion. Delayed credit reporting is not a final bill or billing clearance. No account-plan change or new provisioning occurred. [Observations](read-only-observations.json).

Fresh Ohio [Pricing API observations](pricing-observations.json) match the six quoted RDS, Fargate and endpoint rates in the existing USD 11.2868 conservative resource estimate. That estimate includes a four-task-hour envelope, storage, retained backups, whole-month key/secret allowances and cleanup contingency; it is not a hard spending cap.

[Preservation check](preservation.json) retains all 66 previously manifested artifacts except the current package index byte-for-byte. The [original controller source](../native-controller-source-archive/manifest.json) is archived at its exact executed revision so later diagnostic changes cannot rewrite its binding.
