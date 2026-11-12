# AWS production-style deployment acceptance

**Status: NOT EXECUTED.** This is the gate for a future authorized deployment, not evidence that the full ECS/HTTPS/Multi-AZ topology has been deployed. The completed October 28, 2026 native RDS/Fargate recovery test remains a separate, successfully executed, bounded acceptance: [recorded native application recovery](evidence/cloud-recovery/native-application-2026-10-28/README.md).

## Objective and exact boundary

Demonstrate that an **approved, privately routed** Control Plane can be deployed to persistent ECS/Fargate services, reached over authenticated HTTPS, operated under independent PostgreSQL authority, upgraded and rolled back safely, and restored without replaying already committed business effects.

Use only synthetic, non-contacting customer records. Do not substitute a publicly accessible demo, public credentials, an ALB screenshot, a green Terraform plan, or a successful database restore for the end-to-end acceptance.

The existing infrastructure root is explicitly restricted to **ca-central-1**. The previously executed recovery experiment was in **us-east-2** and used a different, reduced topology. Do not change the region validation merely to bypass an account restriction.

## Read-only discovery: November 12, 2026

A connected AWS session was used for **read-only** inventory. No AWS mutation, deployment, billing change or cost approval occurred.

- **us-east-2:** ECS returned zero clusters; RDS returned zero DB instances; ECR returned zero repositories; ACM returned zero certificates; EC2 returned one default VPC. These are observations in the **connected session**, not a claim about every account or the historical test.
- **ca-central-1:** ECS, RDS, ECR, ACM and VPC read requests returned an **explicit organization service-control-policy deny**. There is no reliable infrastructure inventory for the Terraform target region.
- The connected principal and project/account ownership have **not** been established as the intended deployment identity. Do not infer that an authenticated role grants permission to deploy, that the earlier experiment's account is currently connected, or that a region's lack of observed resources authorizes provisioning.

**Disposition: BLOCKED BEFORE CLOUD WRITE.** Establish the approved target account, authorization, allowed region, infrastructure ownership and budget before requesting even a Terraform apply.

## Release prerequisites and hard stops

| Gate | Required independent evidence | Stop if |
|---|---|---|
| Account and region | Approved account/region, matching assumed-role identity, permitted API reads, environment ownership | Target identity is ambiguous; SCP denies inventory or deployment |
| Spending | Written finite test budget, expected charge-bearing resources, designated owner, alerts, cleanup/retention liability | No explicit approval or claimed hard USD cap based only on a budget alert |
| Dependencies | Existing or explicitly provisioned private DNS, trusted ACM certificate for both names, VPN/peering/TGW route, on-call SNS subscriptions, RDS 17 patch/class availability and quotas | Certificate, route, DNS, subscription or quota not verified |
| Source binding | Exact clean source SHA, pinned dependency tests, Linux/AMD64 OCI image, ECR digest readback, matching task revision and SOURCE_COMMIT | Mutable tag, missing digest, conflicting task definition |
| Infrastructure | Reviewed Terraform plan in a dedicated environment/state, no broad CIDRs, encrypted protected RDS, private networking/endpoints, scoped secrets and IAM | Unreviewed state/apply, public database, elevated runtime identity |
| Release permissions | GitHub protected environment, independent review, exact OIDC subject, least-privilege release role, no infrastructure power for application releaser | Unprotected or wildcard trust |
| Operational safety | Baseline source-to-receipt reconciliation, controlled quiescence, zero running services/tasks before migration/recovery, reviewed restore window | Unknown outcome, remote-ahead discrepancy or active writer |

Infrastructure and application roles are intentionally separate. The release role cannot provision IAM, networks, databases or Terraform state. The recovery operator has different authority again.

## Proposed acceptance sequence

Record original revision, AWS request IDs, task definitions, image digest, sanitized timestamps, actual exit codes, observed resources and independent findings for every step. Do **not** declare a pass on the basis of scripted expected outputs.

1. **Preflight only:** Run existing offline validation (`python scripts/validate_infra.py`) and CI; inspect the actual AWS account, region, quotas, costs, networking, DNS/PKI, SNS subscriber state and IAM policies. Capture a reviewed Terraform plan. No write until each prerequisite above is satisfied.
2. **Deploy closed:** Provision in the approved account using the existing `infra/foundation` and `infra/aws` roots. Export `terraform output -json release_manifest`. Verify encryption, network isolation, restricted credentials, pinned image/task definitions, endpoint reachability and **all ECS service desired counts at zero**.
3. **Start under a fresh gate:** Run the approved migration and read-only verifier, then release services in the established order: downstream, API, workers, observer. Check running **and desired** counts, the requested task revisions, health, read-only observer access and CloudWatch signals. An ECS circuit-breaker rollback to an older revision is not a pass.
4. **HTTPS and authorization:** From an explicitly routed authorized test client, verify certificate hostname/chain and private DNS; inspect exact API health and authenticated synthetic proposals. Test unauthorized requests, denied field ownership, consent revocation and prohibited database writes. From outside the authorized route, verify private endpoints are inaccessible. Do not send messages to real people.
5. **Uncertain write recovery:** Force an acknowledgment loss **after** a synthetic downstream effect has committed, terminate a worker, then resume. Verify identical effect ID, immutable payload hash, independent downstream receipt and exactly one recorded effect. A timeout by itself is not proof of a failed write.
6. **Release and rollback:** With a second, source-pinned approved revision, exercise maintenance drain and startup. Then roll back to the previous known-good digest. Check service task revisions, database migration compatibility, persistent audit/receipt history and no duplicate external effects. Never automatically downgrade schema or erase evidence.
7. **Blocked and permitted PITR:** Stop admissions and writers, select a verified recovery timestamp, restore the control DB **without restoring the independent receipt ledger**, and bind only the reviewed replacement. Test a remote-ahead state that must return `safe_to_resume=false` and remain closed. Separately exercise a matching state, fresh approval, current consent and a successful independent reconciliation. Record actual application recovery time; database `available` is not application readiness.
8. **Observe and retire:** Preserve bounded scrubbed logs, independent receipt reconciliation, CloudWatch alarms, actual task/restore IDs, backup/restore verification and cost telemetry. Drain and remove **only test-owned** resources under an approved retention plan. Verify residual services, VPC attachments, snapshots, ECR images, S3 versions, secrets and keys. Report pending deletion distinctly from absent resources, and billing estimates distinctly from final charges.

**Do not reuse the October 28 run as evidence for steps 2-8.** Its native recovery result stands on its own; this acceptance requires actual persistent-service and network observations.

## Required recorded verdicts

| Area | Pass condition |
|---|---|
| Persistent runtime | Approved services reached desired/running counts on the exact digest and task revision; required paths healthy |
| Network and trust | Private routing, HTTPS certificate verification, role separation and deny checks observed independently |
| Stateful delivery | One immutable synthetic business effect per stable effect ID across lost acknowledgment and worker restart |
| Unsafe restore | Receiver-ahead facts discovered from the independent ledger; no further business dispatch |
| Safe restore | Fresh complete comparison, fresh authorization, exact receipts, and verified resumptions without duplicate effects |
| Rollback | Previous image revision restored without removing committed effects or bypassing migration compatibility |
| Teardown | Authorized owner inventories residual resources; pending deletes and delayed charges remain open where applicable |

Any missing result makes that area **UNVERIFIED**, not “passed by design.” Keep the original audit records and source pins intact.

## Cost and retention rule

RDS Multi-AZ, Fargate tasks, ALB, private endpoints, CloudWatch, backup retention, Secrets Manager, KMS and network attachments may all incur costs. The Terraform topology is not a free-tier demonstration. A proposed dollar amount is **planning**, not an enforceable AWS spending cap. Budget alerts, watchdogs and a short test window reduce exposure but do not guarantee maximum billing. Do not deploy, upgrade, enable billing or create persistent resources without a separate explicit account-specific approval covering a tested teardown plan.

## Source and operating references

- [AWS infrastructure ownership, immutable release and required inputs](../infra/README.md)
- [Release and PITR runbook](cloud-operations.md)
- [Operational failure semantics](failures.md)
- [Previously executed, bounded native AWS recovery](evidence/cloud-recovery/native-application-2026-10-28/README.md)
- [Deployment validation script](../scripts/validate_infra.py)

**Tools execute. The control layer determines what is true.** A successful AWS API call, CI job, or Terraform apply does not by itself establish business-integrity acceptance.
