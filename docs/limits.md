# Known limits and production reference

The implemented system is a bounded, single-tenant correctness demonstration. Its difficult failure cases are real local execution; neither its data volume nor its process topology is a production capacity claim.

| Area | Current implementation | Production reference, not deployed |
|---|---|---|
| Identity ingestion | Global admission lock, exact source IDs, email review candidates | Partition identity locks using a documented key strategy; measure contention; design reviewed merge/split and source-record migration workflows |
| Database access | Short psycopg connections, statement/lock timeouts, JSONB state | Pooling, connection budgets, schema evolution automation, transactional load tests and index/workload measurement |
| Queue | PostgreSQL leases, backoff, durable retries and exceptions | Measured queue-age objectives, bounded per-tenant capacity and fairness, adapter circuit breakers; broker only if measurements justify another boundary |
| Privacy | One global consent boolean and suppression boolean per synthetic customer | Purpose/channel/jurisdiction-specific consent receipts, legal basis, retention, erasure and propagation requirements designed with the relevant stakeholders |
| Authentication | Distinct local role tokens; trusted API and executor processes | OIDC/service identity, key rotation, scoped identities, TLS, network policy, request size/rate budgets and tenant/object authorization |
| Audit | Append-only permissions plus triggers; immutable policy snapshots | Independently administered immutable export, retention, access review, verification against deliberate privileged tampering |
| Policy evolution | One versioned policy/evaluator and historical snapshot storage | Release/migration compatibility checks, staged policy rollout, rollback and independently reviewed version lifecycle |
| Remote contract | Atomic synthetic effect + receipt, stable-key lookup | Provider-specific verification of idempotency retention, payload comparison and reconciliation consistency; no assumed vendor support |
| Consent in flight | Permission checked before durable dispatch; subsequent retries recheck | Cooperative cancellation/authorization epochs if a stronger cutoff is required |
| Reliability | Actual worker kill/lease recovery and HTTP fault tests | Database failover, backup/restore, disk exhaustion, network partitions, regional loss and sustained soak/load testing |
| Delivery | Native PostgreSQL/HTTP run; Compose and Actions configuration | Observed container and hosted CI runs before claiming either; hardened image/digest and dependency update process |

Canonical event bodies and audit snapshots contain synthetic contact fields. They are not a production PII retention design. The application role can insert audit entries and modify operational tables; a compromised trusted control-plane process can misbehave. The protected boundary demonstrated is an untrusted API actor plus database role separation, not resistance to every administrator or arbitrary code execution in the trusted executor.

The one-second fault model proves selected ordering and uncertainty cases, not every network schedule. There is no terminal cancellation protocol for an already dispatched request, and an uncertain blocked/dead job may need later manual reconciliation. The mock service retains keys indefinitely; a real provider's shorter deduplication retention would require a carefully bounded replay policy.

No Salesforce, HubSpot, paid LLM, cloud deployment, customer production history or performance SLA is claimed. This repository remains centered on customer identity, MarTech state and AI/automation control. Revenue-to-GL traceability, billing reconciliation, legal-entity mappings and M&A integration belong to a separate future project.
