output "repository_url" { value = aws_ecr_repository.application.repository_url }
output "release_role_arn" { value = aws_iam_role.release.arn }
output "private_api_url" { value = "https://api.${var.internal_domain}" }
output "release_manifest" {
  description = "Non-secret release binding. Capture with terraform output -json release_manifest."
  value = {
    environment   = var.environment
    region        = var.region
    commit        = var.source_commit
    image         = "${aws_ecr_repository.application.repository_url}@${var.image_digest}"
    cluster       = aws_ecs_cluster.this.arn
    services      = { for key, service in aws_ecs_service.runtime : key => service.name }
    tasks         = { for key, task in aws_ecs_task_definition.runtime : key => task.arn }
    subnets       = aws_subnet.private[*].id
    groups        = { for key, group in aws_security_group.task : key => group.id }
    log_groups    = { for key, log in aws_cloudwatch_log_group.task : key => log.name }
    replicas      = { api = 2, remote = 2, worker = 2, observer = 1 }
    control_db    = coalesce(var.restored_control_identifier, aws_db_instance.state["control"].identifier)
    downstream_db = aws_db_instance.state["downstream"].identifier
  }
}
