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
run "deregister_uses_supported_regional_authorization" {
  command = plan
  assert {
    condition = anytrue([for statement in jsondecode(aws_iam_role_policy.cleanup.policy).Statement :
      try(statement.Action == "ecs:DeregisterTaskDefinition" && statement.Resource == "*" && statement.Condition.StringEquals["aws:RequestedRegion"] == "us-east-2", false)
    ])
    error_message = "DeregisterTaskDefinition requires its own wildcard-resource action restricted to Ohio."
  }
}
run "encrypted_repository_cleanup_grants" {
  command = plan
  assert {
    condition = anytrue([for statement in jsondecode(aws_iam_role_policy.cleanup.policy).Statement :
      try(contains(statement.Action, "kms:RetireGrant"), false) &&
      try(statement.Condition.StringEquals["aws:ResourceTag/AcceptanceRun"] == var.run_id, false) &&
      statement.Resource == "arn:aws:kms:us-east-2:${var.account_id}:key/*"
    ])
    error_message = "Encrypted ECR cleanup requires RetireGrant on this run's tagged runtime keys only."
  }
}
run "private_logs_are_archived_without_expanding_checker_authority" {
  command = plan
  assert {
    condition = anytrue([for statement in jsondecode(aws_iam_role_policy.cleanup.policy).Statement :
      try(contains(statement.Action, "logs:GetLogEvents"), false) &&
      try(contains(statement.Resource, "arn:aws:logs:us-east-2:${var.account_id}:log-group:/csa-native/${var.run_id}/*:*"), false) &&
      !try(contains(statement.Resource, "*"), false)
    ]) && strcontains(aws_iam_role_policy.function["watchdog"].policy, "/task-logs/*") && !strcontains(aws_iam_role_policy.function["checker"].policy, "/task-logs/*")
    error_message = "Only the watchdog may archive exact-run logs; the independent checker stays read-only."
  }
}
run "identity_rejected" {
  command = plan
  variables { run_id = "bad" }
  expect_failures = [var.run_id]
  # Valid names are not authority. Account/Free-plan/deadline checks are independently exercised in Python.
}
run "retained_backup_cleanup_is_tag_scoped" {
  command = plan
  assert {
    condition     = contains(local.read_actions, "rds:ListTagsForResource") && anytrue([for statement in jsondecode(aws_iam_role_policy.cleanup.policy).Statement : try(statement.Action == "rds:DeleteDBInstanceAutomatedBackup" && statement.Resource == "arn:aws:rds:us-east-2:${var.account_id}:auto-backup:*" && statement.Condition.StringEquals["aws:ResourceTag/AcceptanceRun"] == var.run_id, false)])
    error_message = "Retained backup cleanup must read ownership and keep its resource-tag restriction."
  }
}
run "deployment_clock_starts_at_apply" {
  command = apply
  variables { cleanup_at = null }
  assert {
    condition     = aws_scheduler_schedule.cleanup.start_date == timeadd(output.guard_manifest.deployment_started_at, "3h") && aws_scheduler_schedule.cleanup.end_date == timeadd(output.guard_manifest.deployment_started_at, "4h")
    error_message = "The execution and cleanup windows must start at this deployment, not planning or approval time."
  }
  assert {
    condition     = alltrue([for f in aws_lambda_function.guard : f.environment[0].variables["CLEANUP_AT"] == aws_scheduler_schedule.cleanup.start_date]) && aws_kms_key.guard.tags["GuardDeploymentStartedAt"] == output.guard_manifest.deployment_started_at && aws_s3_bucket.guard.tags["GuardDeploymentStartedAt"] == output.guard_manifest.deployment_started_at && aws_sqs_queue.failures.tags["GuardDeploymentStartedAt"] == output.guard_manifest.deployment_started_at
    error_message = "All guard timers and first chargeable resources must share one deployment clock."
  }
  assert {
    condition     = output.guard_manifest.deadline_policy == "three_hours_from_guard_apply_start"
    error_message = "The deployed manifest must declare its approved clock policy."
  }
}

run "retry_preserves_original_clock" {
  command = plan
  variables { cleanup_at = null }
  assert {
    condition     = output.guard_manifest.deployment_started_at == run.deployment_clock_starts_at_apply.guard_manifest.deployment_started_at && output.guard_manifest.cleanup_at == run.deployment_clock_starts_at_apply.guard_manifest.cleanup_at
    error_message = "A retry or later plan must not restart or extend the original execution window."
  }
}
