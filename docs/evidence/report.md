# Executed control-plane proof

91 tests; 0 failures; 0 skipped. Real PostgreSQL and separate HTTP server processes. Generated 2026-10-08T02:06:57.289835+00:00.

| Scenario | Observed result |
|---|---|
| No-Regress | Opportunity -> MQL refused; final Opportunity |
| Consent revoked after approval | blocked; 0 messages |
| Response lost after commit | 2 worker attempts; 1 remote effect; reconciled success |
| Eight competing workers | 1 claim; 1 effect |
| Worker process killed after remote commit | succeeded; 1 effect |

The JSON cases retain IDs, decisions and receipts. `verification.json` records source hashes and runtime versions. `tests.xml` records every test. This record covers native execution; hosted checks are linked separately. AWS SDK tests use stubs and do not provision resources.
