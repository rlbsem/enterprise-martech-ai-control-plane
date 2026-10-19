# Free-plan retention correction

The first actual database creates were rejected with `FreeTierRestrictionError`, not an
OAuth, regional or general RDS authorization failure. [Executed rejection evidence](initial-retention-denial.json)
retains both request IDs. No database or SQL task was created by those requests; the
already-created network and supporting resources remain tracked for cleanup.

Only the isolated native acceptance lane changes to one day of automated backups.
Zero retention remains rejected. Production retention and the actual timestamp-based
PITR, independent receipts and remote-ahead fail-closed assertions are unchanged.
One day is the minimum nonzero [RDS API backup retention](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.BackupRetention.html);
the corrected requests were subsequently accepted and an independent read observed two instances provisioning with one-day retention. See [actual provider corrections](provider-corrections.json). This is not SQL/PITR acceptance.

[Local regressions](local/verification.json) and [infrastructure/security checks](infrastructure/verification.json)
are distinct from AWS acceptance. Historical evidence is preserved.
