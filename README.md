# Enterprise MarTech / AI Control Plane

Customer and commercial state crosses CRM, consent, scoring, revenue operations and activation systems. Each can trigger work; none should independently override authoritative truth. This control plane governs the boundary: who owns a field, which customer an event belongs to, whether a lifecycle change is valid, and whether an automated action is still authorized when it executes.

**Tools execute. The control layer determines what is true.**

Built for integration challenges across multi-brand operators, PE-backed portfolios, multi-product B2B SaaS and enterprise commercial systems: canonical identity, source provenance, No-Regress Logic, consent and approval controls, durable execution, and recovery across independent systems. The public implementation demonstrates those architecture patterns without exposing private client systems or claiming that this exact repository ran unchanged in client production. AI agents submit typed proposals; they do not receive source authority or downstream execution credentials.

**Why this matters to a business:** A CRM update, an AI recommendation or a timed-out integration request should not be allowed to silently corrupt the customer record or trigger the same downstream action twice. This system demonstrates three concrete protections: authoritative fields cannot be overwritten by the wrong source; permission and approval are checked again before execution; and a lost response is reconciled against the receiver's actual receipt before a retry can produce another effect.

**Example failure:** The receiving system commits an action, but its acknowledgment is lost. Instead of assuming failure and sending a new action, the control plane checks the stable effect ID, recognizes the existing receipt and preserves one downstream effect. After a database restore, services remain closed until recovered state agrees with independently held downstream receipts. These are tested controls in the published implementation, not claims of measured client savings.

**Start with the [executed proof](docs/evidence/report.md), [enterprise workload results](docs/evidence/enterprise-workload.json), and [database recovery design](docs/cloud-operations.md).** The implementation includes deployable AWS infrastructure, distinct service identities, immutable releases and a fail-closed recovery gate.

![Control-plane architecture](docs/diagrams/01-control-plane.svg)

## Engineering choices and tradeoffs

- **I kept AI outside the authority boundary.** An agent can propose an action, but it cannot assign field ownership, override source identity or hold downstream execution credentials. Typed proposals, scoped actors and a separate executor keep business state governed even when an AI output sounds persuasive. [Transaction and trust decisions](docs/architecture.md).
- **I chose durable intent and receiver receipts over blind retries.** An HTTP timeout cannot tell us whether the effect committed. The exact intent and effect ID are saved before dispatch; a retry first reconciles the receiver's receipt and rechecks authority if there is no matching effect. This is **at-least-once delivery with idempotent effects under the receiver contract**, not an unsupported exactly-once network guarantee. [Failure semantics](docs/failures.md).
- **I made database recovery conditional on business-state reconciliation.** A restored control database can be older than independently committed downstream effects. The executed remote-ahead case blocks resumption instead of treating a successful database restore as sufficient. [Recovery design](docs/cloud-operations.md) · [Recorded blocked case](docs/evidence/remote-ahead-restore.json).
- **I accepted a bounded performance tradeoff.** A global admission lock simplifies correct identity discovery in this implementation, but limits throughput. Its measured synthetic workload and the thresholds for introducing pooling or partitioning are documented; The production AWS infrastructure is defined but **not deployed**. [Capacity boundaries](docs/capacity.md).

## The invariants

| Boundary | Enforced behavior |
|---|---|
| Identity | Source record IDs resolve to canonical UUIDs. Matching email creates review candidates, never an automatic merge. |
| Authority | Field owners and source sequences determine accepted state. A later event cannot grant a source authority it does not have. |
| No-Regress | Lifecycle advances through Lead → MQL → SQL → Opportunity → Customer; stale regression is refused. |
| Consent and approval | Protected writes are denied. Marketing needs explicit approval bound to customer revision, policy and expiry. Current permission is checked again before dispatch. |
| Execution | A durable intent, stable effect ID and immutable payload survive retries. Leases distribute work; tokens fence stale workers. |
| Recovery | Unknown outcomes query the original receipt first. Restored control state is compared with the independent downstream ledger before services resume. |
| Accountability | Decisions retain actor, source evidence, provenance and policy hash. Application roles cannot rewrite audit history. |

## Execution and failure semantics

The API validates a proposal and commits eligible work to PostgreSQL. A worker claims it, locks the customer, rechecks permission, and commits the dispatch intent **before** making an HTTP request. The downstream commits its business effect and idempotency receipt together.

If the response disappears or the worker dies, the next worker looks up the same effect ID. A matching receipt establishes an existing fact; it does not authorize another action. An absent receipt requires a fresh permission check before retry. Conflicting identities, permanent rejection and exhausted retry budgets become reviewable exceptions.

Delivery is **at least once with idempotent effects under the downstream adapter contract**. Consent is enforced at dispatch authorization; an independently running remote system cannot recall an already in-flight request. See [precise failure semantics](docs/failures.md) and the [execution diagram](docs/diagrams/04-execution.svg).

## AWS operating architecture

**AWS readiness update:** [Local tests and static infrastructure checks](docs/evidence/aws-readiness/implementation/README.md)
cover the hardened release, startup and restore boundaries. A bounded read-only collector
is ready for an authorized deployment. [Native acceptance](docs/aws-native-acceptance.md)
and [cost/teardown review](docs/aws-cost-review.md) remain pending native execution and explicit
cost approval; these records do not claim a deployed AWS environment.
[Ohio preflight](docs/evidence/aws-readiness/ohio/README.md) independently confirmed
regional access and corrected the earlier Canadian-region denial diagnosis. The
[separate Single-AZ/PITR proposal](docs/aws-ohio-acceptance.md) preserves the production
architecture. The [executable Ohio fixture, isolated Terraform and independent cleanup guard](docs/aws-ohio-execution.md) are implemented and locally tested. The [isolated guard and image bootstrap have deployed and passed native arming/checker and exact image read-back checks](docs/evidence/aws-readiness/guard-native/README.md). The [native RDS runtime has deployed](docs/evidence/aws-readiness/free-retention/README.md) and [bootstrap is being corrected against the restricted RDS owner](docs/evidence/aws-readiness/rds-bootstrap/README.md); SQL/PITR acceptance remains pending.
The [minimal acceptance assessment](docs/aws-minimal-acceptance.md) explains which
recovery tests can run without ACM, custom DNS, an ALB or a workstation VPN.

![Private AWS runtime](docs/diagrams/02-aws-runtime.svg)

- Separate Fargate services for API, workers, downstream and a read-only observer; one-shot migration and verification tasks.
- Immutable ECR tags and digest-qualified task revisions, bound to source commits. Protected GitHub OIDC releases use short-lived authority.
- Two private, encrypted PostgreSQL RDS instances keep control state and downstream receipts in independent recovery domains. Multi-AZ, 14-day PITR, deletion protection and TLS are configured.
- Private HTTPS ingress, explicit service-to-database security groups, and private ECR/Secrets/Logs endpoints. No public database, public task address or NAT dependency.
- Secrets Manager values are created and retrieved at runtime. Terraform contains secret identifiers, never application passwords or token values.
- CloudWatch tracks eligible-work age, unresolved outcomes, retries, reconciliations, dependency errors and worker heartbeat. Metrics have only environment/service dimensions.

[Infrastructure and deployment](infra/README.md) covers foundation state, enterprise network/PKI prerequisites, IAM grants, the immutable image path and controlled releases. [Trust boundaries](docs/diagrams/03-trust-boundaries.svg) show which service can access each authority.

### Recovery is a business-integrity gate

A successful database restore alone is insufficient. Downstream effects can be newer than the restored local record. The operating tools drain admission and execution, restore into a **new** instance, bind its endpoint, and compare all local intents with independent receipts. Missing intents, mismatched hashes and missing acknowledged receipts keep services closed. A saved pass report cannot reopen them: resume runs a fresh verification task.

[Restore and reconciliation runbook](docs/cloud-operations.md) · [Recovery diagram](docs/diagrams/06-database-restore.svg) · [Executed remote-ahead case](docs/evidence/remote-ahead-restore.json)

## Workload and capacity

| Validation layer | Purpose and observed result |
|---|---|
| Exact correctness suite | Original 68 tests retained, plus cloud authority, restore comparison and release-gate cases; real PostgreSQL and separate HTTP processes. |
| Enterprise workload | 1,000 customer accounts, 1,000 intents, four worker processes, 1,200 attempts; 950 unique effects, 50 intended permanent rejections, 50 lost acknowledgements reconciled, zero duplicate effects. |
| Measured local run | 19.3 seconds admission; 55.5 seconds drain; 18.0 intents/second during drain. HTTP admission p95: 63 ms. |
| Production capacity model | Explicit connection budgets, admission-lock limits, worker concurrency, retention, failure domains and scaling decision points. |

The workload has deterministic source records and fault schedules; timings and generated receipt IDs are observations. [Capacity and boundaries](docs/capacity.md) explains why a larger customer estate does not imply unbounded event throughput, and when pooling, partitioning or queue separation becomes justified.

## Run and verify

With Docker Compose v2:

```bash
docker compose run --build --rm demo
docker compose down
```

The demo asserts authority protection, the consent race and lost-acknowledgement recovery. To run the full suite in a container:

```bash
docker compose --profile test run --build --name control-proof verify
docker cp control-proof:/app/docs/evidence ./local-proof
docker rm control-proof
docker compose down
```

For native development, use Python 3.12 and a dedicated PostgreSQL 17 instance:

```bash
python -m venv .venv
# Activate .venv for your shell.
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
# CONTROL_ADMIN_DSN: a disposable demo database you own.
python scripts/local_demo.py
# TEST_ADMIN_DSN: a separate disposable database ending in _test.
python scripts/verify.py
```

The verifier deliberately recreates its test schemas. [Local operations](docs/operations.md) supplies exact environment examples. [Workload instructions](docs/capacity.md) use a separate empty database. Infrastructure validation needs Terraform 1.13.5 and Trivy 0.75.0, **no AWS credentials**:

```bash
python scripts/validate_infra.py
```

## Evidence and implementation map

| Review area | Entry point |
|---|---|
| Governance and transactions | [Architecture](docs/architecture.md), [governance](docs/governance.md), [source](src/controlplane/service.py) |
| Leases and reconciliation | [Worker](src/controlplane/worker.py), [failure model](docs/failures.md), [tests](tests/test_execution.py) |
| Deployment and security | [Terraform](infra/aws), [runtime bootstrap](src/controlplane/cloud.py), [release gate](src/controlplane/release.py) |
| Restore and operations | [Read-only comparison](src/controlplane/operations.py), [PITR tool](src/controlplane/restore.py), [runbook](docs/cloud-operations.md) |
| Verification | [Generated test proof](docs/evidence/verification.json), [infrastructure checks](docs/evidence/infrastructure/verification.json), [validation guide](docs/validation.md), [GitHub Actions](https://github.com/rlbsem/enterprise-martech-ai-control-plane/actions) |

The isolated guard, image bootstrap and native RDS runtime have deployed; production deployment, live failover and PITR remain [environment acceptance checks](docs/cloud-operations.md).
