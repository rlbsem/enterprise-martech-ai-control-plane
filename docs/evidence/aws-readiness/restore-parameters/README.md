# PostgreSQL PITR request correction

The first actual RDS point-in-time restore request was rejected with HTTP 400,
`InvalidParameterValue`, request ID `6a5b7e5d-c2fb-4386-a87f-3c93fd47b124`.
It included `ManageMasterUserPassword`, which the
[RDS restore API](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_RestoreDBInstanceToPointInTime.html)
supports at restore time for Oracle, not PostgreSQL. No restored instance was created
by that rejected request. This rejection is preserved separately from successful execution.

The host controller now omits that flag and never supplies or retrieves a master
password for the disposable restore. Verification uses the pre-existing read-only
application roles copied by PostgreSQL PITR. The two source databases retain their
RDS-managed owner credentials; this change grants no additional role permissions.

[244 executed local tests](local/verification.json) include the corrected request
contract: neither `ManageMasterUserPassword` nor `MasterUserPassword` is sent.
The original SQL baseline, intent and independent receipt are retained. Retrying the
rejected request does not regenerate synthetic history or manufacture a restore point.
This record alone does not claim that PITR succeeded.

The suite also checks fail-closed quota/access rejection and exact-family ECS inventory,
including `DELETE_IN_PROGRESS` revisions. AWS's `ListTaskDefinitions.familyPrefix` takes
a [full family name](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_ListTaskDefinitions.html),
not a namespace prefix. The corrected reader queries all five approved families and rejects
a foreign-family response. Pending task-definition deletion remains visible until AWS removes it.
It can count as non-running metadata only when no cluster or task remains; tests explicitly
reject that classification when either compute resource is still present.
