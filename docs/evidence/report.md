# Executed control-plane proof

68 tests; 0 failures; 0 skipped. Real PostgreSQL and separate HTTP server processes. Generated 2026-09-12T03:44:42.010463+00:00.

| Scenario | Observed result |
|---|---|
| No-Regress | Opportunity -> MQL refused; final Opportunity |
| Consent revoked after approval | blocked; 0 messages |
| Response lost after commit | 2 worker attempts; 1 remote effect; reconciled success |
| Eight competing workers | 1 claim; 1 effect |
| Worker process killed after remote commit | succeeded; 1 effect |

The JSON cases retain IDs, decisions and receipts. `verification.json` records source hashes and runtime versions. `tests.xml` records every test. No GitHub Actions success, container execution, external SaaS or cloud deployment is claimed.
