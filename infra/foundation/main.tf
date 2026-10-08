terraform {
  required_version = "= 1.13.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.15.0" }
  }
}
provider "aws" { region = "ca-central-1" }
variable "state_bucket_name" {
  type = string
  validation {
    condition     = can(regex("^control-tfstate-[a-z0-9-]+$", var.state_bucket_name))
    error_message = "Use a globally unique control-tfstate- name."
  }
}
resource "aws_s3_bucket" "state" {
  bucket        = var.state_bucket_name
  force_destroy = false
  tags          = { Application = "enterprise-control", Purpose = "terraform-state" }
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
    bucket_key_enabled = true
  }
}
resource "aws_kms_key" "state" {
  description             = "Terraform state encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Deny", Principal = "*", Action = "s3:*",
  Resource = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } }] })
}
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
output "state_bucket" { value = aws_s3_bucket.state.id }
output "oidc_provider_arn" { value = aws_iam_openid_connect_provider.github.arn }
