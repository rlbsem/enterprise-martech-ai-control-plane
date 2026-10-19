# Minimum secure native acceptance

**Historical assessment.** Subsequent short-lived authentication, approved native RDS/SQL
execution and the Free-plan PITR quota blocker are documented in the
[executed record](evidence/aws-readiness/native-sql/README.md). The prerequisite table below
describes the earlier assessment date, not current account access or execution status.

**Latest region correction:** [Ohio preflight](evidence/aws-readiness/ohio/README.md)
supersedes the account-wide-block interpretation below. AWS documents Canada projects
in `us-east-2`; 29 independent reads there succeeded. The new
[Ohio acceptance proposal](aws-ohio-acceptance.md) separates Single-AZ/PITR from full
production acceptance. Historical denied-call records remain unchanged.

**2026-10-18 update:** AWS Core now provides a real authenticated read-only connection.
Account and billing checks succeeded, while resource discovery encountered explicit
organization-policy denials. See the [sanitized native account preflight](evidence/aws-readiness/account-preflight/README.md).
The observations below describe the earlier local-only assessment; they are not a claim
that no AWS account exists. No resource creation or Paid-plan upgrade is authorized.

Native deployment and PITR remain the next milestone. They have **not executed**.
This assessment changes no production defaults, service topology, TLS settings or IAM
permissions. No account setup, authentication or chargeable operation was performed.

## What is known, and what is not

| Prerequisite | Observed state | Needed for which proof? |
|---|---|---|
| AWS CLI | Not available on the current command path | Convenient authentication; not a warehouse/runtime dependency |
| SDK credentials/profile | No configured profile or resolved credentials in this workspace | All native SDK/Terraform execution |
| Browser console session | Console navigation reached AWS sign-in | Cannot inspect account resources from this session |
| AWS account itself | Owner believes no account is configured; existence not independently established | Native execution |
| Certificate, DNS domain, existing VPC/routes, SNS, backend, quotas | **Unknown, not confirmed absent**; no authenticated account inventory was possible | Depends on acceptance lane |
| Local implementation | Executed local PostgreSQL/HTTP tests and mocked SDK/infrastructure checks | Readiness only |

No credential request is pending from this assessment. Account creation/sign-in, if
needed later, is an owner action; it is not accomplished by this repository.

## ACM, DNS and VPN are not universal prerequisites

**Database/PITR lane:** two independent RDS databases, a temporary in-VPC ECS
bootstrap/verifier task and its private image/log/secret access can establish native
PostgreSQL execution, a real control-only PITR, remote-ahead refusal and independent
receipt reconciliation. Native RDS hostnames and the checked-in public RDS CA bundle
provide database identity/TLS. No custom ACM certificate, custom DNS zone, ALB, public
domain or workstation-to-VPC VPN is inherently needed for this narrower experiment.
The operator can collect results through authorized AWS APIs and CloudWatch.

This is a **proposed scoped lane, not an implemented separate deployment root or a
passing full application deployment**. The current Terraform root provisions the whole
application. A reviewed partial acceptance plan and task permissions must be prepared
and tested after the account inventory is known; do not apply the whole root and claim
those resources were omitted. SQL/PITR proof alone does not establish native HTTP
delivery, ECS worker interruptions, releases, alarm delivery or full operating acceptance.

**Full application lane:** the current implementation uses a private HTTPS ALB and
`api`/`effects` DNS names. It therefore does need a trusted certificate and private name
resolution. An ACM **public** certificate with public DNS validation can serve a private
ALB; a paid ACM Private CA is unnecessary. Existing owned DNS is preferable. If none
exists, imported private-PKI certificates plus an explicitly trusted root and private
test DNS are possible, but require a reviewed trust-store implementation; do not disable
TLS verification or claim that variant has been tested.

A VPN is not compulsory even here. An ephemeral acceptance client inside the VPC can
exercise the private HTTPS API and inspect the results through AWS APIs. It needs a
dedicated scoped identity and an allowlisted task definition; it must not run an owner
database login or a local worker and label that ECS-worker evidence. This avoids a new
VPN/NAT/public ingress. That client is additional tooling, not something already executed.

Multi-AZ is needed to demonstrate managed failover, **not for PITR itself**. A separately
approved Single-AZ database-only experiment could cost less but must be labeled as
PITR/ledger acceptance, excluding Multi-AZ availability proof. Existing Multi-AZ defaults
remain unchanged. GitHub OIDC is optional for a locally operated experiment; it becomes
necessary if GitHub is selected as the short-lived cleanup/deployment runner.

References: [ACM public certificates](https://docs.aws.amazon.com/acm/latest/userguide/acm-public-certificates.html),
[RDS PITR](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.html).

## Later authentication, without sharing keys

When native work is explicitly resumed, prefer an existing IAM Identity Center profile.
If none exists, AWS CLI v2.32+ supports browser-based `aws login --profile control-readiness`.
Use a non-root identity with the appropriate sign-in policy; authentication does not grant
infrastructure authority or spending approval. Do not create long-lived access keys.

The repository pins an older SDK/provider. Use AWS's documented process-credential bridge
rather than assuming those versions understand a newer login cache:

```ini
[profile control-sdk]
region = ca-central-1
credential_process = aws configure export-credentials --profile control-readiness --format process
```

This belongs in the owner's private AWS config, never the repository. The owner would
share only the profile name/config path. First perform read-only STS identity and regional
inventory for ACM, DNS, VPC/network paths, RDS engine versions, quotas, backend and SNS;
then produce the final account-specific plan and cost review. No mutation follows merely
from signing in. See [AWS CLI login documentation](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html).

## Automatic teardown and independent checks

The new `controlplane.teardown` tool validates a saved destroy plan against an exact,
private owner-approved resource inventory. It rejects updates, replacements, unknown or
duplicate resources, wrong IDs/types, missing deletions, wrong environment/region and
unapproved data disposition. CLI execution checks inventory/plan SHA-256, expiry and
actual STS account identity. It then applies the reviewed saved plan with a one-hour
process timeout and records sanitized status. Provider diagnostics stay private.

**It is not a provisioned watchdog or a hard billing cap.** A timeout can leave remote
operations in progress. The current destroy protections must first be changed in a
reviewed isolated teardown configuration; the tool does not silently remove them.
PITR instances must be imported into that cleanup state and explicitly inventoried.

The recommended unattended mechanism is a one-shot cloud-side scheduled cleanup runner,
armed and verified **before the first billable deployment**, outside the resources it
destroys. It should start at hour three, leaving an hour for teardown, use a separate
temporary cleanup role, fetch a checksum-bound private configuration and remote state,
generate the final destroy plan and validate its exact IDs against the approved inventory
before executing. Preserve its raw logs privately. Follow it with a distinct read-only
inventory job; retry only bounded known transient failures and notify on residuals.
An EventBridge Scheduler → CodeBuild runner is one viable option; a separately armed
GitHub OIDC runner is another if repository/account controls permit it. Neither is yet
provisioned or natively validated. A local sleeping process or Terraform destroy in a
test harness `finally` block is insufficient protection against a laptop/process failure.

Provisioning the watchdog, its narrowly scoped role, private bundle/state and notification
destination is part of the **next approved native plan**, not an existing capability.
Until that plan is executable and the watchdog is independently observed armed, native
deployment must remain blocked. Do not claim unattended cleanup is already operational.

`controlplane.cleanup_check` independently reads exact RDS, ALB, ECR, VPC-endpoint and
log-group identities. Only explicit not-found responses/complete empty inventories
establish absence; AccessDenied, throttling and truncated results do not. Other resource
types are reported unsupported and prevent an all-absent result. It does not read Terraform
state or call delete APIs. Run it against the same private account-pinned inventory:

```bash
python -m controlplane.cleanup_check --inventory .local/approved-inventory.json \
  --record .local/cleanup-observation.json
```

This initial checker deliberately does not claim account-wide cleanup. Snapshots,
automated backups, secrets, keys, metrics/alarms, backend versions, ENIs, retained logs,
and untracked resources still need account inventory and service-specific checks in the
native plan. Billing must also be checked after its reporting delay. Scheduled deletion
and retained storage must be disclosed, not converted into a zero-cost claim.
