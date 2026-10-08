locals {
  ecs_trust = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole",
    Principal = { Service = "ecs-tasks.amazonaws.com" }, Condition = {
      StringEquals = { "aws:SourceAccount" = local.account },
      ArnLike      = { "aws:SourceArn" = "arn:${local.partition}:ecs:${var.region}:${local.account}:*" }
  } }] })
}
resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = local.ecs_trust
}
resource "aws_iam_role_policy" "execution" {
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = aws_ecr_repository.application.arn },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = [for log in aws_cloudwatch_log_group.task : "${log.arn}:*"] }
  ] })
}
resource "aws_iam_role" "task" {
  for_each           = local.tasks
  name               = "${local.name}-${each.key}"
  assume_role_policy = local.ecs_trust
}
resource "aws_iam_role_policy" "task" {
  for_each = local.tasks
  role     = aws_iam_role.task[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"],
    Resource = each.key == "migration" ? concat(local.db_secrets, [for secret in aws_secretsmanager_secret.runtime : secret.arn]) : [aws_secretsmanager_secret.runtime[local.secret_for[each.key]].arn] },
    { Effect = "Allow", Action = each.key == "migration" ? ["kms:Decrypt", "kms:GenerateDataKey"] : ["kms:Decrypt"], Resource = aws_kms_key.data.arn,
    Condition = { StringEquals = { "kms:ViaService" = "secretsmanager.${var.region}.amazonaws.com" } } }
  ], each.key == "migration" ? [{ Effect = "Allow", Action = ["secretsmanager:PutSecretValue"], Resource = [for secret in aws_secretsmanager_secret.runtime : secret.arn] }] : []) })
}
resource "aws_iam_role" "release" {
  name                 = "${local.name}-github-release"
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
    Principal = { Federated = var.github_oidc_provider_arn }, Action = "sts:AssumeRoleWithWebIdentity",
    Condition = { StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub" = var.github_oidc_subject } }
  }] })
}
resource "aws_iam_role_policy" "release" {
  role = aws_iam_role.release.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecs:DescribeServices", "ecs:UpdateService"], Resource = [for service in aws_ecs_service.runtime : service.id] },
    { Effect = "Allow", Action = ["ecs:RunTask"], Resource = [for name in local.tasks : "arn:${local.partition}:ecs:${var.region}:${local.account}:task-definition/${local.name}-${name}:*"], Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } } },
    { Effect = "Allow", Action = ["ecs:DescribeTasks", "ecs:ListTasks", "ecs:StopTask"], Resource = "*", Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn } } },
    { Effect = "Allow", Action = ["ecs:DescribeTaskDefinition"], Resource = "*" },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = concat([aws_iam_role.execution.arn], [for role in aws_iam_role.task : role.arn]), Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } } },
    { Effect = "Allow", Action = ["logs:GetLogEvents"], Resource = [for log in aws_cloudwatch_log_group.task : "${log.arn}:*"] }
  ] })
}
