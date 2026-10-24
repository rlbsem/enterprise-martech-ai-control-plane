# Development observations excluded from final acceptance

These files preserve preliminary executions separately from the final frozen version-2 protocol. They do not establish the final implementation's performance and are not included in its four-run result table.

- [Initial higher-budget healthy run](isolation-qualifying-healthy.json.gz): five minutes, 5,000 proposals, 2,330 accepted effects and 2,670 explicit overload rejections. The old worker polling configuration limited progress; this was not a controlled optimization comparison. Its source was an uncommitted development snapshot.
- [Withheld version-1 run](qualified-v1-0-healthy.json.gz): actual observations were retained, but the runner refused qualification because implementation/reporting source changed during execution. Its protocol/source hashes remain as observed; no successful qualification record was manufactured.

The final version-2 runs used a predeclared lower request budget to exercise a binding limit, enabled faster polling for the isolated lane and froze source before traffic. No acceptance threshold was relaxed after observing their outcomes. Earlier syntax/setup diagnostics and smoke journals remain in ignored local workspaces; they are not native execution evidence. All original pre-existing repository evidence remains untouched.

The final publication regression also exposed a test-fixture race: **1 failed, 322 passed** when the reserved-capacity assertion crossed an actual one-second boundary. The stored test bucket is now pinned ahead of wall time, exercising the existing backwards-clock clamp while keeping the spent-capacity assertion stable. Runtime capacity code and sustained observations were unchanged. The failed console record remains in the ignored workspace; the current software evidence records the fresh corrected suite.
