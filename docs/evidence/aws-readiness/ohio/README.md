# Ohio read-only preflight and acceptance design

**Native read-only discovery executed. Native deployment, SQL/PITR, recovery and teardown
have not executed. No resources or account settings were changed.**

- [Sanitized inventory](inventory.json): 29 real successful AWS API reads in the selected
  Ohio region/global prerequisite context. Account identifiers, financial balance,
  network IDs, raw IAM documents and private diagnostics are excluded.
- [Architecture and teardown proposal](../../../aws-ohio-acceptance.md): separate
  Single-AZ micro database/PITR lane; production architecture stays unchanged.
- [Credit model](credit-estimate.json), [public rates](public-rates.json) and
  [artifact integrity](artifact-integrity.json): deterministic estimate, not actual spend.
- [Local regression report](local/report.md), [source bindings](local/verification.json)
  and [cases](local/tests.xml): 164 local tests passed, with zero failures or skips.
- [Infrastructure/security checks](infrastructure/verification.json): unchanged
  production Terraform validated with six mocked tests and Trivy scans.

The earlier [Canadian-region denied calls](../account-preflight/README.md) and all
previous local/infrastructure evidence are preserved verbatim. Those denials describe
requests to `ca-central-1`; the account-wide-block interpretation was too broad.
AWS's [project-region documentation](https://docs.aws.amazon.com/accounts/latest/reference/project-regions.html)
lists Canada under Ohio, and the independent regional reads corroborate that assignment.

The inventory confirms no RDS instances, ECS clusters or project workload in the queried
resources. It also confirms a default VPC with publicly routed default subnets. Empty
workload inventories do not make those subnets private or authorize their modification.

PostgreSQL 17.11 and encrypted `db.t4g.micro`/gp3 are present in the regional catalog.
Neither a catalog entry, a quota nor a successful Describe call establishes permission
to create, restore or run a task. No mutation was attempted to probe permissions.

The production release, restore and configuration-witness tools remain pinned to their
existing topology and region. A new native fixture/root, Single-AZ witness, complete
residual checker and cloud-side cleanup watchdog are still required before unattended
provisioning. A credit calculator is not deployment tooling or a hard billing cap.
