# Ohio Free-plan database/PITR acceptance proposal

**Historical proposal; execution results are recorded separately.** The first run passed
native SQL verification but its third-instance PITR request hit the Free-plan quota.
The subsequent two-instance retained-backup run reached an available restored RDS instance;
its final native restored-state reconciliation was not recorded. See the
[first execution](evidence/aws-readiness/native-sql/README.md) and
[retained-backup outcome and teardown](evidence/aws-readiness/retained-two-instance/native-outcome/README.md).
No Paid-plan upgrade or out-of-pocket expense is authorized. The existing Canadian-region production Terraform, release manifest guards,
Multi-AZ witness and all historical evidence remain unchanged.

## Region correction and verified inventory

AWS's [project-region table](https://docs.aws.amazon.com/accounts/latest/reference/project-regions.html)
assigns Canadian projects to `us-east-2`. Independent Ohio reads succeeded for RDS,
ECS, EC2 and supporting services. This corroborates the region assignment; no direct
project-settings metadata read is claimed. The prior `ca-central-1` SCP denials are
real historical observations, **not evidence that this account cannot use those services
in Ohio**. They must not be erased or reinterpreted as empty inventories.

[The sanitized Ohio record](evidence/aws-readiness/ohio/inventory.json) contains 29
successful read calls and zero mutations. RDS instances/snapshots/subnet groups, ECS
clusters, EC2 instances, managed SSM nodes, private endpoints, ECR repositories, secrets,
log groups, Lambda functions and Scheduler schedules were empty in the queried region.
One default VPC and three default subnets exist. Those subnets assign public IPs and
their main route table has an internet gateway; they are **not** ready-made private
acceptance subnets. Preserve them and do not modify project-managed roles or policies.

The account's Free-plan state and remaining credit balance were rechecked privately.
The regional catalog offers PostgreSQL `17.11`, `db.t4g.micro`, encrypted gp3 and a
20-GB minimum for that storage type. An exact engine lookup reports `available`.
Quota reads support planning a three-instance peak, but neither quotas nor orderable
options establish effective permission to create an instance or restore it.

## Minimal candidate, separate from production

| Component | Proposed bound |
|---|---|
| Region and namespace | `us-east-2`; unique acceptance-run namespace and ownership tags |
| Control and independent receipts | Two separate encrypted Single-AZ PostgreSQL 17.11 micro instances, 20 GB gp3 each |
| PITR | At most one additional identical control restore; original control and receipt store retained during reconciliation |
| Backup and SQL safety | 14-day automated backup configuration, TLS `verify-full`, forced TLS parameter group, fixed storage cap, no public DB endpoint |
| Network | Separate VPC and two private subnets across AZs; local routes only; no NAT, public tasks, ALB, custom DNS, ACM, VPN or paid Private CA |
| Task access | Four interface endpoints in one task AZ: ECR API/Docker, Logs and Secrets Manager; S3 gateway for image layers |
| Compute | One-off bootstrap, isolated synthetic fixture writers and read-only verifier; no persistent ECS services, at most two concurrent tasks and eight aggregate task-hours |
| Credentials | Runtime retrieval only; scoped separate identities for control writes, independent receipt writes and verification; no owner credentials in fixture writers |
| Deadline | Four hours from first billable creation; stop new experiments and begin cleanup at hour three |
| Cleanup/image builds | At most 60 small CodeBuild minutes, separate cloud-side scheduling and independent read-only post-check |

RDS's [current Free Tier guide](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Welcome.html)
describes Single-AZ micro options. Use those as a conservative eligibility candidate;
do not transfer the old 750-hour offer to this credit-based project. The regional
catalog's `MultiAZCapable` flag is a service capability, **not** proof that Multi-AZ is
permitted by this project's plan. Existing production Multi-AZ defaults are preserved.

The narrower lane needs its own reviewed configuration and manifest. Do **not** change
`infra/aws` to Ohio, relax `Release` region validation, or run the production
`aws_acceptance` collector against a smaller topology and call its missing checks passed.
That collector intentionally expects the production service topology and Multi-AZ.
The [isolated implementation and execution procedure](aws-ohio-execution.md) provides separate Terraform roots, a native fixture/PITR executor and an independent scheduled guard. These components were deployed for the bounded experiments; their recorded results do not establish full application acceptance.

## Native experiment and independent facts

1. After explicit owner approval, recheck `FREE`/`ACTIVE`, credit headroom and the exact
   home region. Refuse Paid/expired/unavailable state. Never call account-upgrade,
   advanced-feature activation, organization-join or commitment-purchase APIs. If any
   required service demands an upgrade, stop; do not silently substitute a paid path.
2. Review the actual scoped resource plan and arm the independent cleanup mechanism
   before starting database provisioning. Track every ID immediately, including partial
   creations and managed-owner secrets. Keep all raw plans, IDs and logs private.
3. Bootstrap the existing schema and restricted SQL roles on separate control/receipt
   databases. Use the repository's source revision and immutable container digest.
   Confirm SQL role denial boundaries and read-only verifier privileges natively.
4. Establish an actual restorable time **before** a known fixture intent/effect. Commit
   a deterministic synthetic intent and its matching receipt using separate identities.
   Preserve the stable key, immutable content hash and independently committed receipt.
   Compare actual source ledgers through `operations.recovery_snapshot`; require a pass.
5. Quiesce writers and prove no write-capable tasks remain. Restore **only control** to
   that earlier time; never restore the independent receipt database. Wait for actual
   RDS availability and independently check private/encrypted/Single-AZ/TLS settings,
   backups and roles. Do not manufacture a restore gap by deleting a local row.
6. Read the restored control and unchanged receipt store with the verifier identity.
   Require the remote-ahead finding and `safe_to_resume=false`. No resume operation is
   permitted from an old report. Preserve the failed gate as successful evidence of
   fail-closed behavior, not as a failed experiment to hide.
7. Rebind to the preserved original control or a separately approved later recovery
   point, rerun a fresh independent comparison and only then consider reopening.
   A second restore is outside the one-restore bound and needs another approval.

This proves native managed PostgreSQL/PITR and ledger reconciliation **only if it
actually executes**. Direct SQL fixture writes are not HTTP delivery, real customer
outcomes, ECS worker recovery or application release/rollback proof. Multi-AZ failover,
HTTPS ingress, consent races through the live API and full ECS application acceptance
remain a later, separate milestone. Preserve the existing local proof for those controls.

Backup readiness and PITR can exceed a four-hour window. If no valid recovery point
becomes available with cleanup time remaining, record incomplete native acceptance and
tear down. Do not extend the run automatically or claim success from a snapshot listing.

## Reproducible credit estimate

[Public Ohio catalog rates](evidence/aws-readiness/ohio/public-rates.json) retain source
URLs, retrieval/publication dates, SKUs and catalog hashes. Reproduce the estimate:

```bash
python scripts/estimate_ohio_acceptance.py
```

[The updated result](evidence/aws-readiness/implementation/credit-estimate.json) models **USD 1.948274**
in credit consumption, including fixed contingency allowances for unmetered supporting
items. Propose a **USD 5 credit planning allowance** for four hours. It is not a hard cap
or measured consumption. No legacy free-usage deductions are assumed.

| Main rate/quantity | USD credit equivalent |
|---|---:|
| Three micro RDS instances for all four hours, at 0.016/hour | 0.192000 |
| 60 GB gp3, prorated at 0.115/GB-month | 0.037808 |
| 60 GB additional-backup allowance at 0.095/GB-month | 0.031233 |
| Four interface endpoint AZ units for four hours at 0.01/hour | 0.160000 |
| Eight aggregate quarter-vCPU/half-GB task-hours | 0.098740 |
| Eight secrets and two KMS keys, prorated | 0.028493 |
| 60 small CodeBuild minutes at 0.005/minute | 0.300000 |
| Logs, requests, transfer, scheduler/checker and residual allowances | 1.100000 |

Regional reads establish discovery availability, **not create/restore/RunTask eligibility**.
RDS has no harmless create/PITR dry-run that proves all write conditions. No write probe
was attempted. Final permissions/service availability must be checked against the exact
plan; any denied creation must fail closed without requesting advanced/Paid activation.
Credit checks must continue during the run. Do not represent a remaining-credit API
response as a universal cost cap or an authorization to provision.

## Implemented cleanup controls and remaining native execution

The [current implementation](aws-ohio-execution.md) replaces the earlier proposed
CodeBuild cleanup job with a smaller non-VPC Lambda watchdog and independently scoped
read-only Lambda checker. The one-minute schedule starts at hour three and ends one
hour later, with bounded invocation/retry controls, a private failure queue and an
observable queue alarm. Actual execution boundaries are in the [native evidence](evidence/aws-readiness/native-sql/README.md). See the execution guide for exact
inventory, private versioned evidence, synthetic disposal and independent guard retirement.

All prior discovery, cost v1 and local records remain historical evidence. The new
[implementation records](evidence/aws-readiness/implementation/README.md) bind that implementation checkpoint.
Native SQL verification and resource operations subsequently executed with explicit approval.
[PR #4](https://github.com/rlbsem/enterprise-martech-ai-control-plane/pull/4) merged on
October 19, 2026 at `c786b73a3085b120121eb2656a9fce009bd8e1d0`. The retained-backup
restore reached `available`, but the final native fail-closed comparison and delayed
billing clearance remain unverified. Pending-deletion resources are described in the
[dated closeout](evidence/aws-readiness/retained-two-instance/native-outcome/README.md);
this document supplies no new account observation or execution authorization.
