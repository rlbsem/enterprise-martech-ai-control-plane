# Isolated AWS cost and teardown review

**Proposal only; not approved or executed.** USD estimates use the official public
regional price catalogs retrieved 2026-10-18 UTC, with selected rates, publication dates
and complete-catalog hashes in [cost-estimate.json](evidence/aws-readiness/cost-estimate.json).
No Free Tier or credits are assumed. Taxes and currency conversion are excluded.

The lowest-risk small acceptance preserves the existing two-AZ private design and
independent recovery domains. Use `stage`, seven persistent quarter-vCPU/0.5-GB Fargate
tasks (API/worker/downstream two each, observer one), plus sequential bounded utilities.
Retain Multi-AZ to test genuine failover/recovery; removing it would weaken that proof.
No new service, public endpoint, NAT, VPN or larger database is proposed.

| Resources | Estimated steady USD/hour |
|---|---:|
| Two PostgreSQL Multi-AZ `db.t4g.medium` instances | 0.2820 |
| 60 GB Multi-AZ gp3 at 0.254/GB-month, modeled at 730 hours | 0.0209 |
| Four interface endpoints in two AZs, eight AZ-hours/hour | 0.0880 |
| Seven Fargate tasks, 0.04456/vCPU-hour + 0.004865/GB-hour | 0.0950 |
| One private ALB + allowance of one LCU/hour | 0.0336 |
| **Compute/network/provisioned-storage subtotal** | **0.5194** |

A four-hour window plus **one** additional 30-GB Multi-AZ restored instance for the
entire window models approximately **USD 2.68** for those items. This is not the bill.
Migration/verifier tasks, image pull data, inter-AZ transfer, RDS burst credits, KMS and
Secrets Manager requests, logging, custom metrics, Container Insights, ten alarms,
one dashboard, image storage and retained backups are additional.

Request a **USD 15 planning allowance for four hours**, including contingency and
cleanup. It is **not a hard billing cap**. Start the window at the first billable creation;
stop new experiments with at least one hour remaining for cleanup. Do not extend the
window, add a second restore or create connectivity without renewed approval. RDS
backup readiness or restoration can exceed the window; record incomplete acceptance.

Other inventory: one VPC, two subnets, route tables, S3 gateway endpoint, security groups,
six task roles plus execution/release roles and policies, one ECR repository, one KMS key,
five runtime secrets plus two RDS owner secrets (a restore can add one), six log groups,
ten alarms, one dashboard, one private hosted zone with records and one backend state
bucket/lock configuration if an approved existing backend is unavailable. Preserve an
already-owned certificate and notification topic; domain purchase, VPN and changes to
existing corporate networking are outside this proposal. Review the actual plan inventory.

Secrets are 0.40/secret-month and symmetric KMS key versions 1.00/key-month before
proration/requests. Conservatively allow a full 0.50 hosted-zone month rather than relying
on the [12-hour deletion grace](https://aws.amazon.com/route53/pricing/). Logs/metrics
vary with volume and region; the contingency is a budget allowance, not a measurement.
Consult [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/) and
[ECR pricing](https://aws.amazon.com/ecr/pricing/) before the approved window.
Existing account spend, routes, PKI and billing visibility are currently unknown.

## Teardown must be approved with creation

This infrastructure intentionally resists deletion: RDS has both `prevent_destroy` and
deletion protection, ECR refuses forced deletion, secrets have 30-day recovery and the
KMS key has a 30-day deletion window. A plain `terraform destroy` is not a cleanup plan.

For an isolated synthetic acceptance environment only, obtain approval to remove those
specific resources and approve retention or deletion of synthetic snapshots/backups.
Keep a private inventory from the reviewed plan, including out-of-state PITR instances.

1. Stop admissions, quiesce services and confirm zero tasks including utilities. Preserve
   sanitized evidence, the failed records and required private recovery material.
2. Inspect a dedicated teardown diff removing `prevent_destroy` **only for the approved
   isolated DBs** and disabling their AWS deletion protection. Re-review its saved plan.
   Delete the out-of-state restored instance separately under the same inventory.
   Do not remove protections from shared or production databases.
3. Honor the approved final-snapshot/backup retention choice. Snapshot retention keeps
   storage charges and a KMS dependency; price and report it. No approval to discard data
   is implied by this document. Do not schedule key deletion while retained state needs it.
4. Remove isolated ECS services, ALB/target groups, eight endpoint ENIs/four interface
   endpoints, gateway endpoint, DNS records/zone, alarms/dashboard, log groups, images
   and emptied ECR, runtime/owner secrets, IAM and VPC dependencies through reviewed
   plans/SDK operations. Do not destroy reused external certificate, topic, routes or backend.
5. Reconcile Terraform state with independent ECS/RDS/EC2/ELB/Secrets/KMS/ECR/Logs/DNS
   inventory, including snapshots, retained automated backups, deleted-secret recovery,
   scheduled keys, backend objects and orphaned ENIs. Zero ECS tasks is insufficient.
   Record scheduled deletion dates and every retained billable resource.
6. Inspect billing/usage after its reporting delay. Confirm subsequent usage stops or
   explicitly report approved residual storage/retention charges. Delayed billing and
   failed cleanup prevent a guarantee that the planning allowance will never be exceeded.

**Current cost status: no AWS resource operations executed by this work.** An authorized
temporary SDK profile, existing private connectivity/certificate and explicit approval of
the window, allowance and teardown disposition are required before native execution.
