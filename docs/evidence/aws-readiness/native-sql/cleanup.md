# Teardown evidence and limits

The approved operator SDK cleanup removed the isolated RDS databases, automated backups,
network endpoints, VPC/subnets/security groups, images, runtime roles and logs. It did not
remove or alter the pre-existing default VPC. No production application service was started.

The first reader incorrectly treated an ECS namespace as a full task family and missed
ten task-definition revisions. Its initial absence observation is superseded. The corrected
reader queried each of the five exact families in ACTIVE, INACTIVE and DELETE_IN_PROGRESS
states. The operator then deregistered and submitted the remaining revisions for deletion.
This correction was applied to the repository and operator tooling, not retrospectively
attributed to the deployed guard bundle.

[The final independent read](teardown.json) checks RDS instances, manual snapshots,
automated backups, ECS cluster/tasks/revisions, ECR, runtime and managed-owner secret metadata,
customer-managed keys, networking, roles, database configuration groups and logs. It also
checks the guard's schedule/functions/roles/queue/alarm/key/logs and versioned S3 bucket.
An unavailable or denied API read does not establish absence.

`active_resources_absent` excludes seven-day pending keys/secrets and deleting task-definition
metadata only when no cluster or task remains. It does **not** mean `all_resources_absent`.
[AWS documents](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_DeleteTaskDefinitions.html)
that a DELETE_IN_PROGRESS definition cannot launch new tasks, while an existing service can
continue running; the remaining-compute check is therefore required. Residual definitions
remain listed until the service finishes deletion.

[Pending-deletion dates](pending-deletion.json) come from fresh KMS metadata and the actual
[CloudTrail DeleteSecret responses](secret-deletion-receipts.json), without retrieving secret
values. The five secret deletion receipts establish seven-day recovery windows and no forced
deletion. Raw `DescribeSecret.DeletedDate` is retained separately from each DeleteSecret
response's future permanent-deletion eligibility date. The key dates include the runtime and guard keys. The guard
key's pending deletion means `guard_resources_absent` can remain false after its executable
resources and evidence bucket are removed. Delayed deletion and billing review remain explicit.

## Guard observations

[Actual Lambda observations](guard-observations.json) preserve invocation counts and billed
milliseconds, including invocations after the fixed cleanup deadline. Invocation timing alone
is not caller attribution, and does not prove successful deletion under the watchdog role.
The substantial runtime teardown was performed by the authorized operator SDK before the
deadline; that authority is distinct from the watchdog's restricted role. The original guard
reader shared the task-family discovery defect described above.

[Guard retirement](guard-retirement.json) records retirement after corrected independent
runtime discovery, private archival/read-back of versioned evidence and runtime state, and
removal of the guard's executable resources. Raw plans, state, object versions, credentials
and account identifiers remain private. Shared AWS service-linked roles are preserved;
their presence is not represented as residual running acceptance infrastructure.

This package establishes observed resource states, not a zero-dollar bill, credit settlement,
scheduled-cleanup mutation coverage, or guaranteed disappearance of pending metadata.
An owner can reproduce the independent reader with the preserved private manifest and exact
registry after the stated deletion dates; fresh delayed billing review remains necessary.
