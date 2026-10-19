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

RDS, SQL, PITR and remote-ahead recovery have not executed. The separately approved ECR/key bootstrap and scoped policy correction subsequently executed; see [actual image-bootstrap results](image-bootstrap-execution.json). No ECS task or CodeBuild project was started.
Raw plans, state, account identifiers, credentials and full errors remain private.
Earlier local evidence and denied regional calls are unchanged.

The encrypted image-repository bootstrap revealed that the originally deployed watchdog
policy lacks `kms:RetireGrant`. A scoped Terraform correction and mock assertion are
prepared and separately approved, then applied before ECR provisioning. The policy was read back from IAM; its condition remains limited to this run's tagged runtime keys.
AWS documents the requirement in [ECR encryption permissions](https://docs.aws.amazon.com/AmazonECR/latest/userguide/encryption-at-rest.html).
The container CI export uses no AWS credentials and provisions no AWS resources.

The real CI-built Linux/amd64 OCI artifact was downloaded and its archive, config and
17 layer hashes verified, along with its source revision label. All 18 config/layer blobs
were uploaded to the immutable encrypted repository. Registry manifest read-back was
byte-identical and ECR returned the expected digest. The source is GitHub's tested PR
merge commit; its branch head is `939a995`. The independent checker observed repository/key
inventory under its separate identity; it did not independently read the manifest body.
No AWS compute task or database was started by this bootstrap.
