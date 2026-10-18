# Native AWS acceptance

This procedure describes the **full private HTTPS application** lane. See the
[minimal acceptance assessment](aws-minimal-acceptance.md) for a narrower native
database/PITR lane that does not inherently require ACM, custom DNS, ALB or a VPN.
No account authentication or chargeable operation is requested during offline readiness.

**Current boundary: AWS readiness, not native AWS acceptance.** The current local
regression and infrastructure records are in [aws-readiness](evidence/aws-readiness/README.md).
No AWS credentials were available during this readiness work. No AWS environment
was deployed, restored, stopped or deleted. Historical execution records remain intact.

## Changes made before deployment

- Release validation rejects an overridden container entry point, injected credentials,
  duplicate environment names, inactive task revisions and noninteger replica counts.
- A partial utility launch or failed bounded wait requests termination of only the
  tasks launched by that call. A stop request is not proof of termination: the operator
  must confirm `STOPPED`. Final task identity and exit status are independently read.
- Startup clears inherited credentials for other service identities. Bootstrap checks
  existing secret document shape and database usernames before preserving credentials.
- Restore targets must stay in the source environment. Terraform checks private,
  encrypted, backed-up PostgreSQL configuration on the restored database and gates new
  task registration on the exact environment KMS/subnet/security/TLS/owner-secret binding.
- Release records retain source and image hashes, hashed identifiers and failure state,
  rather than public account ARNs or SDK exception text.
- A bounded, read-only configuration witness checks the authenticated account,
  service revisions/counts, observed image digests, private networking, distinct task
  roles and independent RDS identities. It can collect a fresh actual verifier result
  from its exact task log stream. It cannot authorize resumption or prove PITR.

## Authorization and access prerequisites

The owner must approve the isolated account, `dev` or `stage` environment, region,
four-hour window, resource inventory, spending allowance and teardown disposition in
[the cost review](aws-cost-review.md) **before any chargeable operation**. Approval is
not implied by this runbook or a command-line flag. No production environment is eligible.

Provide a temporary SDK-compatible role/profile through the normal private AWS credential
chain. Do not put credentials in issues, chat, Terraform variables, manifests or commits.
The browser's console login does not authenticate Python or Terraform. Keep the account
number, manifests, raw plans/state and endpoints in an ignored private workspace.

Before provisioning, establish an existing approved ACM certificate for the owned DNS
name and a real private route plus DNS resolution from the acceptance operator to the
ALB. A client CIDR alone creates neither. Check quotas, PostgreSQL version availability,
IAM trust and existing resource collisions with read-only queries. The existing design
does not create a VPN, NAT or public fallback. Missing connectivity is a blocker, not
permission to create additional networking resources. Review the actual saved Terraform
plan before applying it; reject unexpected replacement, production resources or scope.

## Execution sequence after approval

Use the procedures and manifest export in [infra](../infra/README.md) and
[cloud operations](cloud-operations.md). Pin the full source commit and immutable image
digest. Keep previous known-good image/manifest for rollback. Record UTC start/deadline,
the approved private resource inventory and every native action before starting.

1. Apply only the reviewed isolated plan. Infrastructure starts with zero service counts.
   Review actual RDS/endpoint/ALB settings and role policies. Build and push the pinned
   image, bootstrap with the migration task and use the existing release gate to open
   only the approved task revisions. A successful API response alone is insufficient.
2. Collect configuration independently:

   ```bash
   python -m controlplane.aws_acceptance --manifest .local/release.json \
     --output .local/native-01/configuration.json --approved-native-observation
   ```

   The collector has a 40-request ceiling and performs only STS/ECS/RDS/Logs reads.
   `CONFIGURATION_WITNESS_PASSED` is narrower than operational acceptance. The manifest
   must include environment, both database identifiers and the verifier log group from
   Terraform outputs. Its private content is not an evidence artifact.
3. Run a small synthetic API workload through the private HTTPS ingress, using separate
   source/approver/operator identities. Observe the **ECS worker** drain durable intents.
   Check effect IDs, immutable hashes and independent receipts with read-only verifier
   credentials. Run wrong-role/source/approval/consent denials without granting new rights.
   Record responses and durable rejection/audit facts. Never reuse the local `demo.py`
   or `workload.py` worker loop as evidence of ECS execution: those launch local workers.
4. Exercise one lost acknowledgement and the same-key replay. Confirm one remote effect
   and a matching receipt, then revoke current authority and confirm reconciliation of
   that historical receipt does not authorize a new action. Inject only through the
   existing isolated synthetic downstream/operator interface. Bound attempts and wait time.
5. Interrupt one API task, then one worker during an observed durable intent. Confirm
   service replacement and queue recovery without losing accepted work or duplicating
   effects. Preserve task-stop timing, effect counts, queue state and CloudWatch evidence.
   Verify actual alarm state and delivery to the approved notification destination;
   alarm configuration is not proof of delivery. Roll back to the retained source/image
   using the existing release gate and verify actual running task digests again.
6. For PITR, stop admission and quiesce all four services; verify no running one-offs.
   Select an actual RDS restorable timestamp preceding a known synthetic effect. Preserve
   the original control DB and independent downstream DB; restore **only** control to
   a new reviewed identifier. Review the helper's request before `--execute`. Stop if
   the restorable window or remaining budget cannot support the experiment.
7. Bind while closed, run migration then the read-only verifier. A remote-ahead receipt
   must block reopening. Keep the report, original database and independent receipts.
   Do not forge missing intents to manufacture a pass. Select a later valid recovery
   point or return to the preserved original control DB, reverify and reopen only with
   a fresh successful gate. Measure RTO through verified processing, not RDS availability.

For a quiesced verifier observation, use its actual private task ARN:

```bash
python -m controlplane.aws_acceptance --manifest .local/release.json \
  --output .local/native-01/recovery-observation.json --approved-native-observation \
  --quiesced --verifier-task '<actual-task-arn>'
```

The stopped task, exact revision/image, exit zero, exact log stream, single report,
zero findings and timezone-aware snapshot no older than five minutes must agree. This
is a point-in-time observation under exclusive incident ownership, never a saved token
that can reopen processing. IAM policy effectiveness still needs actual denial probes.

## Evidence and stop conditions

Keep raw SDK/task logs and plans private. Publish only reviewed allowlisted results:
commit/image hashes, UTC timings, hashed resource/request references, counts, observed
denials, finding codes, reconciliation hashes and measured recovery/cost observations.
Do not publish tokens, DSNs, account IDs, private hostnames or raw SDK errors. Retain
failed cases alongside eventual successful cases. A passing configuration witness is
never reported as full native acceptance.

Stop further workload creation on unexpected spend, scope drift, permission ambiguity,
unverifiable task termination, inconsistent receipts or deadline exhaustion. Quiescing
ECS does **not** stop RDS, endpoints, ALB or storage charges. Follow the separately
approved teardown in the cost review and verify absence/residuals explicitly. If cleanup
fails, report exact private inventory and ongoing cost exposure; do not claim zero cost.
