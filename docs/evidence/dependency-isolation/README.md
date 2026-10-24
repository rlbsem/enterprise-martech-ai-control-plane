# Original dependency-isolation acceptance archive

[Current corrected-runtime acceptance](portability-fix/README.md). The results below are preserved observations of implementation `7103c63`; they are not proof of the later liveness correction.

**Executed locally on PostgreSQL 17.11 with separate HTTP receivers and four worker processes.** One healthy baseline and three mixed-outage repetitions each offered 5,000 synthetic proposals over a declared five-minute window. The receiver request journals and effect ledgers were read independently after workers stopped.

| Run | Admitted and reconciled effects | Explicit overload rejections | Healthy successful POSTs during the provider-fault interval |
|---|---:|---:|---:|
| Healthy baseline | 2,285 | 2,715 | 246 in the corresponding comparison interval |
| Outage repetition 1 | 2,075 | 2,925 | 291 |
| Outage repetition 2 | 1,912 | 3,088 | 298 |
| Outage repetition 3 | 2,081 | 2,919 | 281 |

All admitted jobs completed with exactly one matching destination effect. The checker found zero duplicate effects, substituted destinations, unaccounted jobs, client-concurrency violations or shared-budget violations. Each run reached its configured **10 total request permits per epoch-second per destination**. Receipt GETs consume that budget too; dispatch can use at most nine permits. Backlog is bounded at 128 per destination. The offered load intentionally exceeds the useful delivery capacity; rejected proposals are not counted as successful actions.

[Incident timelines](timeline.md) · [Complete run summaries](runs/summary.json) · [Protocols, observations and checks](runs/README.md) · [Operating contract](../../dependency-isolation.md).

## What the timelines establish

The affected receiver returned actual HTTP 429 and 503 responses during each scheduled fault. The healthy destination continued committing work. After injection ended, the affected destination accepted new work, the backlog alarm cleared through the authenticated local alert receiver, pending work drained and exact ledgers reconciled. The alarm is a **backlog threshold**, so it may already be firing before the provider fault because the offered load is excessive. It does not establish the cause of a backlog. Full read-only ledger reconciliation occurs after the measurement window, separately from alarm clearance.

The protocol declares a 300-second workload window; final observation and reconciliation add a few seconds. The JSON summaries expose per-destination admission, first-dispatch and terminal-latency percentiles, rejected and never-dispatched populations, request amplification and resource samples. These results are not a controlled before/after speedup study or a production SLO. The initial healthy run shared the local host with regression testing. Application CPU/RSS samples exclude PostgreSQL CPU/memory and can miss peaks; connection counts cover the local cluster.

## Software and preservation

The [unchanged baseline](baseline/verification.json) passed 298 tests. Its [original workload rerun](baseline/workload.json) produced 950 effects, 50 expected permanent rejections, 50 reconciliations and zero duplicates across 1,000 intents and four workers. The historical **323-test** [software verifier](software/verification.json) and [individual results](software/tests.xml) record the extended suite separately from sustained acceptance.

Focused tests include killing a routed worker after actual remote commit, waiting 30.1 seconds for actual permit expiry, then recovering the historical receipt after a customer revision changes. [Executed process-death record](software/bound-worker-death.json). Other tests cover current consent/owner/approval checks, admission races, scoped SQL permissions, exact recovery-reader scope and corrupted evidence.

The [preservation manifest](preserved-files.json) binds **309 original evidence and diagram artifacts** to their baseline Git bytes. Those files, original migration 1, existing business policy and native AWS execution records remain unchanged. Migration 2 is additive. The sustained source/protocol seals match implementation commit `7103c63`; report postprocessing has its own [manifest](timeline-manifest.json).

## Reproduce and check

Use the existing locked dependencies and local PostgreSQL bootstrap from the [runbook](../../dependency-isolation.md). Run one healthy baseline and three fresh mixed-fault repetitions:

```text
python scripts/isolation_acceptance.py --admin postgresql://postgres@127.0.0.1:5432/postgres --output work/new-healthy --duration 300 --proposals 5000 --request-limit 10 --fault healthy
python scripts/isolation_acceptance.py --admin postgresql://postgres@127.0.0.1:5432/postgres --output work/new-mixed-1 --duration 300 --proposals 5000 --request-limit 10 --fault mixed
```

Repeat the mixed command with new output paths for repetitions 2 and 3. Each protocol is written before traffic; source changes withhold qualification. Each run creates three uniquely named disposable local databases and performs a separate catalog read after dropping them. All twelve generated databases were independently observed absent: [run 0](runs/run-0/cleanup.json), [run 1](runs/run-1/cleanup.json), [run 2](runs/run-2/cleanup.json), [run 3](runs/run-3/cleanup.json).

Validate the delivered evidence without starting a workload:

```text
python scripts/check_isolation.py docs/evidence/dependency-isolation/runs --source-map docs/evidence/dependency-isolation/portability-fix/historical-source-map.json
python docs/evidence/dependency-isolation/render_timeline.py
```

The 60-second/100-proposal CI smoke run is separate from the four sustained Windows executions. [Development runs](development/README.md) retain the preliminary higher-budget observations and a source-change refusal. No new native AWS, model inference, vendor integration or commercial outcome is claimed. The historical missing native post-restore fail-closed result remains unverified.

The local software report binds its executed source before the final CI timing adjustment. Runtime and sustained-run source remain unchanged. The final workflow declares a 60-second smoke window and retains its JSON observations even on failure; hosted checks establish that final workflow separately. The original [30-second push-run failure](https://github.com/rlbsem/enterprise-martech-ai-control-plane/actions/runs/40110582737) remains visible; the [same-head PR run](https://github.com/rlbsem/enterprise-martech-ai-control-plane/actions/runs/40110598568) passed, exposing short-profile sensitivity. Subsequent source review found the successful-lookup pacing defect described in the current corrected-runtime entry point; the longer smoke pass did not rule it out.
