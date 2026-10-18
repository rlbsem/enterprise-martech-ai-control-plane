# Native RDS restore-window correction

The executed SQL fixture reconciled one original intent with one independently committed
receipt. Its duplicate returned the same receipt and one effect. The first recovery
controller then waited on an invalid response-field assumption; it had not sent a restore.

[Actual RDS metadata](actual-window.json) shows that the earliest bound comes from
[automated-backup RestoreWindow](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_RestoreWindow.html),
not `DBInstance.EarliestRestorableTime`. The corrected reader pins both database name and
physical resource ID, requires an active automated backup and aware timestamps, and uses
the intersection with `DBInstance.LatestRestorableTime`. Denied, missing or mismatched
metadata cannot be substituted with an invented restore point.

[237 local tests](local/verification.json) include the real botocore model contract,
physical identity mismatch, ambiguous/missing windows and denied reads. Existing
[infrastructure checks](../free-retention/infrastructure/verification.json) remain bound
to unchanged Terraform.

The continuation re-reads the original seven stopped task identities, immutable image
digests and CloudWatch result bodies; it preserves the actual pre-intent SQL timestamp.
It performs a fresh 1/1 reconciliation and creates no new baseline, fixture or receipt.
This record alone does not claim PITR completion. The container release and host-controller
revision are tracked separately because this correction changes only the host controller.
