mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["ca-central-1a", "ca-central-1b"] }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012", arn = "arn:aws:iam::123456789012:root", user_id = "mock" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
}
variables {
  environment              = "stage"
  source_commit            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  image_digest             = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  certificate_arn          = "arn:aws:acm:ca-central-1:123456789012:certificate/mock-only"
  internal_domain          = "control.example.test"
  client_cidrs             = ["10.50.0.0/16"]
  alarm_topic_arn          = "arn:aws:sns:ca-central-1:123456789012:mock-only"
  github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
  github_oidc_subject      = "repo:rlbsem/enterprise-martech-ai-control-plane:environment:aws-release"
}
run "private_encrypted_and_quiescent" {
  command = plan
  assert {
    condition     = alltrue([for db in aws_db_instance.state : !db.publicly_accessible && db.storage_encrypted && db.backup_retention_period >= 14 && db.deletion_protection && db.multi_az])
    error_message = "Databases must retain private, encrypted, protected recovery boundaries."
  }
  assert {
    condition     = alltrue([for service in aws_ecs_service.runtime : service.desired_count == 0 && !service.network_configuration[0].assign_public_ip])
    error_message = "Infrastructure creation must not open admission before bootstrap/reconciliation."
  }
  assert {
    condition     = aws_ecr_repository.application.image_tag_mutability == "IMMUTABLE" && aws_lb.private.internal
    error_message = "Images are immutable; ingress is private."
  }
  assert {
    condition     = aws_lb_listener.https.protocol == "HTTPS" && aws_lb_listener.https.port == 443
    error_message = "TLS is required at the private ingress boundary."
  }
}
run "reject_mutable_image" {
  command = plan
  variables { image_digest = "latest" }
  expect_failures = [var.image_digest]
}
run "reject_untrusted_oidc" {
  command = plan
  variables { github_oidc_subject = "repo:other/repo:environment:aws-release" }
  expect_failures = [var.github_oidc_subject]
}

run "reject_cross_environment_restore_binding" {
  command = plan
  variables { restored_control_identifier = "control-prod-restore-other" }
  expect_failures = [var.restored_control_identifier]
}

run "reject_public_restore_binding" {
  command = plan
  variables { restored_control_identifier = "control-stage-restore-incident" }
  override_data {
    target          = data.aws_db_instance.restored[0]
    override_during = plan
    values = {
      engine                  = "postgres"
      storage_encrypted       = true
      publicly_accessible     = true
      multi_az                = true
      backup_retention_period = 14
      db_name                 = "control"
      db_subnet_group         = "wrong-subnet"
      vpc_security_groups     = ["sg-other"]
      db_parameter_groups     = ["wrong-parameters"]
      master_user_secret = [{
        kms_key_id    = "arn:aws:kms:ca-central-1:123456789012:key/mock"
        secret_arn    = "arn:aws:secretsmanager:ca-central-1:123456789012:secret:mock"
        secret_status = "active"
      }]
    }
  }
  expect_failures = [data.aws_db_instance.restored[0]]
}

run "reject_wrong_restore_binding" {
  command = plan
  variables { restored_control_identifier = "control-stage-restore-incident" }
  override_data {
    target          = data.aws_db_instance.restored[0]
    override_during = plan
    values = {
      engine                  = "postgres"
      storage_encrypted       = true
      publicly_accessible     = false
      multi_az                = true
      backup_retention_period = 14
      db_name                 = "control"
      db_subnet_group         = "wrong-subnet"
      vpc_security_groups     = ["sg-other"]
      db_parameter_groups     = ["wrong-parameters"]
      master_user_secret = [{
        kms_key_id    = "arn:aws:kms:ca-central-1:123456789012:key/mock"
        secret_arn    = "arn:aws:secretsmanager:ca-central-1:123456789012:secret:mock"
        secret_status = "active"
      }]
    }
  }
  override_resource {
    target          = aws_kms_key.data
    override_during = plan
    values          = { arn = "arn:aws:kms:ca-central-1:123456789012:key/mock" }
  }
  override_resource {
    target          = aws_security_group.database["control"]
    override_during = plan
    values          = { id = "sg-00000000" }
  }
  override_resource {
    target          = aws_db_parameter_group.postgres
    override_during = plan
    values          = { name = "control-stage-tls" }
  }
  expect_failures = [aws_ecs_task_definition.runtime]
}
