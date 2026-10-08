terraform {
  required_version = "= 1.13.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.15.0" }
  }
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Application = "enterprise-control", Environment = var.environment, ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_availability_zones" "available" { state = "available" }

locals {
  name       = "control-${var.environment}"
  partition  = data.aws_partition.current.partition
  account    = data.aws_caller_identity.current.account_id
  services   = toset(["api", "worker", "remote", "observer"])
  tasks      = toset(["api", "worker", "remote", "observer", "migration", "verifier"])
  secret_for = { api = "api", worker = "worker", remote = "remote", observer = "observer", verifier = "verifier" }
  db_secrets = [local.control_master, aws_db_instance.state["downstream"].master_user_secret[0].secret_arn]
}
