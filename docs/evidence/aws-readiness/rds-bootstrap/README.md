# RDS bootstrap privilege correction

[Actual native failure and safe diagnostic](native-bootstrap-failure.json) record a stopped
bootstrap task and a bounded diagnostic retry. The diagnostic reported SQLSTATE `42501`
(`InsufficientPrivilege`) at the role password/attribute statement. No synthetic intent
was generated. RDS creation and native container execution do not constitute SQL/PITR acceptance.

Managed RDS owners are [not PostgreSQL superusers](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Roles.rds_superuser.html). The helper no longer issues superuser-only
attribute changes. It reads effective role attributes and memberships, refuses administrative
or inheriting targets, then sets the password through an ordinary permitted operation.
It does not grant more owner authority or silently demote unsafe existing roles.

[Local tests](local/verification.json) run real PostgreSQL with a nonsuperuser `CREATEROLE`
identity: the old attribute setter is denied; the corrected helper creates an ordinary login.
Tests also reject each privileged flag and inherited membership. Existing secret reuse,
production-default database binding and application SQL/HTTP regressions remain checked.
[Infrastructure checks](../free-retention/infrastructure/verification.json) remain valid because
this correction changes no Terraform infrastructure.

Native tasks now emit only error types, codes and source locations. They exclude exception
messages, SQL, locals and credentials; a regression test uses a secret-bearing exception.
A corrected native retry is required before any acceptance pass is claimed.
