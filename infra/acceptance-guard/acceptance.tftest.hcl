mock_provider "aws" {
  mock_resource "aws_kms_key" { defaults = { arn = "arn:aws:kms:us-east-2:123456789012:key/11111111-1111-1111-1111-111111111111" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/synthetic" } }
  mock_resource "aws_lambda_function" { defaults = { arn = "arn:aws:lambda:us-east-2:123456789012:function:synthetic" } }
  mock_resource "aws_sqs_queue" { defaults = { arn = "arn:aws:sqs:us-east-2:123456789012:synthetic" } }
}
variables {
  account_id = "123456789012"
  run_id     = "test2026"
  cleanup_at = "2026-12-01T12:00:00Z"
  bundle     = "../../.local/native-guard.zip"
}
run "independent_guard" {
  command = apply
  assert {
    condition     = aws_scheduler_schedule.cleanup.state == "ENABLED" && aws_scheduler_schedule.cleanup.schedule_expression == "rate(1 minute)" && aws_scheduler_schedule.cleanup.end_date == "2026-12-01T13:00:00Z"
    error_message = "Exact four-hour total bound and enabled deadline schedule required."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.function["checker"].policy, "Delete") && !strcontains(aws_iam_role_policy.function["checker"].policy, "GetSecretValue") && !strcontains(aws_iam_role_policy.cleanup.policy, "GetSecretValue")
    error_message = "Independent reader cannot delete resources or retrieve secrets."
  }
  assert {
    condition     = length(aws_lambda_function.guard) == 2 && aws_lambda_function.guard["watchdog"].reserved_concurrent_executions == 1 && aws_lambda_function.guard["watchdog"].timeout == 120 && aws_s3_bucket_public_access_block.guard.block_public_policy
    error_message = "Bounded guard and private independent evidence required."
  }
}
run "identity_rejected" {
  command = plan
  variables { run_id = "bad" }
  expect_failures = [var.run_id]
  # Valid names are not authority. Account/Free-plan/deadline checks are independently exercised in Python.
}
