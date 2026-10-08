# Capacity and enterprise workload

The enterprise context is a multi-product commercial estate: CRM owns contact and lifecycle fields, independent systems own consent/privacy/scoring, and workflows propose downstream account activation or synchronization. Wrong authority, duplicate effects and recovery gaps can corrupt eligibility, attribution and revenue-operations reporting even when every individual API call succeeds.

## Three different evidence layers

Exact tests establish ordering and authority. The larger workload tests operational composition. Capacity planning extrapolates neither into an unmeasured production SLA.

The [executed workload](evidence/enterprise-workload.json) admits 1,000 accounts across 12 commercial operating units, progresses each from Lead to Opportunity, and submits one stable synchronization intent. Every tenth proposal is resubmitted to exercise admission idempotency. Each 20-account group includes one 429, one 500, one timeout before commit, one lost acknowledgement after commit and one permanent rejection. Four independent worker processes drain the same durable queue.

Observed: 1,200 attempts; 950 successful unique effects; 50 expected permanent rejections; 50 recovered committed effects; no duplicates. The read-only comparison finds no unexplained remote-ahead state. The schedule hash permits repeatability; canonical UUIDs, receipts, worker assignment, backoff jitter and elapsed timings remain runtime observations.

Run against a **new empty disposable database**, after installing the root lock file:

```bash
export CONTROL_ADMIN_DSN='postgresql://postgres:local-admin-only@127.0.0.1:5432/control_workload'
export CONTROL_WORKLOAD_ENTITIES=1000
export CONTROL_WORKLOAD_WORKERS=4
export REMOTE_TIMEOUT=0.5
python scripts/local_demo.py
```

PowerShell uses `$env:NAME='value'` for the same variables. Unset `CONTROL_WORKLOAD_ENTITIES` to return to the three-case demo. The harness refuses a populated customer table. It starts separate HTTP processes and workers, retains the database for inspection, and writes `work/workload/result.json`. Fault injection is accessible only through its separate injector credential. The CI workload uses its own database and the same deterministic schedule.

## Capacity model and decision points

| Constraint | Current design | Operating decision |
|---|---|---|
| Admission serialization | Global identity lock protects discovery and candidate matching. | Measure lock waits and p95 admission latency. Partition only after an explicit subject/email collision strategy; simply increasing API replicas cannot remove this serialization. |
| Worker concurrency | One synchronous effect at a time per worker; row locks and leases allow competing workers. | Bound worker count by provider rate limits and connection headroom. Scale through the approved release manifest (1–8 replicas); automatic scaling is intentionally absent so it cannot reopen a recovery quarantine. |
| Connection budget | Each operation opens a short psycopg connection with statement/lock timeouts. | Budget API concurrency + workers + observer + migration/recovery headroom against RDS limits. Add pooling after measuring churn; do not set an autoscaling maximum from CPU alone. |
| Dependency throughput | Throughput is bounded by latency, reconciliation lookups and retry traffic. | Approximate useful effects/sec as workers × success fraction / average service time; validate against measured workload. Limit concurrency before saturating a rate-limited adapter. |
| Database sizing | Configurable encrypted PostgreSQL instances; Multi-AZ default. | Start from working-set, IOPS, WAL and connection measurements. Burstable classes need credit monitoring; sustained workloads justify non-burstable classes. Aurora is a separate cost/availability decision, not a free scaling switch. |
| Queue | PostgreSQL outbox co-commits decisions and execution work. | Introduce broker fan-out only when measured throughput, tenant fairness or adapter isolation justifies the additional consistency and replay boundary. Preserve the same durable effect identity. |
| Retention | Logs: 90 days. PITR: 14 days. Audit and receipts: retained without automatic deletion. | Define legal/operational retention before production. A receipt must outlive every permitted retry and recovery horizon. Partition/archive event history only with tested lookup and audit continuity. |
| Failure domains | Control RDS and downstream RDS restore independently. Private services span two AZs. | Multi-AZ handles instance/AZ faults; it is not regional DR. Cross-region backup copies, KMS authority and tested DNS/network failover are separate acceptance work. |
| Environments | Isolated state per environment; recommended separate accounts and CIDRs. | Keep production release approvals, secrets, keys, databases and evidence independent from stage. Foundation administrators own infrastructure; release automation cannot create IAM grants. |

## Boundaries that remain explicit

One global consent and suppression value per customer does not encode purpose, channel, jurisdiction or legal basis. Production privacy policy requires that domain model before adapter rollout. There is no terminal cancellation protocol for a request already in flight.

The API and executor are trusted processes. SQL grants prevent cross-service access and protect immutable ledgers; a compromised API can still manipulate the operational tables it must write. The worker has an UPDATE grant on the customer revision column solely because PostgreSQL requires UPDATE privilege for its row lock. This does not confer source-field writes, but it is not a hostile-code sandbox. An administrator can alter grants/triggers; independent immutable audit export is needed for stronger tamper resistance.

The adapter commits an effect and receipt atomically and retains deduplication keys indefinitely. Every real provider must be assessed for key retention, lookup consistency and payload identity before that contract is relied on. Recovery comparison currently reads complete intent/receipt sets into memory: bound recovery volume, measure it, and implement streamed comparison/checkpoints before a ledger exceeds available verifier memory. Bulk query timeouts deliberately fail closed.

## Cost ownership

No AWS resources were provisioned for this revision. The deployable footprint has two Multi-AZ RDS instances, seven default service tasks, an internal ALB, four interface endpoints in two AZs, encrypted secrets/logs and a private hosted zone. These have standing cost even at low traffic. A development environment can explicitly choose Single-AZ; production defaults stay Multi-AZ. Endpoint and database baseline charges dominate a short exercise, so creating this footprint simply to obtain a screenshot is unnecessary.

Use the [AWS pricing calculator](https://calculator.aws/) with the intended region, retention and traffic before authorizing an environment. Budget alarms are alerts, not a hard spending cap. Destruction requires a reviewed retention decision because databases, state and encryption keys are deliberately protected.
