# Executed Ohio guard evidence

The approved guard plan deployed on October 18, 2026. [Actual results](guard-execution.json)
record 23 AWS creates plus a local deployment clock, independent live metadata checks,
a successful native checker invocation and a watchdog invocation returning
`ARMED_NO_MUTATION` before the deadline. These are guard checks, not database recovery proof.

The first checker invocation failed with S3 `AccessDenied` for a missing registry.
Restricted readers cannot distinguish that missing key without `ListBucket`.
The operator independently inventoried an empty runtime namespace and conditionally
created the encrypted empty registry. The subsequent checker passed under its original
restricted role. No permission was widened and the rejected invocation is recorded.
The reusable `scripts/native_initialize_registry.py` implements this initialization;
its refusal, preservation, race and exact-content checks have [local regression evidence](local/verification.json).

Cleanup is scheduled for 2026-10-19 00:00:38 UTC, with scheduled passes ending at
01:00:38 UTC. Reading its live configuration and calling the watchdog before its deadline
does not prove scheduled delivery, deletion permissions or a completed cleanup.

RDS, SQL, PITR and remote-ahead recovery have not executed. The separate targeted ECR/key
bootstrap plan has not been applied. No ECS task or CodeBuild project was started.
Raw plans, state, account identifiers, credentials and full errors remain private.
Earlier local evidence and denied regional calls are unchanged.

The encrypted image-repository bootstrap revealed that the originally deployed watchdog
policy lacks `kms:RetireGrant`. A scoped Terraform correction and mock assertion are
prepared; they are not applied. This blocks ECR provisioning until separately approved.
AWS documents the requirement in [ECR encryption permissions](https://docs.aws.amazon.com/AmazonECR/latest/userguide/encryption-at-rest.html).
The container CI export uses no AWS credentials and provisions no AWS resources.
