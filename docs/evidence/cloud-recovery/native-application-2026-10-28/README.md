# Native application recovery acceptance

**Actual AWS acceptance passed in us-east-2 on October 28, 2026.** Two sequential retained-backup RDS restores demonstrate why a restored database must reconcile with independent business effects before execution can reopen. All customer records, permissions changes and effects are synthetic.

## Executed recovery sequence

Two point-in-time restores, two deliberate decisions. The first **correctly blocked unsafe execution**; the second resumed only after fresh verification.

```mermaid
%%{init: {"theme":"base","themeVariables":{"background":"#10101D","primaryColor":"#2A2A3D","primaryTextColor":"#F0F0FF","primaryBorderColor":"#7C7CAF","lineColor":"#B8B8FF","textColor":"#F0F0FF","secondaryColor":"#181825","tertiaryColor":"#2E2E45","fontFamily":"IBM Plex Sans, Arial, sans-serif","fontSize":"15px","edgeLabelBackground":"#10101D"}}}%%
flowchart TB
  subgraph First["RESTORE 01  |  SAFETY CHECK"]
    direction LR
    Early["AWS RDS PITR #1<br/>3 restored intents"] --> CompareEarly{"5 independent receipts<br/>Do they match?"}
    CompareEarly -->|No| Block["EXPECTED BLOCK<br/>No unsafe replay"]
  end
  subgraph Second["RESTORE 02  |  VERIFIED RESUMPTION"]
    direction LR
    Later["AWS RDS PITR #2<br/>5 intents / 5 receipts"] --> Proof["Fresh proof + approval<br/>Consent and worker checks"]
    Proof --> Final["SAFE RESUMPTION<br/>6 intents / 6 effects<br/>Zero duplicates"]
  end
  First --> Second
  classDef stage fill:#2A2A3D,stroke:#B8B8FF,color:#F0F0FF,stroke-width:1.6px
  classDef decision fill:#2E2E45,stroke:#E3BC7F,color:#F0F0FF,stroke-width:2px
  classDef hold fill:#30252E,stroke:#D98EA3,color:#F0F0FF,stroke-width:2.5px
  classDef release fill:#20352E,stroke:#81C5B0,color:#F0F0FF,stroke-width:2.5px
  class Early,Later,Proof stage
  class CompareEarly decision
  class Block hold
  class Final release
```

*Both restores used distinct control generations and the same independent receipt database, with no more than two RDS instances concurrently. Native AWS RDS recovery used synthetic one-off Fargate application tasks; this does not establish full production ECS/HTTPS/Multi-AZ acceptance. Detailed evidence follows.*

| Executed stage | Observed result |
|---|---|
| Source permissions preflight | Real coordinator operations rolled back; empty ledgers unchanged; API/receiver health and empty worker poll passed; two reader writes denied |
| Source application | Three intents/effects, including lost acknowledgment and a worker terminated after receiver commit; later advanced to five matching intents/effects |
| Earlier recovery point | `20:18:23 UTC`; restore reached available after **850.679 seconds** |
| Blocked application recovery | Three restored intents versus five unchanged receipts; two `remote_ahead_missing_intent` findings; worker claimed no work |
| Independent blocked comparison | Separate read-only task confirmed 3/5, closed execution gate and `safe_to_resume=false`; expected refusal exit code 2 |
| Later recovery point | `20:23:32 UTC`; distinct restored generation reached available after **796.774 seconds** |
| Governed resumption | Stale approval rejected after an owner-source update; fresh exact proof approved; stale worker completion rejected; revoked-consent marketing proposal denied |
| Independent final comparison | **Six intents, six effects, zero findings, `safe_to_resume=true`**; two reader writes denied; zero duplicated effects |

The [executed result](execution.json) preserves the actual task results and their separate launch/stopped observations. [Checkpoints](checkpoints.json) bind both service restore windows, requests and application comparisons. [API receipts](aws-request-receipts.json) include both successful `RestoreDBInstanceToPointInTime` request IDs. These are captured observations, not simulated AWS responses.

## Identity, authority and source bindings

The original control is deleted only after verified backup windows, encryption, ownership, quiescence and durable readbacks. The first restored control is then deleted before the second restore. The independent receipt instance remains unchanged. [Hashed database generations](database-generations.json) identify three sequential control generations and one receipt generation; independent observations confirm two concurrent instances. Four identities over the experiment do not mean four concurrent instances.

Nine one-off Fargate tasks executed actual API, worker and receiver processes against private TLS-verified PostgreSQL. Bootstrap alone receives owner-secret access. The final comparison runs separately with read-only database credentials. The finite synthetic driver exercises recovery-operator approval; this does not claim a staffed human approval process.

Application image and controller source are both pinned to [`3d62df2d750d24dc522e6d5f2e756985edcfc38e`](https://github.com/rlbsem/enterprise-martech-ai-control-plane/tree/3d62df2d750d24dc522e6d5f2e756985edcfc38e). [Image verification](image-verification.json) records every verified OCI blob; [upload verification](image-upload.json) records exact registry manifest readback. The actual controller elapsed **2,937.260 seconds**, including backup-window waits and both restores. These finite measurements establish no general production RTO/RPO or reliability guarantee.

## Cleanup and scope

The [guard arming record](guard-arming.json) precedes runtime provisioning. The three-hour execution deadline was fixed, with one additional cleanup hour. The [combined cost allowance](cost-envelope.json) includes the aborted deployment and earlier delayed-reporting reserves; it is a planning estimate, not a hard billing cap or measured bill. The account remains on the Free plan; no Paid upgrade, billing change or real prospect communication occurred.

The [independent teardown observation](teardown.json) confirms no active runtime or guard infrastructure. Both remaining databases, retained backups, images, cluster, networking, roles, logs, schedule, functions and evidence bucket were removed. All **73 private object versions** were reopened and rehashed before guard retirement. Nine complete task log streams were archived with exact cloud readback.

At that observation, **six secrets, two customer keys and five task-definition deletion records** remained pending. The two key deletions are scheduled for November 4, 2026. Pending deletion is not permanent deletion or billing clearance; non-chargeable usage reporting returned no rows and does not establish zero cost. Operator SDK teardown completed before the scheduled deadline, so scheduled watchdog mutation is not claimed as executed. The single completed experiment will not be repeated automatically. No daily experiment or recurring account monitor is configured.

This reduced lane uses independent RDS stores and process boundaries inside finite one-off tasks. It does **not** establish full production ECS-service/HTTPS/Multi-AZ acceptance, resilience to an untrusted infrastructure administrator, or exactly-once behavior for arbitrary external vendors.

The [earlier failed application attempt](../native-2026-10-28/README.md), its unresolved task cause, and the [separate admission abort](../native-admission-2026-10-28/README.md) retain their original records. This new run supplies its own source binding and verdict; it does not relabel either prior outcome.

[Recovery architecture](../../../cloud-recovery.md) · [Evidence index](../README.md)
