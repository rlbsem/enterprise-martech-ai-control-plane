mock_provider "aws" {
  mock_resource "aws_kms_key" { defaults = { arn = "arn:aws:kms:us-east-2:123456789012:key/11111111-1111-1111-1111-111111111111" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/synthetic" } }
  mock_resource "aws_lambda_function" { defaults = { arn = "arn:aws:lambda:us-east-2:123456789012:function:synthetic" } }
  mock_resource "aws_sqs_queue" { defaults = { arn = "arn:aws:sqs:us-east-2:123456789012:synthetic" } }
  mock_resource "aws_db_instance" {
    defaults = { master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-2:123456789012:secret:synthetic", kms_key_id = "synthetic", secret_status = "active" }] }
  }
  mock_data "aws_lambda_function" {
    defaults = {
      code_sha256 = "approved-guard"
      environment = [{ variables = { RUN_ID = "test2026" } }]
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
run "mutable_image_rejected" {
  command = plan
  variables { image_digest = "latest" }
  expect_failures = [var.image_digest]
}
