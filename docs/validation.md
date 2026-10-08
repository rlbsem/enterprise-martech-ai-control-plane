# Validation and engineering review

## Executed checks

The [generated verification record](evidence/verification.json) is the authority for test count, runtime, timings and source hashes. All original 68 tests remain unchanged. New tests exercise managed-secret denial, malformed role credentials, separate cloud SQL grants, rejected executor authority, bounded metrics, immutable task validation, recovery refusal and private restore requests. Tests use PostgreSQL 17 and independent HTTP processes; AWS SDK interactions are stubbed without account access.

`scripts/verify.py` checks installed/source parity, runs Ruff and the entire suite, and refuses to publish evidence if source changes during execution. The [JUnit record](evidence/tests.xml) identifies individual tests. Portable source hashes normalize LF newlines across Windows/Linux; they include Terraform, workflows, certificates and operating tools as well as the core logic. [The scenario report](evidence/report.md) is built from executed assertions.

The [larger workload](evidence/enterprise-workload.json) is a separate measured run: 1,000 accounts, four worker processes, planned dependency faults and full-ledger comparison. It does not replace the exact consent, authority, concurrency and worker-death fixtures.

| Layer | Validation method |
|---|---|
| Business invariants | Existing real PostgreSQL/HTTP tests, including 100 generated lifecycle examples |
| Worker/process failures | Eight competing workers; stale-token fences; actual process kill after remote commit; 429/500/422 and before/after-commit timeouts |
| Cloud database authority | Real SQL under API, worker, observer and verifier roles, including prohibited mutations and cross-schema reads |
| Secret/IAM error behavior | Botocore Stubber responses for AccessDenied, missing secrets and KMS decryption failure; malformed authority contracts rejected |
| Database recovery gate | Independent receipt survives local intent loss; complete comparison rejects missing intents, conflicting hashes/receipts and missing acknowledgements |
| Release safety | No service-open calls after verifier failure; busy clusters rejected; mutable or mismatched images rejected |
| Infrastructure | Both Terraform roots fmt/validate; mocked provider tests; [Trivy HIGH/CRITICAL configuration scan](evidence/infrastructure/security.json) |
| Documentation | Internal Markdown targets, source/evidence bindings and credential-pattern checks; all six Mermaid sources parsed and rendered |
| Containers and hosted CI | Separate [GitHub Actions jobs](https://github.com/rlbsem/enterprise-martech-ai-control-plane/actions) build the image, execute demo/tests, run workload and validate infrastructure/diagrams |

Docker is unavailable on the local Windows execution host. The container checks run in GitHub Actions; use the actual run result when assessing a particular commit. Live AWS task launches, private routing, certificate coverage, paging delivery and managed RDS failover/PITR are environment acceptance checks. No AWS resources were provisioned for this revision.

## Review decisions

| Question | Engineering decision |
|---|---|
| Could a restored database hide an already committed effect? | Compare the entire independent receipt ledger, including keys absent locally. Never generate replacement keys to make a mismatch disappear. |
| Can a saved pass report authorize a later resume? | No. Resume validates task bindings, checks quiescence and executes a fresh verifier before opening services. |
| Can an autoscaler or infrastructure apply reopen quarantine? | No autoscaling resource is enabled. Terraform ignores service counts; controlled release tooling owns them. |
| Can ECS stability hide a failed release? | Check the requested task definition after stability; a circuit-breaker rollback to another revision is a failed release. |
| Can API or monitoring compromise obtain execution credentials? | Separate IAM secret paths and SQL identities; observer sees queue only; API has no downstream token or SQL access. |
| Can an unknown result become a fresh action after revocation? | Reconcile the old fact first. Any new dispatch rechecks current revision, policy, approval and consent. |
| Does the deployment role own infrastructure or secret values? | No. It can operate approved task families and named services; administrators own Terraform, image publication and recovery authority. |
| Is a database row lock equivalent to preventing a remote request? | No. Local fencing and downstream deduplication solve different problems. The in-flight consent boundary remains explicit. |
| Is the workload a production benchmark? | No. Measured local admission/drain and a separate capacity model are presented with their exact scope. |

The version-1 schema and policy semantics are preserved. The cloud layer adds new login grants around that schema; it does not edit an already applied migration. The original [build record](evidence/build-checks.json) remains historical baseline evidence; its earlier wheel hash is not the cloud revision's build identifier.

## Reproduce

```bash
python scripts/verify.py
python scripts/validate_infra.py
npm ci
python scripts/render_diagrams.py
python scripts/repo_check.py
```

Supply a dedicated `TEST_ADMIN_DSN` ending in `_test` for the first command. Set `PUPPETEER_CONFIG` to a JSON browser configuration when using an existing Chromium installation; CI renders with its isolated runner configuration. Raw infrastructure check logs and the scanner JSON are retained beside the [infrastructure verification manifest](evidence/infrastructure/verification.json).
