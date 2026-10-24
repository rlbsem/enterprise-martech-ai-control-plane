# Permit issuance and receiver arrival review

Offline reanalysis of the four immutable qualifying records delivered at `b176cf3be3d8f606f0c6729bfc7e37878318a9a3`. No sustained workload was rerun and no limiter or application behavior changed. [Machine-readable results and input SHA-256 hashes](rate-review.json).

The database-assigned permit windows comply with the declared limit of **10 per destination per UTC epoch second**. Independent receiver journals contain **eight arrival buckets with 11 requests**, across **six destination/run combinations**. The observed receiver maximum is **11**, not a guaranteed upper bound.

| Run | Destination | Permit maximum per assigned window | Arrival maximum per UTC second | Arrival buckets above 10 |
|---|---|---:|---:|---:|
| 0 | healthy | 10 | 11 | 2 |
| 0 | affected | 10 | 11 | 1 |
| 1 | healthy | 10 | 10 | 0 |
| 1 | affected | 10 | 11 | 1 |
| 2 | healthy | 10 | 11 | 1 |
| 2 | affected | 10 | 10 | 0 |
| 3 | healthy | 10 | 11 | 1 |
| 3 | affected | 10 | 11 | 2 |

## Observed arrival buckets

| UTC second | Run | Destination | Arrivals | Permit windows represented |
|---|---:|---|---:|---|
| 2026-10-24T16:47:12+00:00 | 0 | healthy | 11 | 1792860431: 1, 1792860432: 10 |
| 2026-10-24T16:48:23+00:00 | 0 | healthy | 11 | 1792860502: 1, 1792860503: 10 |
| 2026-10-24T16:49:55+00:00 | 0 | affected | 11 | 1792860594: 1, 1792860595: 10 |
| 2026-10-24T16:55:12+00:00 | 1 | affected | 11 | 1792860911: 1, 1792860912: 10 |
| 2026-10-24T16:58:44+00:00 | 2 | healthy | 11 | 1792861123: 1, 1792861124: 10 |
| 2026-10-24T17:01:47+00:00 | 3 | healthy | 11 | 1792861306: 1, 1792861307: 10 |
| 2026-10-24T17:03:11+00:00 | 3 | affected | 11 | 1792861390: 1, 1792861391: 10 |
| 2026-10-24T17:03:53+00:00 | 3 | affected | 11 | 1792861432: 1, 1792861433: 10 |

At **16:47:12 UTC**, run 0 / healthy received one request assigned to permit window `1792860431` and ten assigned to `1792860432`. The earlier grant was journaled at `16:47:11.992358 UTC`; its request arrived at approximately `16:47:12.003593 UTC`. All eight overfull arrival buckets have the same one-prior-window plus ten-current-window composition.

## What passes, and what does not follow

The acceptance contract counts permits by the database-assigned `grant.window_id`. Receiver arrival buckets independently use `floor(request.at)` from the receiver journal, grouped by destination. Both receipt GETs and effect POSTs are included. The checker still fails a permit-window violation, unpermitted request or reused permit; an arrival overage is reported separately and is not relabeled as a permit-contract failure.

The current result uses `permit_issuance_violations` and `rate_observations` instead of the ambiguous `budget_violations` name. A passing result establishes the declared permit contract and existing effect invariants. It does **not** establish zero overages against a provider's strict arrival-based or rolling-window API quota. Client scheduling and transport can move an earlier permit into a later arrival bucket; fixed-window boundaries can also allow bursts across adjacent buckets. No rolling-window or remote-provider guarantee is measured here.

Historical summaries, run records, protocol/source hashes, timelines, prior failures and software reports retain their original bytes. Their archived `budget_violations: 0` field means permit-issuance compliance only. The [preservation record](preserved-files.json) also binds all runtime source files; the [source map](source-map.json) pins the two original checker/reporting files whose bytes appear in the run protocols. This mapping does not replace or modify observations.

## Stronger guarantees: proposal for separate review

If a provider requires stricter limits, evaluate a shared provider-compatible sliding-window or paced/leaky-bucket dispatch gate, with suitable headroom and all dispatch/reconciliation calls included. Such a sender can reduce bursts but cannot guarantee remote arrival timing under arbitrary transport delay. An absolute receiver-arrival guarantee requires provider-side enforcement or an explicit bounded-delay/server contract. No such redesign or guarantee is included in this correction.

## Verification

The focused tests cover delayed cross-window arrivals, genuine permit violations despite dispersed arrivals, exact UTC boundaries, destination separation, empty journals and all eight recorded overages. The complete local suite passed **334 tests, zero failures and zero skips** for this correction. GitHub CI independently checks the final PR head; [local verification metadata](verification.json) and [individual test results](tests.xml) identify the executed source and actual counts. Existing historical execution records remain separate.

Reproduce the offline analysis from the repository root; this starts no workers or databases:

```text
python scripts/check_isolation.py docs/evidence/dependency-isolation/portability-fix/runs --source-map docs/evidence/dependency-isolation/arrival-review/source-map.json
```

The output reports permit maxima and violations, receiver arrival maxima and over-limit buckets, and each overfull bucket's originating permit windows. [Original qualification and effect reconciliation](../portability-fix/README.md).
