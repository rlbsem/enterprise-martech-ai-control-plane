# Development observations excluded from final acceptance

These files preserve preliminary executions separately from the final frozen version-2 protocol. They do not establish the final implementation's performance and are not included in its four-run result table.

- [Initial higher-budget healthy run](isolation-qualifying-healthy.json.gz): five minutes, 5,000 proposals, 2,330 accepted effects and 2,670 explicit overload rejections. The old worker polling configuration limited progress; this was not a controlled optimization comparison. Its source was an uncommitted development snapshot.
- [Withheld version-1 run](qualified-v1-0-healthy.json.gz): actual observations were retained, but the runner refused qualification because implementation/reporting source changed during execution. Its protocol/source hashes remain as observed; no successful qualification record was manufactured.

The final version-2 runs used a predeclared lower request budget to exercise a binding limit, enabled faster polling for the isolated lane and froze source before traffic. No acceptance threshold was relaxed after observing their outcomes. Earlier syntax/setup diagnostics and smoke journals remain in ignored local workspaces; they are not native execution evidence. All original pre-existing repository evidence remains untouched.

The final publication regression also exposed a test-fixture race: **1 failed, 322 passed** when the reserved-capacity assertion crossed an actual one-second boundary. The stored test bucket is now pinned ahead of wall time, exercising the existing backwards-clock clamp while keeping the spent-capacity assertion stable. Runtime capacity code and sustained observations were unchanged. The failed console record remains in the ignored workspace; the current software evidence records the fresh corrected suite.

GitHub's initial 30-second/100-proposal smoke run failed with `unfinished accepted work` after the regression suite and original enterprise workload passed. A duplicate PR run at the same head passed. The final CI-only profile predeclares 60 seconds for the same 100 proposals and keeps the strict completion gate. The four 300-second/5,000-proposal frozen acceptance runs and runtime code are unchanged. See the linked Actions records in the current evidence entry point; no failed result is relabeled as passed.
