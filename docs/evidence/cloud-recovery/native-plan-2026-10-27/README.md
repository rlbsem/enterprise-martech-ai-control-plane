# Reduced native application recovery

**Status: implemented and locally tested; not provisioned or natively executed.** An expired local CLI session prevents an authenticated saved deployment plan. AWS Core independently reads the account; its connection does not authenticate local Terraform or Python. Short-lived CLI renewal is pending browser authorization. No access keys are required.

The [machine-readable resource estimate](cost-plan.json) totals **USD 11.2868** against a USD 20 planned-consumption ceiling. It assumes at most two simultaneous encrypted Single-AZ PostgreSQL `db.t4g.micro` instances, 20 GB gp3 each, four hours including cleanup allowance, four private endpoints, bounded one-off Fargate tasks, and retained-backup removal within 48 hours. Whole-month key/secret allowances and contingency deliberately overstate short-lived usage. No free allocation is subtracted. This is not a hard spending cap or actual consumption.

Read-only Ohio observations establish FREE/ACTIVE status, sufficient credit headroom, no existing RDS instances/retained backups/manual snapshots, an orderable PostgreSQL 17.11 micro shape, and a Fargate quota sufficient for the half-vCPU task. Account-level RDS quota is not treated as permission to run a third Free-project instance. Direct project-region metadata was denied by policy; successful Ohio service APIs and prior actual Ohio execution corroborate the selected region. No account-plan change is attempted. [Sanitized observations](read-only-observations.json).

## Resource envelope

| Resource | Maximum planned footprint and cleanup |
|---|---|
| RDS | Two concurrent private Single-AZ micro instances; original control is deleted only after exact retained-backup checks; sequential restores reuse one target name |
| Storage and backups | 40 GB concurrent gp3; conservative 100 GB backup allowance for 48 hours; remove retained automated backups and any snapshots before key retirement |
| Networking | One new isolated VPC, two private subnets, no internet route/NAT/public IPv4, four interface endpoints and one restricted S3 gateway |
| Application | Five pinned one-off task definitions; half-vCPU/1 GB; no long-running/public service or ALB; read-only root with one non-root scratch volume |
| Credentials | Six runtime secret resources and two initial managed owner secrets; record any returned restore-generation secret metadata; only bootstrap reads owners; verifier remains read-only |
| Encryption | Two run-owned customer keys; retain until stores/backups/archive are handled, then schedule deletion; never alter pre-existing AWS-managed keys |
| Evidence/cleanup | Private versioned encrypted S3 evidence/state, scoped watchdog and independent checker, deadline schedule, failure queue/alarm and bounded logs |

The [application protocol](../../../cloud-recovery.md#versioned-native-application-lane) requires an independently verified blocked early restore and a later matching restore with controlled resumption. Existing production infrastructure and historical SQL-only results are unchanged. Two successful RDS restores alone do not qualify the application. PostgreSQL restore calls omit the Oracle-only managed-password flags; application credentials are recovered from the database and their existing scoped runtime secrets, without requiring a restored owner secret or password reset. The ten-secret cost allowance remains conservative.

## Execution and teardown gates

1. Renew short-lived CLI authorization. Independently pin STS to the connected account; recheck FREE/ACTIVE, USD credit headroom of at least 20, eligibility, empty exact regional RDS inventory and quotas.
2. Require green source-bound CI and verify every OCI image blob before starting the guard clock. Generate and inspect authenticated create-only saved plans and scoped role permissions. Retain their exact digests privately; mock plans are not substitutes.
3. Deploy the exact guard plan with its deployment-anchored three-hour deadline. Independently verify its code, schedule, retry end, failure queue, separate checker identity and empty initial registry before runtime provisioning.
4. Apply only the reviewed finite runtime plan. Verify image upload/readback, actual task revisions and private encrypted stores. Recheck credits and deadline before task launches and destructive control-generation transitions. No paid account upgrade, optional infrastructure, CodeBuild or billing arrangement change is permitted.
5. Preserve immutable private read-back checkpoints and request receipts before each control deletion. Keep the independent receipt database unchanged through both restores. Stop new work if headroom, identity, quota, credit or backup checks fail; never extend the deadline.
6. Invoke exact-run cleanup, archive every evidence version and independently verify resource absence before retiring the guard. Check RDS, retained backups, snapshots, all secret generations, customer keys, networking/ENIs/endpoints, ECS definitions/tasks/cluster, ECR, roles, logs and guard resources. Pre-existing default networking and AWS-managed keys remain untouched.
7. Report pending secret/key deletion separately. Read only non-chargeable credit/usage information; delayed or unavailable reporting is not zero cost or billing clearance. No recurring account monitoring is enabled by this implementation.

Static IAM analysis was executed with IAM Policy Autopilot 0.3.0 over the Python SDK code and Terraform. Its baseline includes ambiguous cross-service mappings and is **not applied** or treated as effective-permission proof. The reviewed Terraform task policies retain explicit service/resource scope; runtime native permission checks remain required.

Sources: [RDS Free Tier](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Welcome.html), [retained automated backups](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.Retaining.html), [RDS PITR](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.html), [private ECR access](https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html), and Ohio AWS Pricing API product identifiers recorded in the estimate. These establish service contracts and price assumptions, not execution of this new experiment.
