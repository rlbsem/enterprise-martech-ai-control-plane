# Corrected-runtime dependency-isolation acceptance

**Actual local PostgreSQL/HTTP execution, with separate receiver processes and four worker processes.** One healthy baseline and three mixed-fault repetitions each offered 5,000 synthetic proposals over a predeclared 300-second window. Source was frozen at implementation commit `0c30d62` before traffic. No cloud, model or vendor execution was performed.

| Run | Admitted and independently matched effects | Explicit overload rejections | Healthy successful POSTs during fault interval |
|---|---:|---:|---:|
| Healthy baseline | 3,434 | 1,566 | 366 in the corresponding comparison interval |
| Outage repetition 1 | 2,822 | 2,178 | 296 |
| Outage repetition 2 | 2,909 | 2,091 | 274 |
| Outage repetition 3 | 2,870 | 2,130 | 292 |

All **12,035 admitted jobs** completed with one exact matching effect; **7,965 offers** were explicitly rejected at the bounded backlog. The independent checker found zero duplicate effects, substituted destinations, unaccounted jobs, permit-issuance budget violations or client-concurrency violations. The budget is ten request permits per fixed UTC epoch-second per destination, including receipt GETs; dispatch can use at most nine. Client concurrency is two and backlog is bounded at 128 per destination. Receiver journals separately peak at **11 arrivals per UTC second**, with **eight buckets above ten across six destination/run combinations**. Permit compliance does not guarantee compliance with a strict provider-arrival or rolling-window quota. [Exact rate analysis](../arrival-review/README.md).

[Recorded incident timelines](timeline.md) · [Complete summaries](runs/summary.json) · [Protocols and compressed observations](runs/README.md) · [Current software verification](software/verification.json) · [Operating contract](../../../dependency-isolation.md).

## Liveness correction and preserved evidence

A successful request previously set a 50-millisecond pacing delay. On a fast database, a receipt GET returning 404 could repeatedly defer the following POST. Source review after the first CI failure identified this starvation path even though the original sustained runs passed. The corrected runtime applies cooldown only after failure; fixed-window quotas and concurrency bounds remain enforced. Tests execute the preserved old implementation to establish its positive delay and verify immediate dispatch eligibility after successful lookup in the corrected implementation.

The [original four runs](../runs/README.md), [323-test software record](../software/verification.json), original observations and manifests retain their bytes. They establish execution of the earlier source, not qualification of the correction. [Explicit historical source mapping](historical-source-map.json) resolves the changed runtime and harness source files to exact preserved snapshots. Original CI failure and subsequent results remain linked in the [archive entry point](../README.md).

## Software verification

The unchanged baseline passed **298 tests**. The [pre-correction complete verifier](software/verification.json) passed **328 tests with zero failures and zero skips**, separately from sustained acceptance. [Individual cases](software/tests.xml), the [actual killed-worker/30.1-second permit-expiry record](software/bound-worker-death.json), and the [preserved pacing counterexample](software/preserved-pacing-counterexample.json) remain inspectable. The original 1,000-intent/four-worker baseline workload produced 950 effects, 50 expected permanent rejections, 50 reconciliations and zero duplicates; it is not part of the 20,000-offer isolation table.

The [rate-evidence correction](../arrival-review/README.md#verification) has separate current regression metadata and CI; the original 328-test report remains unchanged.

## Independent evidence and limits

Each receiver writes its own actual request journal and PostgreSQL effect ledger. After all worker processes stop, reconciliation independently reads the healthy, affected and legacy ledgers; exact keys, destinations, content hashes and receipts must match. The authenticated local backlog alert fires and clears, with successful affected-destination POSTs after fault end and before clearance. Alarm clearance is distinct from the final ledger comparison.

The mixed fault returns actual HTTP 429 for the first half of the injection window and 503 for the second. Healthy progress is checked during that same window. The complete summaries retain rejected, never-dispatched and unfinished populations, nearest-rank admission/dispatch/terminal percentiles, oldest-queue-age samples, request amplification, audit-derived scheduler deferrals, business retries, application CPU/RSS and cluster connections. Deferrals equal immutable claim count minus final consumed attempts in this harness, which never administratively requeues jobs or resets attempt counters; older records without exported claim counts report null rather than zero. The [server preflight](server-preflight.json) declares 100 connections and 128 MiB shared buffers before traffic. An earlier run on a 25-connection/400-KiB cluster aborted with connection exhaustion and related Windows shared-memory mapping errors; its [failure](pre-resource-adjustment/failed-mixed/failure.json) and [partial journals](pre-resource-adjustment/failed-mixed/partial-receiver-journals.json.gz) remain preserved. Root cause of the transient server exhaustion was not conclusively established, and that run is not counted as passing. The [host observation](host-observation.json) was collected during these corrected runs; it does not reconstruct baseline machine utilization. No regression suite was deliberately run concurrently with these four runs, but workstation load is uncontrolled. Samples exclude PostgreSQL CPU/memory and can miss peaks. These are local experiment criteria, not production SLOs or a controlled before/after speedup study.

All twelve disposable run databases were separately confirmed absent after deletion: [run 0](runs/run-0/cleanup.json), [run 1](runs/run-1/cleanup.json), [run 2](runs/run-2/cleanup.json), [run 3](runs/run-3/cleanup.json). The [original 309-artifact preservation contract](../preserved-files.json), migration 1 and historical AWS boundary remain unchanged. The missing native post-restore fail-closed result is still unverified.

## Reproduce and check

Use the existing locked dependencies and isolated local PostgreSQL bootstrap. Run one healthy lane and three mixed lanes using fresh output directories; do not reuse an existing run directory. The full [runbook](../../../dependency-isolation.md) defines clocks, quotas, retry and recovery semantics. Baseline commands were the same repository verifier and original local_demo workload at `5af0642727b2f5c1c2d7759bcbfea7171ad131f6`; dependencies and exact source hashes are preserved in the [baseline verification](../baseline/verification.json).

```text
python scripts/isolation_acceptance.py --admin postgresql://postgres@127.0.0.1:5432/postgres --output work/new-healthy --duration 300 --proposals 5000 --request-limit 10 --fault healthy
python scripts/isolation_acceptance.py --admin postgresql://postgres@127.0.0.1:5432/postgres --output work/new-mixed --duration 300 --proposals 5000 --request-limit 10 --fault mixed
python scripts/check_isolation.py docs/evidence/dependency-isolation/portability-fix/runs --source-map docs/evidence/dependency-isolation/arrival-review/source-map.json
python scripts/check_isolation.py docs/evidence/dependency-isolation/runs --source-map docs/evidence/dependency-isolation/portability-fix/historical-source-map.json
```

The offline checker/reporting correction uses an explicit source map for the original checker and reporter. All runtime files still match their recorded hashes. Archived `budget_violations` fields describe permit issuance only, not receiver arrivals.

The 60-second/100-proposal Linux CI smoke is a separate harness check. It does not substitute for the sustained executions. Current CI and local software results are reported separately; archived records are never rewritten to claim a later runtime passed.
