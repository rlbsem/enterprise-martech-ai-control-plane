# AWS readiness evidence

**Outcome A: local readiness verified; native AWS acceptance not executed.** No AWS
credentials or configured SDK profile were available. No AWS resources were created,
modified or deleted. SDK mocks are not deployment evidence.

| Lane | Actual execution | Evidence |
|---|---|---|
| Local correctness | 139 tests, zero failures/errors/skips; real PostgreSQL 17 and separate HTTP processes | [Report](local/report.md), [source hashes/runtime](local/verification.json), [test cases](local/tests.xml) |
| Infrastructure | Terraform 1.13.5 format/init/validate, six mock tests; Trivy 0.75.0 HIGH/CRITICAL configuration scan and secret scan passed | [Record](infrastructure/verification.json), [security](infrastructure/security.json), [secrets](infrastructure/secrets.json) |
| Enterprise regression | 1,000 entities/intents, four local worker processes, 950 independent effects, 50 permanent rejections, 50 reconciled lost acknowledgements, zero duplicate effects | [Measured workload](enterprise-workload.json) |
| AWS observation/operations | **Not executed**; no native SDK session | [Required execution](../../aws-native-acceptance.md) |
| Cost | Public pricing lookup only, no billable account operation | [Estimate](cost-estimate.json), [approval/teardown review](../../aws-cost-review.md) |

The suite preserves the original governance, idempotency, process-death and independent
receipt tests. New cases cover task-definition overrides, inherited credentials, bounded
utility cleanup, source/environment pins, read-only witness boundaries and stale verifier
reports. Terraform rejects cross-environment, public and incorrectly bound restore targets.
The negative restore check initially deferred at plan time because a data postcondition
depended on not-yet-created resources. The fixed design checks known database properties
on read and gates task registration on environment-specific bindings; six mock plan tests
now pass. Trial mock applies also exposed expected `prevent_destroy` cleanup protection;
no real cloud resources were involved. That protection is retained in the deployment.

One initial local run failed before tests could execute because Windows denied pytest's
default temporary directory. A fresh private `--basetemp` resolved it without weakening
tests. The successful full run is the published record; local troubleshooting logs remain
private because runtime logs can contain paths or credentials.

All 22 prior files under `docs/evidence` are preserved from baseline
`8557d4d7123d0a0dbe17491bc4f67641cf587bfe`; their original Git-blob hashes are in
[historical-records.json](historical-records.json). Historical records bind their original
source revision. The new records bind current source; no historical result is relabeled.

Reproduce using the Python/PostgreSQL instructions in the repository README:

```bash
python scripts/verify.py work/current-proof
python scripts/validate_infra.py --output work/current-infrastructure
python scripts/repo_check.py --verification-record work/current-proof/verification.json \
  --infrastructure-record work/current-infrastructure/verification.json
```

The default repository integrity check validates the new checked-in records. Both record
paths can be supplied explicitly when reproducing an older revision. Source hashes use
LF normalization across Windows/Linux; JUnit evidence omits runner hostnames. Diagrams
remain the six existing Mermaid sources and their hash-bound SVGs. Hosted CI results must
be checked at the exact PR head; local success does not establish hosted success.
