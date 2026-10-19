# Executed native SQL; PITR blocked by the Free-plan instance limit

**Native SQL acceptance passed. Native PITR acceptance did not pass.** The real
`RestoreDBInstanceToPointInTime` request was rejected before a restored instance existed.
This evidence is separate from the local PostgreSQL/HTTP suite and mocked infrastructure tests.
The [current local regression record](../restore-parameters/local/verification.json) contains
244 passes, zero failures and zero skips, including denied-restore and cleanup-discovery cases.

## What actually executed

Two independent private, encrypted PostgreSQL 17.11 `db.t4g.micro` databases ran in
`us-east-2`, with 20 GB gp3 each, Single-AZ, forced TLS and one-day automated backups.
The account was observed as FREE/ACTIVE. No Paid upgrade or CodeBuild host was used.

The [executed task results](acceptance.json) establish:

1. Successful restricted-owner bootstrap and an empty 0-intent/0-effect reconciliation.
2. A real database-clock baseline before creating the synthetic SQL intent.
3. One independently committed receipt; a second delivery returned the identical receipt,
   content hash and key, with exactly one effect.
4. Matching original state: one intent, one effect, no findings. Separate read-only SQL
   identities were actually denied two mutation attempts.
5. An operator continuation independently re-read all seven original stopped task identities,
   image digests and CloudWatch result bodies, preserved the original timestamp, and performed
   another 1/1 reconciliation. It did not reset or regenerate the fixture.

The [actual image](image.json) was built by
[GitHub Actions](https://github.com/rlbsem/enterprise-martech-ai-control-plane/actions/runs/39170995855),
exported as Linux/amd64 OCI, verified locally, uploaded using short-lived authentication,
and read back exactly from ECR. The container source commit and host-controller source hash
are recorded separately. Host-only RDS request corrections did not change the executed image.

## The real recovery blocker

The original baseline was inside the authoritative automated-backup restore window.
The corrected restore request received HTTP 400 `InstanceQuotaExceeded`, request ID
`8da170d6-ed99-4c5f-9a30-2618d8a0d297`, at 22:35:41 UTC on 2026-10-18.
AWS explicitly reported that the Free-plan account had reached its instance limit with
the two original databases. A third concurrent instance could not be created.

The earlier [PostgreSQL parameter rejection](../restore-parameters/README.md),
[restore-window correction](../restore-window/README.md),
[restricted-owner bootstrap correction](../rds-bootstrap/README.md), and
[RDS provisioning corrections](../free-retention/README.md) remain visible. They are
not retrospectively represented as successful attempts.

**Not executed:** a completed RDS PITR, queries against a restored database, the native
restored-control `remote_ahead_missing_intent` refusal, or a post-restore original comparison.
Local tests cover that refusal; they are not a substitute for vendor execution. The controller
propagates quota/access denials without deleting the source, inventing a restored result,
changing the account plan, or weakening its acceptance criteria.

Finishing this exact concurrent experiment requires permission for three concurrent
eligible instances. No upgrade is authorized or recommended as an automatic next step.
A separately reviewed experiment fitting two concurrent instances would need a different
recovery sequence and verification of retained-backup eligibility before deleting a source.
That sequence was not silently substituted for the approved experiment.

## Scope and resource evidence

This is `synthetic_SQL_intent_not_worker_dispatch`. It proves native SQL identity,
receipt idempotency, original-state reconciliation and effective read-only permission denial.
It does not establish production HTTP/worker behavior, Multi-AZ failover, live client outcomes,
or a full application deployment. Those boundaries remain distinct from local execution.

[Measured resource intervals](resource-intervals.json) include failed diagnostics and
continuation tasks, not just successful stages. They are SDK observations, not invoiced usage.
The original USD 1.948274 model and USD 5 allowance are planning figures, not the actual bill
or a hard cap. Delayed usage and credit reporting cannot establish billing clearance.

The production infrastructure remains unchanged and unaccepted natively. In particular,
its existing physical database name `control` is reserved by RDS; the native lane uses
`csaacceptance`. The production name must be corrected and separately reviewed before a
future production deployment. This experiment does not certify that template as deployable.

[Teardown and independent residual observations](cleanup.md) are recorded separately;
seven-day secret/key deletion windows are not immediate disappearance or billing clearance.

[Artifact hashes](artifact-manifest.json) bind the sanitized native records, this explanation
and the current local regression evidence. They provide file integrity, not an AWS-signed
attestation or proof of the uncompleted recovery stage.
