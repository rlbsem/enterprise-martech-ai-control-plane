# Two-instance retained-backup candidate

**No native retained-backup PITR execution is claimed. No resources were created for this candidate.** The [previous native SQL execution and quota rejection](../native-sql/README.md) remain the historical AWS result.

The [execution and approval procedure](../../../aws-retained-pitr.md) documents the same-region deleted-source API, explicit retained-backup deletion flag, destructive-source risk, Free-plan uncertainty, independent receipt comparison and cleanup. Production infrastructure is unchanged.

| Evidence | Actual boundary |
|---|---|
| [Local verification](local/verification.json) and [report](local/report.md) | Real local PostgreSQL/HTTP regressions; offline AWS service-contract/state-machine tests. Not AWS recovery execution. |
| [Infrastructure verification](infrastructure/verification.json) | Four Terraform roots validate, 16 mocked runs pass, static security/secret scans pass. No apply. |
| [Authenticated guard plan](guard-plan-review.json) | Saved read-only account-specific Terraform plan: 24 creates, zero updates/deletes. Not approved/applied. Three role policies depend on new ARNs and remain unknown until apply; their exact source expressions are included. |
| [IAM analysis](iam-analysis.json) | Static analyzer 0.3.0 reports 65 actions and seven wildcard warnings. Output is a review aid, not an applied policy or proof of Free-plan/SCP permission. |
| [Credit estimate](credit-estimate.json) | Reproducible four-hour two-instance model, USD 1.623726 credit consumption; USD 5 planning allowance, not a hard cap or an actual bill. |
| [Previous-run follow-up](prior-followup.json) | Actual read-only AWS observation on October 19 UTC: old task definitions gone; five secrets and two keys still pending deletion. FREE/ACTIVE; zero returned usage records do not prove zero usage. |

The new tests cover unchanged receipt contents, physical identity, source/window/tag/key/snapshot prerequisites, checkpoint read-back failure, source-deletion approval, lost acknowledgement, retained-backup absence, restore denial, changed remote contents, inherited retention correction and scoped cleanup. Fake AWS success values in test fixtures are not published as native results. Both Delete and Restore inputs are checked against the pinned real botocore service schema.

The new guard bundle and exact saved-plan digest are recorded in the plan review. A later execution needs a new CI image containing `receipt-ledger-sha256-v1`; the old native image does not establish that contract. The authenticated runtime plan remains blocked on actual guard metadata and requires its own approval.

Private plans/state, raw account inventory, original ARNs, registry/checkpoints, credential cache and API logs stay outside the public repository. The [artifact manifest](artifact-manifest.json) binds this candidate evidence with LF-normalized hashes; it is an integrity index, not an independent attestation.
