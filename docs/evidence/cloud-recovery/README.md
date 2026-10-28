# Cloud recovery acceptance

This package separates new recovery verification from historical AWS observations.

The new local harness has executed all eight recovery scenarios against real PostgreSQL databases and independent HTTP/worker processes. Its full regression suite passed 356 tests. Exact code-bound run records and native execution observations are attached as separate dated records; infrastructure availability is never substituted for application acceptance.

[Implementation and reproduction](../../cloud-recovery.md) · [Original native outcome](../aws-readiness/retained-two-instance/native-outcome/README.md) · [Prior runtime source bindings](prior-runtime/README.md)

The new recovery gate changes source code previously referenced by immutable workload records. Five exact former source files are retained at their original revision, without rewriting the workload observations, source maps or hashes. Earlier workload timings do not measure the new gate. New verification must identify its own source revision and file hashes.

Native AWS application recovery remains pending execution. The connected AWS account has been checked read-only; no new infrastructure is implied by this evidence entry point.
