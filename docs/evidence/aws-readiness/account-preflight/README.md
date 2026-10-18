# Native account preflight: connected, resource inventory blocked

AWS Core executed real **read-only AWS API calls** on 2026-10-18. This establishes
account connectivity, not native application, database or recovery acceptance.
The billing-plan response was checked privately against the owner's Free-plan boundary.
Credit balance, expiration, account IDs, role/policy ARNs and authorization IDs are excluded.

[Sanitized API telemetry](verification.json) records 14 attempted reads: four succeeded
and ten returned explicit organization service-control-policy denials. No mutating call
was attempted. An initial script-validation failure executed no calls; the inventory
batch's result formatting subsequently failed after its API calls had run. The server's
independent `api_calls` telemetry retains their outcomes. Neither collector failure is
presented as successful inventory collection.

| Read | Observed result |
|---|---|
| STS identity | Succeeded; connected principal/account checked privately |
| Free Tier account-plan state | Succeeded; plan/status/credit information checked privately |
| Free Tier usage | Succeeded with no usage entries; **does not prove no resources exist** |
| Route 53 hosted-zone enumeration | Succeeded with zero hosted zones |
| RDS instances and snapshots | Explicit SCP denial; inventory unknown |
| ECS clusters, VPCs and region enumeration | Explicit SCP denial; inventory unknown |
| ACM, IAM OIDC, Secrets Manager, KMS, load balancers | Explicit SCP denial; inventory unknown |

The target was `ca-central-1`, plus global-service prerequisite reads. Region discovery
was denied, so this is not an all-region inventory. A denied read is not evidence of
absence, and a denied read does not establish which create/restore operations the Free
plan itself permits. No write was used as a permission probe. Reauthentication and a
larger IAM Allow policy would not, by themselves, override an explicit SCP Deny.

## Free-plan acceptance decision

The [RDS service guide](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Welcome.html)
currently lists PostgreSQL among eligible engines, micro instance classes, and Single-AZ
deployment. It excludes other deployment options. Consequently the repository's current
**Multi-AZ `db.t4g.medium` design is not the documented Free-plan acceptance shape**.
Its earlier full-design estimate is not permission to apply it to a Free-plan account.

| Candidate proof | Decision under the present boundary |
|---|---|
| Account identity and plan-state reads | Executed; successful |
| Native PostgreSQL on a permitted Single-AZ micro instance | Documented eligibility candidate, but current connection cannot even enumerate RDS; not executable or authorized yet |
| Real control-only PITR and independent downstream reconciliation | RDS documents PITR from automated backups; eligibility for the exact Free-plan account/restore shape is **not established** by these reads |
| Multi-AZ failover or unchanged full Terraform deployment | Excluded from the proposed Free-plan lane; existing production defaults remain unchanged |
| ECS/Fargate processing, interruption and release/rollback | Service-plan eligibility not conclusively verified; ECS/network discovery denied; no deployment permission inferred |
| Alarm delivery, secrets and network/role boundaries | Unverified; no resources or notifications created |

[RDS backup/restore documentation](https://docs.aws.amazon.com/AmazonRDS/latest/gettingstartedguide/managing-backup-restore.html)
explains that PITR creates a separate instance while retaining the source. It does not
promise that this connection can restore one or that account limits allow the source,
independent downstream and restored instance concurrently. Those checks remain necessary.

The safe next candidate remains the small database/PITR lane described in the
[minimal assessment](../../../aws-minimal-acceptance.md): private independent stores,
bounded synthetic data and a separate verification identity. ACM, custom DNS, ALB and a
workstation VPN are not intrinsic requirements for that narrower proof. No such smaller
deployment root or Single-AZ acceptance policy has been substituted for the existing
tested production design.

## Next milestone and boundaries

Keep the account on its existing Free plan. Do not activate paid-only features, join an
organization, purchase commitments or alter account/security controls. AWS's
[plan documentation](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)
describes automatic-upgrade triggers; none were performed here. Available credits are
neither a provisioning authorization nor proof of service availability.

Before any future resource creation: establish a legitimately permitted read-only
connection to the intended account, resolve the documented SCP restriction with its
policy owner/provider without bypassing it, verify exact Free-plan instance/restore and
supporting-service eligibility, and present a bounded credit-only plan for owner approval.
Do not recommend a Paid-plan upgrade as a workaround. A working plugin session does not
export credentials into the local Python/Terraform process.

Native AWS deployment and PITR remain the next acceptance milestone. Current status:
**account preflight partially executed; application/RDS/PITR acceptance not executed**.
The existing 153-test local proof is preserved and remains a separate evidence lane.
