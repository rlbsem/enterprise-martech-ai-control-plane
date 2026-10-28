# Native deployment admission abort

This separately approved run was **aborted during partial provisioning**. It did not execute application tasks, upload the application image or perform a restore. It supplies no new application-recovery acceptance. [Recorded outcome](execution.json).

The cleanup guard was deployed and independently armed at 17:29 UTC, with a fixed 20:29 UTC deadline. Runtime approval arrived with approximately 132 minutes remaining. The application's existing start gate requires 150 minutes, but the operator's separate pre-deployment check incorrectly admitted provisioning with a 120-minute threshold. Terraform began creating two RDS instances and isolated resources before the mismatch was noticed and the apply stopped. This was an operator admission error; it does not establish a model, database or recovery-algorithm failure.

The deadline was not extended and the controller's 150-minute requirement was not lowered. No full experiment was restarted. Cleanup of the disposable run is authorized; its independent observations are recorded separately from the abort.

The corrected deployment entry point now checks the **same 150-minute constant plus a 15-minute provisioning allowance before Terraform apply**. A late approval cannot create an experiment that the controller would immediately reject. If the guard's clock leaves insufficient headroom, the gate rejects provisioning and requires guard retirement. The existing controller checks remain independently enforced.

The [complete local regression](../timing-admission-regression-2026-10-28/verification.json) executed 401 tests with zero failures, errors or skips, including four targeted deadline cases. The separate 397-test record and its source binding remain unchanged.

The [earlier native attempt](../native-2026-10-28/README.md) and [failure investigation](../continuation-review-2026-10-28/README.md) remain unchanged. Actual post-restore reconciliation and controlled native resumption are still incomplete. A new attempt requires a fresh disposable run and explicit approval; this record authorizes neither a retry nor a deadline extension.
