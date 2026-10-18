# IAM source-analysis review

IAM Policy Autopilot 0.3.0 was executed against the native Python sources and runtime
Terraform using the documentation-only account `123456789012` and Ohio region. The
checked JSON is the tool's untrusted starting baseline, **not a deployed grant**.

The final authored policies are in `infra/acceptance-guard/main.tf` and
`infra/acceptance-runtime/main.tf`. They separate five SQL task identities, image/log
execution, cleanup mutations, independent read-only checking and Scheduler invocation.
The checker and watchdog cannot retrieve secret values. Only bootstrap retrieves
owner secrets; fixture writers retrieve their own runtime secret; verifier retrieves
only its two-reader credential document. Secret identifiers are not secret values.

The source analyzer over-approximates some operations, cannot fully infer the dynamic
inventory dispatcher, and cannot prove effective SCP/Free-plan authorization. Do not
copy its wildcard IAM, PassRole or unrelated operations into deployment roles. Inventory
requires account-wide metadata reads for list/discovery actions; these cannot turn a
denial into an empty result. The watchdog's mutations use exact runtime namespace ARNs
or the unique AcceptanceRun tag, never production roles or a full-access managed policy.
EC2 mutations are tag constrained. Untagged requester-managed interfaces are observed
through the owned VPC and allowed to disappear through their owning service, not given
unrestricted EC2 deletion authority. RDS owns its managed secret deletion.

The automated-backup deletion grant is run-tag constrained. Native execution must verify
that the service propagates/enforces the expected ownership tags and permissions; a
retained backup that cannot be deleted remains a reported residual and blocks cleanup
completion. Likewise, successful local policy parsing does not establish tag-condition
support for every service-generated dependency. Denied mutations are recorded and
retried within the bounded schedule, then escalated as incomplete cleanup.

Terraform mocks assert the independent reader lacks destructive/secret-read actions.
Local SQL tests exercise actual restricted-login write denial; controller tests check
account/plan, schedule identity/deadline/hash and independent reader role. Native role
permission checks and cleanup execution remain outstanding. The operator must confirm
actual scoped policies/permissions before creating runtime resources, and must not
upgrade or broaden project-managed roles to bypass a denied service.
