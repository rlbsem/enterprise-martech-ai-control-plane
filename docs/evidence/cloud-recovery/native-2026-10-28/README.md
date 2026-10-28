# Native application recovery attempt — October 28, 2026

**Application acceptance failed.** Real API, worker and independent receiver processes established five synthetic effects on private AWS RDS PostgreSQL. Retained-backup PITR then restored the earlier control database and reached `available`. The next application task failed the controller's stopped-task acceptance check. No accepted post-restore comparison or safely resumed application was recorded. The second restore was not attempted.

## Executed observations

| Stage | Recorded result |
|---|---|
| Source application | Three intents/effects, including a lost acknowledgment and an actually terminated worker; two later effects produced five intents/effects. |
| Independent source verifier | Five intents, five effects, matching receipt fingerprint, open gate and two actual SQL write denials. |
| Recovery points | Two quiescent whole-second timestamps verified inside the actual backup window; original task timestamps retained separately. |
| Source deletion | Disposable control deleted only after backup, snapshot, encryption, tag, identity and durable checkpoint checks. Independent receipt instance preserved during recovery. |
| Retained-backup restore | Actual `RestoreDBInstanceToPointInTime` request succeeded. Earlier point: `2026-10-28T02:10:42Z`; restored instance reached `available` after **773.118 seconds**. This measures database availability, not application RTO. |
| Post-restore task | Rejected with `Stopped task identity/image/exit mismatch`. The old controller did not retain the failing task's final metadata or logs before raising. Its precise container failure cause is **not established**. |
| Remaining acceptance | No independent restored-state `remote_ahead_missing_intent` result, matching second restore, fresh native resume approval or six-effect post-resume comparison was recorded. Local results do not fill these gaps. |

The fixed execution window was not extended. No new workload or restore was launched after it expired. This lane never requested a third concurrent RDS instance. HTTP processes ran inside bounded one-off Fargate tasks, with distinct SQL identities and TLS to independent databases. Separate production ECS services, HTTPS ingress, Multi-AZ recovery and production reliability were not accepted.

## Inspect the actual records

- [Sanitized execution result](execution.json): original failure, all four accepted task results and exact source/image bindings.
- [Checkpoint excerpts](checkpoints.json): original artifact hashes, selected times, source reconciliation, actual restore request ID and elapsed availability time.
- [SDK request receipts](aws-request-receipts.json): actual operation names, request IDs, response codes and timestamps; includes one successful PITR request.
- [Verified CI image](image-verification.json) and [exact ECR readback](image-upload.json).
- [Independent guard arming](guard-arming.json).
- [Independent teardown and credit observation](teardown.json): active resources absent, pending deletions and delayed reporting kept separate.

The application image came from source [`413f8d9`](https://github.com/rlbsem/enterprise-martech-ai-control-plane/tree/413f8d9a3559bfcddf903985ff0166071871e865). The operator controller used [`58fc983`](https://github.com/rlbsem/enterprise-martech-ai-control-plane/tree/58fc9837e3fd05e0d0fab2a1029b31d56c842e4d), whose only differences were the quiescent-point selector and its tests. The API, worker, receiver, schemas, task definitions and dependencies were unchanged. These are distinct source versions, not one falsely relabeled revision.

Raw private observations remain private. Account IDs, ARNs, database endpoints and physical resource IDs are redacted or hashed in public excerpts. Counts, verdicts, elapsed measurements and request IDs are retained. Original October 19 evidence is unchanged.

## Cleanup and diagnostic follow-up

The scheduled guard removed the databases, retained backups, compute, networking, image repository and runtime roles. Five task definitions remained active; an independently scoped checker exposed them. Authorized operator cleanup deregistered and requested deletion of those exact definitions. Sixty guard failure messages were preserved; the old cleanup handler recorded failed resource kinds without service error codes, so the reason for its task-definition failures is not established.

Before retirement, **739 S3 versions, two deletion markers and all 60 failure messages** were archived privately. Every downloaded version was reopened and hashed; the exact frozen version set was checked again before cloud deletion. The guard's functions, schedule, queue, roles, alarm, logs and bucket were removed. Fresh AWS reads separately established active runtime and guard absence. Two encryption keys, six secrets and five deleting task definitions remained pending; this is not complete deletion or billing clearance.

AWS reported **FREE/ACTIVE, USD139.52 credits** after cleanup, versus USD139.74 earlier. The USD0.22 change is a delayed account-level observation, not an attributed or final experiment bill. The original USD11.2868 estimate and USD20 planning ceiling were not hard billing caps. Free Tier usage returned no records at this observation.

The subsequent code correction archives stopped-task diagnostics before acceptance checks, requires private readback, archives runtime logs before scheduled deletion and retains cleanup service codes/request IDs without exception messages. Those corrections require their own local/CI verification; they have **not** completed another native experiment. A further native attempt requires a fresh approved execution window and plans. No automatic follow-up or new cloud deployment is scheduled.

[Recovery evidence index](../README.md) · [Recovery design and reproduction](../../../cloud-recovery.md)
