mock_provider "aws" {
  override_during = plan
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:us-east-2:123456789012:secret:synthetic" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = { repository_url = "123456789012.dkr.ecr.us-east-2.amazonaws.com/synthetic" }
  }
  mock_resource "aws_kms_key" { defaults = { arn = "arn:aws:kms:us-east-2:123456789012:key/11111111-1111-1111-1111-111111111111" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/synthetic" } }
  mock_resource "aws_lambda_function" { defaults = { arn = "arn:aws:lambda:us-east-2:123456789012:function:synthetic" } }
  mock_resource "aws_sqs_queue" { defaults = { arn = "arn:aws:sqs:us-east-2:123456789012:synthetic" } }
  mock_resource "aws_db_instance" {
    defaults = { address = "synthetic.us-east-2.rds.amazonaws.com", master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-2:123456789012:secret:synthetic", kms_key_id = "synthetic", secret_status = "active" }] }
  }
  mock_data "aws_lambda_function" {
    defaults = {
      code_sha256 = "approved-guard"
      environment = [{ variables = {
        RUN_ID        = "test2026", GUARD_BUCKET = "csa-native-test2026-guard-123456789012",
        GUARD_KEY_ARN = "arn:aws:kms:us-east-2:123456789012:key/11111111-1111-1111-1111-111111111111"
      } }]
    }
  }
}
variables {
  account_id                       = "123456789012"
  run_id                           = "test2026"
  image_digest                     = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  source_commit                    = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  cleanup_at                       = "2026-12-01T12:00:00Z"
  guard_code_sha256                = "approved-guard"
  watchdog_name                    = "csa-native-test2026-watchdog"
  schedule_name                    = "csa-native-test2026-cleanup"
  operator_armed_schedule_verified = true
}
run "isolated_private_lane" {
  command = plan
  assert {
    condition     = alltrue([for d in aws_db_instance.store : !d.multi_az && !d.publicly_accessible && d.storage_encrypted && d.instance_class == "db.t4g.micro" && d.allocated_storage == 20 && d.max_allocated_storage == 0 && d.db_name == "csaacceptance" && d.backup_retention_period == 1 && d.deletion_protection])
    error_message = "Private encrypted Single-AZ micro databases with bounded storage required."
  }
  assert {
    condition     = length(aws_ecs_task_definition.native) == 5 && length(aws_vpc_endpoint.interface) == 4 && alltrue([for s in aws_subnet.private : !s.map_public_ip_on_launch])
    error_message = "Exact private one-off topology required."
  }

}
run "unarmed_rejected" {
  command = plan
  variables { operator_armed_schedule_verified = false }
  expect_failures = [aws_vpc.runtime]
}
run "retained_two_instance_contract" {
  command = plan
  assert {
    condition     = length(aws_db_instance.store) == 2 && !aws_db_instance.store["control"].delete_automated_backups && aws_db_instance.store["receipts"].delete_automated_backups && output.manifest.maximum_concurrent_instances == 2 && output.manifest.recovery_strategy == "retained-two-instance-v1" && output.manifest.source_deletion_scope == "disposable_synthetic_control_only" && output.manifest.verification_contract == "receipt-ledger-sha256-v1"
    error_message = "The native lane must retain source backups and declare the explicit two-instance protocol."
  }
}
run "mutable_image_rejected" {
  command = plan
  variables { image_digest = "latest" }
  expect_failures = [var.image_digest]
}
run "application_recovery_permissions_and_scratch" {
  command = plan
  variables { acceptance_workflow = "application-recovery-v1" }
  assert {
    condition     = output.manifest.application_protocol == "source-preflight-v2"
    error_message = "Application manifests must require the rollback-only source preflight protocol."
  }
  assert {
    condition     = length(aws_secretsmanager_secret.runtime) == 6 && alltrue([for t in aws_ecs_task_definition.native : t.cpu == "512" && t.memory == "1024" && jsondecode(t.container_definitions)[0].readonlyRootFilesystem && jsondecode(t.container_definitions)[0].command[2] == "controlplane.native_app_tasks" && jsondecode(t.container_definitions)[0].mountPoints[0].containerPath == "/recovery-work"])
    error_message = "Application tasks need a bounded non-root scratch volume while preserving a read-only root."
  }
  assert {
    condition     = length(jsondecode(aws_iam_role_policy.task["verify"].policy).Statement[0].Resource) == 1 && length(jsondecode(aws_iam_role_policy.task["receipt"].policy).Statement[0].Resource) == 5 && length(jsondecode(aws_iam_role_policy.task["bootstrap"].policy).Statement[0].Resource) == 8
    error_message = "Verifier must remain isolated; only bootstrap can retrieve managed owner credentials."
  }
}
run "unknown_application_contract_rejected" {
  command = plan
  variables { acceptance_workflow = "anything" }
  expect_failures = [var.acceptance_workflow]
}
