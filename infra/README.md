# AWS deployment boundary

Two Terraform roots keep durable foundation ownership separate from application releases. `foundation` creates encrypted/versioned remote state and the account OIDC provider. `aws` creates the private runtime. No resources are created by the validation commands or ordinary push/PR workflows.

## Prerequisites and ownership

- An authorized infrastructure administrator with short-lived credentials for the target account. The restricted GitHub **release** role intentionally cannot create IAM, networks, databases or Terraform state. Infrastructure changes require that administrator's reviewed Terraform plan/apply.
- A dedicated environment/account, non-overlapping VPC CIDR and routed enterprise connectivity (VPN, peering or transit gateway). Route attachment and corporate DNS forwarding belong to the enterprise network owner; supplying a client CIDR alone does not establish a route.
- An existing ACM certificate in `ca-central-1` covering `api.<internal_domain>` and `effects.<internal_domain>`, trusted by the runtime's public CA bundle. Public ACM DNS validation can be performed in the enterprise-owned public zone while application records remain private. Private-PKI use requires adding its approved root to the image and configuring the HTTP client trust store; do not disable certificate verification.
- An existing SNS on-call topic with confirmed subscriptions, budget ownership, and an approved AWS PostgreSQL 17 engine patch/class combination for the region.
- GitHub environment `aws-release`, restricted to `main`, with required independent reviewers and prevention of self-review where the account supports it. Verify the actual repository OIDC subject (including any customized repository-ID claims); pass that exact subject to Terraform. Wildcards and other repositories are rejected.

## Foundation and remote state

Run these commands only in an authorized AWS environment. Local validation uses `init -backend=false` instead.

```bash
terraform -chdir=infra/foundation init
terraform -chdir=infra/foundation plan -var='state_bucket_name=control-tfstate-<unique-environment>' -out=foundation.tfplan
terraform -chdir=infra/foundation apply foundation.tfplan
terraform -chdir=infra/aws init \
  -backend-config='bucket=<foundation-output>' \
  -backend-config='key=stage/control/terraform.tfstate' \
  -backend-config='region=ca-central-1' \
  -backend-config='encrypt=true' \
  -backend-config='use_lockfile=true'
```

Protect the initial foundation state in the administrator's encrypted state system; it contains infrastructure metadata, not secret values. If the account already has the GitHub OIDC provider, import it into the foundation or use its existing ARN; do not attempt a second provider with the same URL. State bucket access is restricted to the foundation/deployment administrators. Grant Get/List/Put on the exact environment state object and Get/Put/Delete on its `.tflock` object, plus the bucket's KMS permissions; do not give the runtime or release role state access. Keep backend configuration free of credentials.

Provide environment values through a private `*.tfvars` file or `TF_VAR_*`. Required inputs are documented in [variables.tf](aws/variables.tf). No sample account ID or ARN in test fixtures represents a deployment.

## Image and first deployment

1. Build and test the exact source commit. The Docker image contains the locked Python runtime, the public RDS CA bundle, and no Node documentation tooling. Build a Linux AMD64 image; task definitions target the Fargate default x86_64 platform.
2. On first setup only, the infrastructure administrator can create the KMS/ECR dependency graph with `terraform apply -target=aws_ecr_repository.application` using reviewed environment inputs. A digest input is required by the root but no service is created by this target. Use the actual digest from a local OCI build or a prior tested image, not an invented deployment record. Subsequent releases use the existing repository.
3. Authenticate Docker to the repository with a short-lived image-publisher identity. Push an immutable tag equal to the full source commit. Record the registry-returned SHA-256 digest, scan the image, and set `image_digest` and `source_commit` to that verified pair. Publishing rights (ECR upload/PutImage/GetAuthorizationToken) are held by the build administrator, separate from the runtime release role.
4. Review `terraform plan -out=release.tfplan`; apply that saved plan. Services start with **zero** replicas. Infrastructure applies ignore service task/count changes so they cannot accidentally reopen a quarantined environment.
5. Export `terraform output -json release_manifest > release.json`. This is non-secret configuration containing exact task revisions, image digest, network IDs and database identities. Retain it with the reviewed plan and commit.
6. Run `python -m controlplane.release deploy --manifest release.json --record work/release-record.json`, or the protected manual workflow. It drains the cluster, runs the bootstrap/migration task, compares state, starts services in order and checks exact task revisions. It never treats an ECS rollback to another revision as successful deployment.

For example, after the repository exists:

```bash
aws ecr get-login-password --region ca-central-1 | docker login --username AWS --password-stdin <registry>
docker build --platform linux/amd64 --label org.opencontainers.image.revision=<commit> -t <repository>:<commit> .
docker push <repository>:<commit>
aws ecr describe-images --repository-name control-stage --image-ids imageTag=<commit> --query 'imageDetails[0].imageDigest'
```

Do not use these commands in PR checks. Image signing/attestation can be layered onto this digest/source binding under an organization's artifact trust policy; a source label alone is not a cryptographic attestation.

## Runtime authorities

| Principal | Permitted authority | Excluded authority |
|---|---|---|
| ECS execution role | Pull this ECR repository; write these log streams | Read application secrets; business database access |
| API task | Read API secret; decrypt through Secrets Manager; SQL state/proposal/audit grants | Executor/injector tokens; downstream SQL |
| Worker task | Read worker secret; SQL lease/completion and customer row-lock grants; executor token | Source field writes, proposals insertion, downstream database |
| Downstream task | Read downstream secret; effects/plans SQL grants | Control SQL and API role credentials |
| Observer | Read observer secret and queue table only | Customer payloads, downstream database, queue writes |
| Verifier | Read verifier secret; read-only control tables and downstream receipts | Requeue, receipt deletion, effect dispatch |
| Migration task | Read managed database owner secrets; create runtime secret versions; SQL role/schema ownership | Long-running public service |
| GitHub release | Run approved task families, update named services, inspect task state, pass only project roles to ECS | Register arbitrary task definitions, push images, IAM/network/RDS administration, state or secret values |

Wildcard resources are limited to APIs that require them (`ecr:GetAuthorizationToken`, task description/list operations) and bounded task-family revisions for rollback. Task mutation calls are cluster constrained. KMS decryption is restricted to Secrets Manager's service path. ECS trust is source-account/source-ARN constrained. Bootstrap is deliberately powerful and protected; its role never becomes an application login.

Private task networking has no internet default route. Four interface endpoints provide ECR API/Docker, Logs and Secrets Manager; the S3 gateway permits ECR layer downloads only. TLS terminates at the internal ALB; the ALB-to-task hop is HTTP on port 8000 with security-group isolation. Database clients verify the RDS hostname and CA. The downstream is reachable through its private HTTPS host and requires its own token for every business operation.

## Recovery, rotation and teardown

Use the [operating runbook](../docs/cloud-operations.md) for immutable rollback, credential rotation, PITR and remote-ahead recovery. The restore helper uses a separate operator identity; the GitHub release role has no RDS restore authority. Restored databases are explicitly bound through `restored_control_identifier`, preserving the original instance for investigation.

Database deletion protection, final snapshots and Terraform `prevent_destroy` protect durable stores. A teardown is a retention operation: drain, export necessary evidence, select snapshot identifiers, obtain the data owner's retention decision, then deliberately change protection controls in a reviewed plan. Keep audit/effect retention consistent; dropping receipts before the replay horizon would invalidate idempotency guarantees.

## Validation

Terraform 1.13.5 and AWS provider 6.15.0 are pinned; lock files include provider package checksums. Run `python scripts/validate_infra.py` from the root. It performs formatting, initialization without a backend, validation of both roots, mocked-provider assertions and Trivy HIGH/CRITICAL configuration checks. The mock tests reject mutable images and untrusted OIDC subjects and verify private/encrypted/quiescent defaults. Static checks cannot establish account quotas, certificate coverage, corporate routing, RDS engine availability or observed AWS recovery time; those belong to environment acceptance.

Primary references: [GitHub OIDC on AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws), [ECR private endpoints](https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html), [RDS PITR](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.html).
