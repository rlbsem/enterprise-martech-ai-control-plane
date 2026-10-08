resource "aws_kms_key" "data" {
  description             = "${local.name} state, runtime secrets and logs"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "AccountAdministration", Effect = "Allow", Principal = { AWS = "arn:${local.partition}:iam::${local.account}:root" }, Action = "kms:*", Resource = "*" },
    { Sid    = "EncryptedLogs", Effect = "Allow", Principal = { Service = "logs.${var.region}.amazonaws.com" },
      Action = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"], Resource = "*",
    Condition = { ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:${local.partition}:logs:${var.region}:${local.account}:log-group:/enterprise/${local.name}/*" } } }
  ] })
}
resource "aws_kms_alias" "data" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.data.id
}
resource "aws_db_subnet_group" "private" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}
resource "aws_db_parameter_group" "postgres" {
  name_prefix = "${local.name}-"
  family      = "postgres17"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_statement"
    value = "none"
  }
  parameter {
    name  = "log_min_error_statement"
    value = "panic"
  }
  lifecycle { create_before_destroy = true }
}
resource "aws_db_instance" "state" {
  for_each                        = toset(["control", "downstream"])
  identifier                      = "${local.name}-${each.key}"
  engine                          = "postgres"
  engine_version                  = "17.6"
  instance_class                  = var.db_class
  db_name                         = "control"
  username                        = "control_owner"
  manage_master_user_password     = true
  master_user_secret_kms_key_id   = aws_kms_key.data.arn
  storage_encrypted               = true
  kms_key_id                      = aws_kms_key.data.arn
  allocated_storage               = 30
  max_allocated_storage           = 100
  storage_type                    = "gp3"
  multi_az                        = var.multi_az
  publicly_accessible             = false
  db_subnet_group_name            = aws_db_subnet_group.private.name
  vpc_security_group_ids          = [aws_security_group.database[each.key].id]
  parameter_group_name            = aws_db_parameter_group.postgres.name
  backup_retention_period         = 14
  backup_window                   = "06:00-07:00"
  maintenance_window              = "sun:07:30-sun:08:30"
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${local.name}-${each.key}-final"
  copy_tags_to_snapshot           = true
  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.data.arn
  apply_immediately               = false
  lifecycle { prevent_destroy = true }
}
data "aws_db_instance" "restored" {
  count                  = var.restored_control_identifier == null ? 0 : 1
  db_instance_identifier = var.restored_control_identifier
}
locals {
  control_address = var.restored_control_identifier == null ? aws_db_instance.state["control"].address : data.aws_db_instance.restored[0].address
  control_master  = var.restored_control_identifier == null ? aws_db_instance.state["control"].master_user_secret[0].secret_arn : data.aws_db_instance.restored[0].master_user_secret[0].secret_arn
}
resource "aws_secretsmanager_secret" "runtime" {
  for_each                = toset(["api", "worker", "remote", "verifier", "observer"])
  name                    = "${local.name}/${each.key}"
  kms_key_id              = aws_kms_key.data.arn
  recovery_window_in_days = 30
  description             = "Startup configuration for ${each.key}; values written by bootstrap task, never Terraform"
}
resource "aws_ecr_repository" "application" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
  image_scanning_configuration { scan_on_push = true }
}
