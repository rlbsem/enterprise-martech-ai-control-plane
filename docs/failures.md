# Failure semantics and recovery

## Remote uncertainty is a separate state

The central failure scenario deliberately commits `remote.effects` and then sleeps longer than the worker's HTTP timeout. The caller receives no usable response. The worker's durable intent already has `uncertain=true`, the original mutation and the same UUID key as the proposal. On its next attempt it performs `GET /effects/{key}`. A matching hash and receipt complete the job without another POST. Both the table count and final state are asserted against PostgreSQL.

The idempotency row **is the synthetic business effect**. In a real adapter, deduplication must be atomic with the actual business operation, or the external service must provide an equivalent contract. Writing an adapter ledger and then sending a non-idempotent email outside that transaction would not provide this guarantee. This repository does not claim that arbitrary CRM or email providers support its contract.

## Failure matrix

| Condition | Durable behavior | Operator interpretation |
|---|---|---|
| Duplicate source event | Original result returned; duplicate receipt audited | Safe redelivery |
| Changed content under same event ID | Conflict exception; existing authoritative state retained | Investigate upstream ID reuse; issue corrected event identity |
| Unknown/ambiguous identity | Event retained with review decision; no guessed merge | Review mappings, link evidence, request new delivery identity |
| Invalid contract | Exception contains error locations/types, not raw rejected values | Correct producer; do not retry malformed content forever |
| Wrong source / old sequence / lifecycle regression | Per-field refusal with provenance and exception | Examine producer authority and ordering |
| Stale/expired/denied proposal | No new dispatch | Submit fresh context; new approval if required |
| Consent/suppression changed after approval | New marketing dispatch blocked | Approval is not a privacy override |
| HTTP 429 | Durable retry; integer Retry-After bounded to 1–300 seconds | Rate-limited, not a governance exception |
| HTTP 5xx | Retry with uncertainty; reconcile before another mutation | A server error is not proof of no commit |
| Timeout before mutation | Unknown state; lookup finds no effect; current policy gates resend | May recover if still authorized |
| Timeout after mutation | Lookup finds original effect and verifies hash | Report success without duplicate mutation |
| Permanent 4xx | Dead letter with remote rejection | Fix adapter credentials/contract before reviewed requeue |
| Worker dies before completion | Lease expires; replacement worker reconciles persisted intent | Do not invent a new key |
| Overlapping workers | Token fences local completion; key/hash serializes remote effect | Lease recovery alone does not stop an old network request |
| Four failed completed attempts | Dead letter; uncertainty remains visible | Investigate and requeue same intent only when justified |

Retry delays use bounded exponential backoff plus jitter. An operator requeue resets the attempt budget but preserves the proposal, mutation, key, expiry and approval. Audit retains previous attempt records. Requeueing stale work can correctly produce `blocked`; it does not authorize a new action. A permanently invalid payload requires a new governed proposal, not alteration of the existing one.

## Consent linearization and in-flight requests

The customer row lock serializes source changes and dispatch authorization. Revocation committed before authorization is observed and blocks marketing. Authorization commits before HTTP, and no customer lock spans the network call. Consequently, a request already authorized can remain in flight while consent is revoked and may commit later. No instant global recall is claimed.

If recovery finds that the old key already committed, it records that historical fact even after revocation. If lookup reports no effect, the worker rechecks current policy and cannot newly resend revoked marketing. However, an earlier request can still be in flight after that lookup. A 404 is only absence of a committed record at lookup time, not proof of cancellation. The idempotency key prevents a second business effect if the earlier request ultimately commits. A blocked or exhausted job with `uncertain=true` remains an operator reconciliation concern.

Production options include an adapter-supported cancellation protocol, downstream enforcement of authorization epochs, or an explicitly agreed dispatch cutoff. Those are reference designs requiring the downstream provider's cooperation, not features implemented here.

## Failure injection

The synthetic downstream has a separately authenticated `/failure-plans` endpoint. It accepts a bounded list of `429`, `500`, `422`, `timeout_before`, and `timeout_after` modes for one UUID key. Modes are consumed transactionally; successful redelivery of an existing key returns its receipt before consuming another mode. Timeout injection happens outside the committed mutation transaction. The test harness uses a short HTTP timeout and a one-second injected delay.

This service is deliberately artificial and local. Its internal effects table makes business-effect counts directly inspectable. Failure injection is not a feature to expose on a real external service.
