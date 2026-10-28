# Operational recovery and business integrity

A database can restore successfully while its application is unsafe to reopen. Independent downstream effects may be newer than restored intents, a saved approval may be stale, and a restored consent record may no longer describe current permission.

The recovery coordinator adds durable sessions and a closed admission/dispatch gate to the existing control plane. It reuses canonical source ingestion, the leased worker, independent HTTP receiver and receipt reconciler. It does not insert missing intents to make a comparison pass.

## Executed local acceptance

The executable acceptance creates separate PostgreSQL control and receipt databases. It stops control writers and captures actual PostgreSQL database copies using `CREATE DATABASE ... TEMPLATE`, then allows more effects to commit. It terminates a real worker after the independent receiver commits, restores the earlier control copy into a new database and starts recovery with work closed.

This is a real local database snapshot/restore with actual HTTP and process boundaries. It is **not WAL replay, native RDS PITR, or a production RPO/RTO measurement**.

| Scenario | Required and executed behavior |
|---|---|
| Restored control behind downstream | Three restored intents versus five independent effects; two missing intents keep recovery closed; no dispatch |
| Lost acknowledgment | Stable original key and matching independent receipt recover the historical effect without another mutation |
| Worker terminated after commit | Actual process termination, naturally expired lease, replacement claim and rejected completion using the old lease token |
| Conflicting mutation | A deliberately corrupted control copy is blocked; the original independent receiver is unchanged |
| Missing acknowledged receipt | A separate negative-test copy loses one receipt; the original receiver remains intact; recovery is blocked |
| Authorization changed | Fresh authenticated owner-source observations are required after recovery starts; revoked consent blocks new marketing while committed historical effects remain recognized |
| Valid controlled resumption | A later complete control snapshot matches the unchanged receiver; fresh verification and exact approval open the gate; existing uncertain work reconciles and one new authorized effect commits |
| Stale approval | A real source update after approval invalidates the earlier proof and prevents reopening |

The local run observes five original effects and six after controlled new work, with no rekeyed or duplicate business effects. Nine run-owned databases are removed. Negative findings remain in the structured result. The full regression suite currently contains 356 passing tests; CI and native AWS observations are separate boundaries.

## Durable gate

Migration 3 adds `cp.recovery_sessions`, append-only `cp.recovery_proofs` and `cp.recovery_gate`. Migrations 1 and 2 retain their original bytes. New work and dispatch authorization acquire the gate's shared transaction lock. Closing and reopening acquire its exclusive lock. SQL triggers also reject direct new proposal/job insertion while closed.

Source truth can still refresh through its existing authenticated authority boundary while work is closed. Source writes share the gate lock, so they cannot straddle the coordinator's reopen transaction. Fresh consent/privacy provenance must reference source events received after the recovery session began; an old restored permission flag is insufficient. This relies on the authoritative source's truth, not a claim of independent verification of a compromised source.

A proof binds the selected recovery point, source/restored database identities, exact deployment fingerprint, policy, all persisted control business/configuration rows, relevant role/table privileges and the independent receiver snapshot. A live process/task witness must establish registered-runtime quiescence. The coordinator re-reads state during approval and again during resumption, rejects changed or expired proofs and requires the exact operator-approved proof ID. An edited JSON report is not an execution authority.

The deployment verifier uses `runtime_acceptance_snapshot`: a closed gate is a failed deployment acceptance even if the separate receipt comparison is internally consistent. Receipt inspection while closed and permission to run services are deliberately different decisions.

## Authorities and limitations

- The recovery coordinator can write operational recovery metadata, not customer facts or downstream effects.
- The independent verifier reads control state and receiver rows. It cannot dispatch, edit proof history or reopen the gate.
- Runtime children receive their own API, worker or receiver credentials; owner credentials are excluded.
- Actual database connections are compared with the bound restored and independent database identities on every observation.
- Both stores must be quiesced before comparison. There is no cross-database atomic snapshot or protection against an untrusted database/infrastructure administrator.
- Resume approval authorizes the checked deployment fingerprint. Normal dispatch still rechecks current proposal, revision, policy, consent and suppression; a receipt only establishes historical execution.
- The independent receiver commits its synthetic business effect and receipt atomically. These results do not establish exactly-once behavior for an arbitrary external vendor.

## Reproduce without AWS

Use a disposable local PostgreSQL cluster and a database ending `_test`. The harness rejects external hostnames and never restores or deletes the supplied database. Only fresh run-owned database names are created/copied/dropped.

```bash
export CONTROL_RECOVERY_ADMIN_DSN='host=127.0.0.1 port=5432 user=postgres dbname=control_test'
python scripts/recovery_acceptance.py --confirm-isolated-postgres --output work/recovery-run-01
python scripts/verify.py work/recovery-regression
```

Install the locked dependencies as described in the main README. The harness refuses an existing output directory. Raw process logs remain private under `.local`; structured reports contain synthetic business records and hashes. The test suite also executes the complete harness. PostgreSQL itself performs database copies, so no mismatched `pg_dump` client is required.

Implementation: [coordinator](../src/controlplane/recovery.py), [additive migration](../src/controlplane/recovery.sql), [actual recovery harness](../src/controlplane/recovery_lab.py), [tests](../tests/test_recovery_acceptance.py), [independent comparisons](../src/controlplane/operations.py).

## Native AWS boundary

The [October 19 native record](evidence/aws-readiness/retained-two-instance/native-outcome/README.md) remains unchanged: retained-backup restoration reached `available`, but final restored-state application reconciliation was not recorded. Current local acceptance does not close that native gap. Full production-style ECS/HTTPS/Multi-AZ recovery has not been executed.

The current owner-authorized experiment is capped at USD 20 planned consumption, conditional on fresh Free-plan, credit, eligibility, quota, cost and cleanup checks. It does not authorize a Paid upgrade, billing changes, production use or out-of-pocket charges. Native execution, if feasible, will have a new source-bound record and independent cleanup/cost observations. No infrastructure is provisioned by this local harness or ordinary PR verification.

[Evidence preservation and status](evidence/cloud-recovery/README.md) · [Existing production operations](cloud-operations.md)
