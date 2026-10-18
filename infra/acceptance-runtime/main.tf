terraform {
  required_version = "= 1.13.5"
  required_providers { aws = { source = "hashicorp/aws", version = "= 6.15.0" } }
  backend "s3" {}
}
provider "aws" {
  region              = "us-east-2"
  allowed_account_ids = [var.account_id]
  default_tags { tags = { Application = "native-acceptance", AcceptanceRun = var.run_id } }
}
variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Pin the approved isolated account."
  }
}
variable "run_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9]{8,16}$", var.run_id))
    error_message = "Use a fresh isolated run identifier."
  }
}
variable "image_digest" {
  type = string
  validation {
    condition     = can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
    error_message = "An existing source-bound immutable image is required."
  }
}
variable "source_commit" {
  type = string
  validation {
    condition     = can(regex("^[a-f0-9]{40}$", var.source_commit))
    error_message = "Pin the complete source commit."
  }
}
variable "cleanup_at" { type = string }
variable "guard_code_sha256" { type = string }
variable "watchdog_name" { type = string }
variable "schedule_name" { type = string }
variable "operator_armed_schedule_verified" {
  type    = bool
  default = false
}
locals {
  name       = "csa-native-${var.run_id}"
  tasks      = toset(["bootstrap", "baseline", "control", "receipt", "verify"])
  secret_for = { baseline = "api", control = "api", receipt = "remote", verify = "verifier" }
  db_users   = toset(["control", "receipts"])
}
data "aws_lambda_function" "watchdog" { function_name = var.watchdog_name }
resource "aws_vpc" "runtime" {
  cidr_block           = "10.43.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  lifecycle {
    precondition {
      condition     = var.operator_armed_schedule_verified && data.aws_lambda_function.watchdog.code_sha256 == var.guard_code_sha256 && try(data.aws_lambda_function.watchdog.environment[0].variables["RUN_ID"], "") == var.run_id
      error_message = "Run native_deploy's independent schedule/account/Free-plan checks before provisioning."
    }
  }
}
resource "aws_subnet" "private" {
  count                   = 2
  vpc_id                  = aws_vpc.runtime.id
  cidr_block              = cidrsubnet("10.43.0.0/16", 8, count.index)
  availability_zone       = ["us-east-2a", "us-east-2b"][count.index]
  map_public_ip_on_launch = false
}
resource "aws_route_table" "private" { vpc_id = aws_vpc.runtime.id }
resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
resource "aws_security_group" "task" {
  for_each    = local.tasks
  name        = "${local.name}-${each.key}"
  description = "One-shot ${each.key}; no public ingress"
  vpc_id      = aws_vpc.runtime.id
}
resource "aws_security_group" "database" {
  for_each    = local.db_users
  name        = "${local.name}-${each.key}-db"
  description = "Private independent ${each.key} database"
  vpc_id      = aws_vpc.runtime.id
}
resource "aws_security_group" "endpoint" {
  name        = "${local.name}-endpoints"
  description = "Scoped TLS access to private AWS APIs"
  vpc_id      = aws_vpc.runtime.id
}
locals {
  connections = { bootstrap_control = ["bootstrap", "control"], bootstrap_receipts = ["bootstrap", "receipts"],
    baseline_control = ["baseline", "control"], control_control = ["control", "control"],
  receipt_receipts = ["receipt", "receipts"], verify_control = ["verify", "control"], verify_receipts = ["verify", "receipts"] }
}
resource "aws_vpc_security_group_ingress_rule" "db" {
  for_each                     = local.connections
  security_group_id            = aws_security_group.database[each.value[1]].id
  referenced_security_group_id = aws_security_group.task[each.value[0]].id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_vpc_security_group_egress_rule" "db" {
  for_each                     = local.connections
  security_group_id            = aws_security_group.task[each.value[0]].id
  referenced_security_group_id = aws_security_group.database[each.value[1]].id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_vpc_security_group_ingress_rule" "endpoint" {
  for_each                     = local.tasks
  security_group_id            = aws_security_group.endpoint.id
  referenced_security_group_id = aws_security_group.task[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_egress_rule" "endpoint" {
  for_each                     = local.tasks
  security_group_id            = aws_security_group.task[each.key].id
  referenced_security_group_id = aws_security_group.endpoint.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(["ecr.api", "ecr.dkr", "logs", "secretsmanager"])
  vpc_id              = aws_vpc.runtime.id
  service_name        = "com.amazonaws.us-east-2.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [aws_subnet.private[0].id]
  security_group_ids  = [aws_security_group.endpoint.id]
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.runtime.id
  service_name      = "com.amazonaws.us-east-2.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  policy            = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = "*", Action = "s3:GetObject", Resource = "arn:aws:s3:::prod-us-east-2-starport-layer-bucket/*" }] })
}
resource "aws_vpc_security_group_egress_rule" "s3" {
  for_each          = local.tasks
  security_group_id = aws_security_group.task[each.key].id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_kms_key" "runtime" {
  description             = "Isolated acceptance ${var.run_id}; no production data"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Principal = { AWS = "arn:aws:iam::${var.account_id}:root" }, Action = "kms:*", Resource = "*" },
    { Effect = "Allow", Principal = { Service = "logs.us-east-2.amazonaws.com" }, Action = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"], Resource = "*", Condition = { ArnLike = { "kms:EncryptionContext:aws:logs:arn" = ["arn:aws:logs:us-east-2:${var.account_id}:log-group:/csa-native/${var.run_id}/*", "arn:aws:logs:us-east-2:${var.account_id}:log-group:/aws/rds/instance/${local.name}-*/postgresql"] } } }
  ] })
}
resource "aws_db_subnet_group" "private" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}
resource "aws_db_parameter_group" "tls" {
  name   = local.name
  family = "postgres17"
  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }
  parameter {
    name  = "log_statement"
    value = "none"
  }
  parameter {
    name  = "log_min_error_statement"
    value = "panic"
  }
}
resource "aws_db_instance" "store" {
  for_each                        = local.db_users
  identifier                      = "${local.name}-${each.key}"
  engine                          = "postgres"
  engine_version                  = "17.11"
  instance_class                  = "db.t4g.micro"
  allocated_storage               = 20
  max_allocated_storage           = 0
  storage_type                    = "gp3"
  multi_az                        = false
  db_name                         = "csaacceptance"
  username                        = "acceptance_owner"
  manage_master_user_password     = true
  master_user_secret_kms_key_id   = aws_kms_key.runtime.arn
  kms_key_id                      = aws_kms_key.runtime.arn
  storage_encrypted               = true
  publicly_accessible             = false
  db_subnet_group_name            = aws_db_subnet_group.private.name
  vpc_security_group_ids          = [aws_security_group.database[each.key].id]
  parameter_group_name            = aws_db_parameter_group.tls.name
  backup_retention_period         = 1
  deletion_protection             = true
  skip_final_snapshot             = true
  copy_tags_to_snapshot           = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  auto_minor_version_upgrade      = false
  lifecycle { prevent_destroy = true }
}
resource "aws_secretsmanager_secret" "runtime" {
  for_each                = toset(["api", "worker", "remote", "observer", "verifier"])
  name                    = "${local.name}/${each.key}"
  kms_key_id              = aws_kms_key.runtime.arn
  recovery_window_in_days = 7
}
resource "aws_ecr_repository" "image" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.runtime.arn
  }
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_ecs_cluster" "runtime" { name = local.name }
resource "aws_cloudwatch_log_group" "task" {
  for_each          = local.tasks
  name              = "/csa-native/${var.run_id}/${each.key}"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.runtime.arn
}
resource "aws_cloudwatch_log_group" "database" {
  for_each          = toset(["control", "receipts", "restore"])
  name              = "/aws/rds/instance/${local.name}-${each.key}/postgresql"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.runtime.arn
}
locals {
  task_trust = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = var.account_id }, ArnLike = { "aws:SourceArn" = "arn:aws:ecs:us-east-2:${var.account_id}:*" } } }] })
}
resource "aws_iam_role" "task" {
  for_each           = local.tasks
  name               = "${local.name}-${each.key}"
  path               = "/csa-native/${var.run_id}/"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  path               = "/csa-native/${var.run_id}/"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role_policy" "execution" {
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = aws_ecr_repository.image.arn },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = [for g in aws_cloudwatch_log_group.task : "${g.arn}:*"] }
  ] })
}
resource "aws_iam_role_policy" "task" {
  for_each = local.tasks
  role     = aws_iam_role.task[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = each.key == "bootstrap" ? concat([for s in aws_secretsmanager_secret.runtime : s.arn], [for d in aws_db_instance.store : d.master_user_secret[0].secret_arn]) : [aws_secretsmanager_secret.runtime[local.secret_for[each.key]].arn] },
    { Effect = "Allow", Action = each.key == "bootstrap" ? ["kms:Decrypt", "kms:GenerateDataKey"] : ["kms:Decrypt"], Resource = aws_kms_key.runtime.arn, Condition = { StringEquals = { "kms:ViaService" = "secretsmanager.us-east-2.amazonaws.com" } } }
  ], each.key == "bootstrap" ? [{ Effect = "Allow", Action = "secretsmanager:PutSecretValue", Resource = [for s in aws_secretsmanager_secret.runtime : s.arn] }] : []) })
}
resource "aws_ecs_task_definition" "native" {
  for_each                 = local.tasks
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task[each.key].arn
  container_definitions = jsonencode([{
    name            = each.key, image = "${aws_ecr_repository.image.repository_url}@${var.image_digest}", essential = true,
    user            = "10001", readonlyRootFilesystem = true, stopTimeout = 60,
    command         = ["python", "-m", "controlplane.native_tasks", each.key],
    linuxParameters = { initProcessEnabled = true, capabilities = { drop = ["ALL"] } },
    environment = concat([{ name = "ACCEPTANCE_RUN", value = var.run_id }, { name = "SOURCE_COMMIT", value = var.source_commit },
      { name = "AWS_DEFAULT_REGION", value = "us-east-2" }, { name = "PYTHONDONTWRITEBYTECODE", value = "1" }],
      each.key == "bootstrap" ? [
        { name = "DB_HOSTS", value = jsonencode({ control = aws_db_instance.store["control"].address, downstream = aws_db_instance.store["receipts"].address }) },
        { name = "DB_MASTER_SECRET_ARNS", value = jsonencode({ control = aws_db_instance.store["control"].master_user_secret[0].secret_arn, downstream = aws_db_instance.store["receipts"].master_user_secret[0].secret_arn }) },
        { name = "RUNTIME_SECRET_ARNS", value = jsonencode({ for k, s in aws_secretsmanager_secret.runtime : k => s.arn }) }
    ] : [{ name = "RUNTIME_SECRET_ARN", value = aws_secretsmanager_secret.runtime[local.secret_for[each.key]].arn }]),
    logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.task[each.key].name, awslogs-region = "us-east-2", awslogs-stream-prefix = "task", mode = "blocking" } }
  }])
}
output "manifest" {
  value = { account = var.account_id, region = "us-east-2", run = var.run_id, cleanup_at = var.cleanup_at, backup_retention_days = 1, database_name = "csaacceptance",
    image       = "${aws_ecr_repository.image.repository_url}@${var.image_digest}", commit = var.source_commit,
    tasks       = { for k, t in aws_ecs_task_definition.native : k => t.arn }, cluster = aws_ecs_cluster.runtime.arn,
    control_db  = aws_db_instance.store["control"].identifier, downstream_db = aws_db_instance.store["receipts"].identifier,
    task_subnet = aws_subnet.private[0].id, task_groups = { for k, g in aws_security_group.task : k => g.id },
  schedule = var.schedule_name, watchdog = var.watchdog_name, guard_code_sha256 = var.guard_code_sha256 }
}

