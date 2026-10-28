terraform {
  required_version = "= 1.13.5"
  required_providers { aws = { source = "hashicorp/aws", version = "= 6.15.0" } }
  backend "local" {}
}
provider "aws" {
  region              = "us-east-2"
  allowed_account_ids = [var.account_id]
  default_tags { tags = { Application = "native-acceptance-guard", AcceptanceGuard = var.run_id } }
}
variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Pin the approved account."
  }
}
variable "run_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9]{8,16}$", var.run_id))
    error_message = "Pin a fresh isolated run."
  }
}
variable "cleanup_at" {
  type    = string
  default = null
  validation {
    condition     = var.cleanup_at == null || can(formatdate("YYYY-MM-DD'T'hh:mm:ssZ", var.cleanup_at))
    error_message = "Use null for a deployment-anchored three-hour clock, or an explicit UTC deadline."
  }
}
variable "bundle" { type = string }
# Local state only: freeze the first apply timestamp so retries cannot extend the window.
resource "terraform_data" "deployment_clock" {
  count = var.cleanup_at == null ? 1 : 0
  input = timestamp()
  lifecycle { ignore_changes = [input] }
}
locals {
  name                  = "csa-native-${var.run_id}"
  deployment_started_at = var.cleanup_at == null ? terraform_data.deployment_clock[0].output : timeadd(var.cleanup_at, "-3h")
  cleanup_deadline      = timeadd(local.deployment_started_at, "3h")
  deadline_policy       = var.cleanup_at == null ? "three_hours_from_guard_apply_start" : "fixed_deadline"
  functions             = toset(["watchdog", "checker"])
  runtime_tag           = { StringEquals = { "aws:ResourceTag/AcceptanceRun" = var.run_id } }
  read_actions          = ["sts:GetCallerIdentity", "rds:DescribeDBInstances", "rds:DescribeDBSnapshots", "rds:DescribeDBInstanceAutomatedBackups", "rds:ListTagsForResource", "rds:DescribeDBSubnetGroups", "rds:DescribeDBParameterGroups", "ecs:DescribeClusters", "ecs:ListTasks", "ecs:ListTaskDefinitions", "ecr:DescribeRepositories", "ecr:DescribeImages", "secretsmanager:ListSecrets", "secretsmanager:DescribeSecret", "logs:DescribeLogGroups", "kms:ListKeys", "kms:DescribeKey", "kms:ListResourceTags", "ec2:DescribeVpcs", "ec2:DescribeVpcEndpoints", "ec2:DescribeNetworkInterfaces", "ec2:DescribeRouteTables", "ec2:DescribeSecurityGroups", "ec2:DescribeSubnets", "iam:ListRoles"]
  trust                 = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_kms_key" "guard" {
  tags                    = { GuardDeploymentStartedAt = local.deployment_started_at }
  description             = "Independent cleanup evidence ${var.run_id}"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }, Action = "kms:*", Resource = "*" },
    { Effect = "Allow", Principal = { Service = "logs.us-east-2.amazonaws.com" }, Action = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"], Resource = "*", Condition = { ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:us-east-2:${var.account_id}:log-group:/aws/lambda/${local.name}-*" } } }
  ] })
}
resource "aws_s3_bucket" "guard" {
  bucket = "${local.name}-guard-${var.account_id}"
  tags   = { GuardDeploymentStartedAt = local.deployment_started_at }
}
resource "aws_s3_bucket_public_access_block" "guard" {
  bucket                  = aws_s3_bucket.guard.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "guard" {
  bucket = aws_s3_bucket.guard.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "guard" {
  bucket = aws_s3_bucket.guard.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.guard.arn
    }
  }
}
resource "aws_s3_bucket_policy" "guard" {
  bucket = aws_s3_bucket.guard.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.guard.arn, "${aws_s3_bucket.guard.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } }] })
}
resource "aws_s3_bucket_lifecycle_configuration" "guard" {
  bucket     = aws_s3_bucket.guard.id
  depends_on = [aws_s3_bucket_versioning.guard]
  rule {
    id     = "bounded-private-evidence"
    status = "Enabled"
    filter { prefix = "checks/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_sqs_queue" "failures" {
  tags                      = { GuardDeploymentStartedAt = local.deployment_started_at }
  name                      = "${local.name}-cleanup-failures"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 604800
}
resource "aws_cloudwatch_metric_alarm" "failures" {
  alarm_name          = "${local.name}-cleanup-failures"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.failures.name }
}
resource "aws_iam_role" "function" {
  for_each           = local.functions
  name               = "${local.name}-${each.key}"
  path               = "/csa-native-guard/${var.run_id}/"
  assume_role_policy = local.trust
}
resource "aws_cloudwatch_log_group" "function" {
  for_each          = local.functions
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.guard.arn
}
resource "aws_iam_role_policy" "function" {
  for_each = local.functions
  role     = aws_iam_role.function[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = local.read_actions, Resource = "*" },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.function[each.key].arn}:*" },
    { Effect = "Allow", Action = "s3:GetObject", Resource = "${aws_s3_bucket.guard.arn}/inventory/registry.json" },
    { Effect = "Allow", Action = "s3:PutObject", Resource = each.key == "checker" ? ["${aws_s3_bucket.guard.arn}/checks/*"] : ["${aws_s3_bucket.guard.arn}/inventory/*", "${aws_s3_bucket.guard.arn}/cleanup/*"] },
    { Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = aws_kms_key.guard.arn, Condition = { StringEquals = { "kms:ViaService" = "s3.us-east-2.amazonaws.com" } } },
    { Effect = "Allow", Action = "sqs:SendMessage", Resource = aws_sqs_queue.failures.arn }
  ] })
}
resource "aws_iam_role_policy" "cleanup" {
  role = aws_iam_role.function["watchdog"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["rds:ModifyDBInstance", "rds:DeleteDBInstance", "rds:DeleteDBSnapshot", "rds:DeleteDBSubnetGroup", "rds:DeleteDBParameterGroup"], Resource = [for type in ["db", "snapshot", "subgrp", "pg"] : "arn:aws:rds:us-east-2:${var.account_id}:${type}:${local.name}*"] },
    { Effect = "Allow", Action = "rds:DeleteDBInstanceAutomatedBackup", Resource = "arn:aws:rds:us-east-2:${var.account_id}:auto-backup:*", Condition = { StringEquals = { "aws:ResourceTag/AcceptanceRun" = var.run_id } } },
    { Effect = "Allow", Action = ["ecs:StopTask", "ecs:DeleteCluster", "ecs:DeregisterTaskDefinition", "ecs:DeleteTaskDefinitions"], Resource = ["arn:aws:ecs:us-east-2:${var.account_id}:cluster/${local.name}", "arn:aws:ecs:us-east-2:${var.account_id}:task/${local.name}/*", "arn:aws:ecs:us-east-2:${var.account_id}:task-definition/${local.name}-*:*"] },
    { Effect = "Allow", Action = "ecr:DeleteRepository", Resource = "arn:aws:ecr:us-east-2:${var.account_id}:repository/${local.name}" },
    { Effect = "Allow", Action = "secretsmanager:DeleteSecret", Resource = "arn:aws:secretsmanager:us-east-2:${var.account_id}:secret:${local.name}/*" },
    { Effect = "Allow", Action = "logs:DeleteLogGroup", Resource = ["arn:aws:logs:us-east-2:${var.account_id}:log-group:/csa-native/${var.run_id}/*:*", "arn:aws:logs:us-east-2:${var.account_id}:log-group:/aws/rds/instance/${local.name}-*/postgresql:*"] },
    { Effect = "Allow", Action = ["ec2:DeleteVpcEndpoints", "ec2:DeleteNetworkInterface", "ec2:DeleteRouteTable", "ec2:DeleteSecurityGroup", "ec2:DeleteSubnet", "ec2:DeleteVpc", "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:DisassociateRouteTable"], Resource = "*", Condition = local.runtime_tag },
    { Effect = "Allow", Action = ["iam:ListRolePolicies", "iam:DeleteRolePolicy", "iam:DeleteRole"], Resource = "arn:aws:iam::${var.account_id}:role/csa-native/${var.run_id}/*" },
    { Effect = "Allow", Action = ["kms:ScheduleKeyDeletion", "kms:RetireGrant"], Resource = "arn:aws:kms:us-east-2:${var.account_id}:key/*", Condition = local.runtime_tag },
    { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = "arn:aws:lambda:us-east-2:${var.account_id}:function:${local.name}-checker" }
  ] })
}
resource "aws_lambda_function" "guard" {
  for_each                       = local.functions
  function_name                  = "${local.name}-${each.key}"
  role                           = aws_iam_role.function[each.key].arn
  runtime                        = "python3.12"
  handler                        = each.key == "watchdog" ? "controlplane.native_watchdog.handler" : "controlplane.native_inventory.handler"
  filename                       = var.bundle
  source_code_hash               = filebase64sha256(var.bundle)
  timeout                        = 120
  memory_size                    = 256
  reserved_concurrent_executions = 1
  environment {
    variables = {
      RUN_ID      = var.run_id, ACCOUNT_ID = var.account_id, CLEANUP_AT = local.cleanup_deadline,
      DISPOSITION = "discard_isolated_synthetic_state", GUARD_BUCKET = aws_s3_bucket.guard.id, GUARD_KEY_ARN = aws_kms_key.guard.arn,
      CHECKER_ARN = "arn:aws:lambda:us-east-2:${var.account_id}:function:${local.name}-checker"
    }
  }
  depends_on = [aws_cloudwatch_log_group.function, aws_iam_role_policy.function, aws_iam_role_policy.cleanup]
}
resource "aws_lambda_function_event_invoke_config" "guard" {
  for_each                     = local.functions
  function_name                = aws_lambda_function.guard[each.key].function_name
  maximum_event_age_in_seconds = 300
  maximum_retry_attempts       = 2
  destination_config {
    on_failure { destination = aws_sqs_queue.failures.arn }
  }
}
resource "aws_iam_role" "schedule" {
  name               = "${local.name}-schedule"
  path               = "/csa-native-guard/${var.run_id}/"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = var.account_id }, ArnEquals = { "aws:SourceArn" = "arn:aws:scheduler:us-east-2:${var.account_id}:schedule-group/default" } } }] })
}
resource "aws_iam_role_policy" "schedule" {
  role = aws_iam_role.schedule.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.guard["watchdog"].arn },
    { Effect = "Allow", Action = "sqs:SendMessage", Resource = aws_sqs_queue.failures.arn }
  ] })
}
resource "aws_scheduler_schedule" "cleanup" {
  name                         = "${local.name}-cleanup"
  group_name                   = "default"
  schedule_expression          = "rate(1 minute)"
  schedule_expression_timezone = "UTC"
  start_date                   = local.cleanup_deadline
  end_date                     = timeadd(local.cleanup_deadline, "1h")
  state                        = "ENABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.guard["watchdog"].arn
    role_arn = aws_iam_role.schedule.arn
    input    = "{}"
    retry_policy {
      maximum_event_age_in_seconds = 300
      maximum_retry_attempts       = 2
    }
    dead_letter_config { arn = aws_sqs_queue.failures.arn }
  }
  depends_on = [aws_iam_role_policy.schedule, aws_lambda_function_event_invoke_config.guard]
}
output "guard_manifest" {
  value = { account = var.account_id, region = "us-east-2", run = var.run_id, cleanup_at = local.cleanup_deadline, deployment_started_at = local.deployment_started_at, deadline_policy = local.deadline_policy,
    watchdog          = aws_lambda_function.guard["watchdog"].function_name, schedule = aws_scheduler_schedule.cleanup.name,
    guard_code_sha256 = filebase64sha256(var.bundle), guard_bucket = aws_s3_bucket.guard.id,
    checker           = aws_lambda_function.guard["checker"].function_name, queue = aws_sqs_queue.failures.id,
  guard_key = aws_kms_key.guard.key_id }
}


