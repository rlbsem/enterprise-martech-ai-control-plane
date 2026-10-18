# Isolated Ohio native acceptance implementation

**The isolated guard and image bootstrap have deployed and passed native arming/checker and exact image read-back checks; database/task runtime provisioning, SQL/PITR and teardown have not executed.** See [native guard evidence](evidence/aws-readiness/guard-native/README.md).
This lane supplements the unchanged production design. It proves only managed PostgreSQL,
independent SQL receipts and recovery refusal if the native run succeeds. It does not prove
live API dispatch, worker recovery, HTTPS ingress, Multi-AZ failover or a production deployment.

## Executable components

| Component | Implementation |
|---|---|
| Isolated runtime | `infra/acceptance-runtime`: two private encrypted PostgreSQL 17.11 `db.t4g.micro` instances, 20 GB gp3 each, 14-day backups, Single-AZ, deletion protection, forced TLS; separate VPC/two private subnets; four interface endpoints in the task AZ and S3 gateway; five one-off task definitions, no ECS services |
| Independent guard | `infra/acceptance-guard`: non-VPC cleanup Lambda and separate read-only checker Lambda, independent IAM roles/KMS key/private versioned evidence bucket, enabled one-minute Scheduler at the cleanup deadline, bounded delivery/invocation retries, encrypted failure queue and observable queue alarm |
| Arming gate | `native_arming.py` reads STS, Free-plan state, actual Scheduler and Lambda metadata. It verifies exact namespace, account, code hash, deadline/end time, target identity, retry/DLQ configuration and independent checker role. An arming metadata check is not a claim that AWS has executed cleanup or simulated its permissions. |
| Provisioning gate | `scripts/native_deploy.py` verifies an approved saved-plan digest, create-only root changes, manifest/plan agreement, Free-plan credit headroom and independently armed guard before runtime apply. No automatic upgrades or account-plan changes. |
| Native experiment | `python -m controlplane.native_run`: fresh empty-ledger check, bootstrap, database-clock baseline, governed synthetic SQL intent, independently committed exact receipt, duplicate read-back, original reconciliation, actual `RestoreDBInstanceToPointInTime` of control only, negative restored-control comparison and fresh original comparison |
| Cleanup | `native_watchdog.py`: persist exact inventory/owner-secret references before mutations, stop tasks, remove protection and delete synthetic databases/automated backups, remove endpoints/network/roles/logs/images, schedule seven-day runtime secret/key deletion; independently invoke the reader |
| Residual proof | `scripts/native_post_teardown.py`: fresh read-only runtime and guard inventory, versioned bucket objects/delete markers/multipart uploads, failure queue/alarm, guard functions/schedule/roles/keys/logs. Denied reads propagate, never become absence. |
| Guard retirement | `scripts/native_finalize_guard.py`: explicit approval digest, independently empty active runtime, exact bucket/key ownership, private archive/read-back of every object version before deletion, controlled guard retirement; a later independent check remains mandatory |

The deterministic fixture explicitly records `synthetic_SQL_intent_not_worker_dispatch`.
Its receipt writer has only remote credentials; the verifier has two read-only SQL identities
and tests effective UPDATE/DELETE denial. A stable UUID and exact body/hash/receipt comparison
make a replay distinguishable from conflicting content. No native effect is inferred from a
local test or a successful restore request alone. A nonempty prior run is refused rather than
moving the baseline past its existing intent.

## Approval envelope

Guard deployment, arming, the scoped ECR grant-retirement correction and image bootstrap were separately authorized and executed. Database/task runtime provisioning has not been authorized. The [updated four-hour estimate](evidence/aws-readiness/implementation/credit-estimate.json)
includes **two** customer-managed keys, three micro instances at peak, four interface endpoint
AZ units, eight aggregate quarter-vCPU/half-GB task-hours, eight secrets, at most 60 small
CodeBuild minutes if a cloud execution bridge is used, and supporting/residual allowances.
The USD 5 credit planning allowance is **not a hard cap**. The account must remain FREE/ACTIVE;
no Paid upgrade, commitments or out-of-pocket charges are authorized. Begin runtime cleanup
no later than hour three from the first chargeable guard resource, leaving one hour for cleanup.
A scheduling or permission failure is a blocker, not authority to weaken these controls.

[The checked resource envelope](evidence/aws-readiness/implementation/resource-envelope.json)
is extracted from **mocked Terraform**, not an authenticated live plan or deployed inventory.
A final saved plan must be generated in the approved account and reviewed against this envelope.
IDs, state, raw logs and manifests stay private. The existing production roots, release workflow,
original evidence and earlier regional denials are preserved.

## Execution sequence after explicit approval

The guard steps have executed in the isolated account; subsequent runtime steps remain unexecuted. Use a private
ignored directory and a fresh run ID. `account_id` and timestamps below are private configuration,
not public environment variables in GitHub logs.

1. Establish a short-lived authenticated execution host. The connected AWS Core session does
   not automatically authenticate local Terraform or boto3. This workstation now has an authenticated short-lived `aws login` session through a private refreshable credential-process profile. Supported choices are a temporary operator SDK session or a separately
   scoped, time-bounded cloud build host authorized through the connected AWS API. Do not copy
   plugin credentials, create long-lived access keys, upgrade the plan or reuse production roles.
   A cloud bridge must use the same reviewed commit, tooling checksums, cost/time bounds and
   private state/evidence outputs. Creating it also requires owner approval.
2. Build the deterministic small guard bundle:
   `python scripts/package_native_guard.py .local/native-guard.zip`.
   Prepare private guard variables with pinned account/run and `cleanup_at: null`,
   and the bundle path. A nonbillable local Terraform clock stores the first deployment timestamp at apply and preserves it across retries. The saved plan starts cleanup three hours later and ends scheduled passes four hours later; approval time does not consume that window. Fixed timestamps remain supported for explicit test configurations. Initialize `infra/acceptance-guard` with its **local private backend**.
   Generate the saved guard plan, inspect it, record its SHA-256 and use
   `python scripts/native_deploy.py --phase guard --plan <absolute-plan> --approved-sha256 <digest> --manifest <private-identity-json> --execute`.
   The guard's initial state must be archived privately; it is independent of runtime state.
3. Export `terraform output -json guard_manifest` privately. Independently run `armed_gate`
   through the SDK before any runtime creation. The gate must observe both active functions and
   the exact enabled schedule; no user assertion alone is accepted by the deployment wrapper.
   Before invoking the checker, run `python scripts/native_initialize_registry.py --manifest <guard-json> --approved-sha256 <manifest-digest> --execute`. This uses the operator session, requires independently empty runtime discovery and creates only a missing encrypted registry with `IfNoneMatch=*`; existing history is preserved. It does not grant either function `ListBucket`. Before creating the KMS-encrypted ECR repository, review/apply the separate watchdog policy correction adding only `kms:RetireGrant` for this run's tagged runtime keys. AWS requires that permission to retire repository grants on deletion; the original deployed policy lacked it. The separately approved correction is now applied and read back from IAM. Inspect effective cleanup permissions in the actual account; local IAM analysis does not
   prove SCP permission or Free-plan service eligibility. Stop on an unavailable service.
4. Initialize the runtime S3 backend using the new guard bucket, Ohio and a unique `state/runtime`
   key with S3 lockfiles and the guard CMK. Do not use or alter the Canadian production backend.
   First review/create only the ECR repository and its CMK using a targeted saved plan through
   the same runtime gate. The existing container CI job exports a source-labelled Linux/amd64 OCI archive as a one-day artifact, without AWS credentials or a registry push. Inspect its manifest/config/layer hashes and source label before uploading to the approved repository with the local short-lived session, then confirm ECR returns the identical manifest digest. This avoids a chargeable CodeBuild host. No upload is authorized by the artifact export. No image build is
   claimed locally on this Docker-less host.
5. Generate the full runtime saved plan. Pin its source commit, actual image digest, account/run,
   guard code hash, schedule and deadline; compare creates against the resource envelope.
   Apply with `native_deploy.py --phase runtime` using its approved plan digest. Export
   `terraform output -json manifest` privately. Never start ordinary application services.
6. Execute `python -m controlplane.native_run --manifest <private-runtime-json> --approved-sha256 <manifest-digest> --output <fresh-private-result> --execute`.
   Record actual tasks/digests/exits, database resource identities, restorable window, restore time,
   independent query results and effective SQL permission denials. An actual negative result must
   contain zero restored intents, one independently retained effect, `remote_ahead_missing_intent`
   and `safe_to_resume=false`. The original source must still reconcile. Failure leaves the guard armed.
7. At the deadline the guard runs minute-by-minute for one hour; each pass is bounded to 120 seconds,
   with concurrency one, two delivery retries, two asynchronous retries and a failure queue. It is
   independent of laptop uptime and private runtime endpoints. Dependency races retry on later passes.
   Reads/denials fail closed; after the bounded schedule ends, unresolved cleanup needs operator action.
   This is not a guarantee that AWS service failures cannot delay deletion.
8. Download the private registry and evidence. Run `native_post_teardown.py` with fresh private/public
   output paths and the guard manifest/registry. Inspect failures and residuals independently. RDS
   manages deletion of its owner secrets; the guard never reads or force-deletes them. Runtime
   secrets/keys can remain in seven-day pending deletion and are reported explicitly.
9. Only after active runtime absence and reviewed archived evidence, retire the guard using
   `native_finalize_guard.py --manifest <guard-json> --registry <downloaded-registry> --approved-sha256 <manifest-digest> --private-archive <fresh-directory> --execute`.
   This pauses invocations, archives and verifies object versions, deletes guard resources and schedules
   its key deletion. If late writes or API errors occur, retain private state and resolve partial
   finalization explicitly; do not call that a pass. Re-run the independent post-teardown reader.
   The guard bucket also holds runtime state: archive it before retirement. Terraform `prevent_destroy`
   stays on native databases; the approved namespace-scoped watchdog performs synthetic disposal
   through AWS APIs. Production deletion protections are unchanged.
10. Repeat delayed resource/Free-plan credit/usage checks, including after seven-day deletion.
    Zero active resources, pending deletion, metadata-only residuals and financial clearance are
    separate statements. Never assert zero charges from an empty ECS cluster or immediate usage API.

## Evidence boundaries and verification

[Current local verification](evidence/aws-readiness/deployment-clock/local/verification.json) and [infrastructure checks](evidence/aws-readiness/deployment-clock/infrastructure/verification.json) covers actual PostgreSQL/HTTP
regressions, new local SQL fixture tests, mocked controller/guard fault tests, pinned Terraform
validation/mock tests and static security checks. The separate native guard record demonstrates actual arming and restricted checker execution. Native RDS/PITR, scheduled cleanup and effective runtime-deletion permissions remain unexecuted. Public evidence must contain only sanitized results and hashed
resource references, never credentials, raw plans or private account/network identifiers.

AWS documents that [RDS deletes its managed owner secret with the instance](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html)
and that [PITR creates a new instance](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.html).
Those service contracts guide the implementation; only future executed results can establish
that this account completed the intended recovery experiment.
