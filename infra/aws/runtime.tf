resource "aws_ecs_cluster" "this" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}
resource "aws_cloudwatch_log_group" "task" {
  for_each          = local.tasks
  name              = "/enterprise/${local.name}/${each.key}"
  retention_in_days = 90
  kms_key_id        = aws_kms_key.data.arn
}
resource "aws_ecs_task_definition" "runtime" {
  for_each                 = local.tasks
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task[each.key].arn
  container_definitions = jsonencode([{
    name            = each.key, image = "${aws_ecr_repository.application.repository_url}@${var.image_digest}",
    essential       = true, user = "10001", readonlyRootFilesystem = true,
    command         = ["python", "-m", "controlplane.cloud", each.key],
    stopTimeout     = 60,
    linuxParameters = { initProcessEnabled = true, capabilities = { drop = ["ALL"] } },
    portMappings    = contains(["api", "remote"], each.key) ? [{ containerPort = 8000, protocol = "tcp" }] : [],
    environment = concat([
      { name = "ENVIRONMENT", value = var.environment },
      { name = "SOURCE_COMMIT", value = var.source_commit },
      { name = "AWS_DEFAULT_REGION", value = var.region },
      { name = "DOWNSTREAM_URL", value = "https://effects.${var.internal_domain}" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
      { name = "ENABLE_METRICS", value = "1" }
      ], each.key == "migration" ? [
      { name = "DB_HOSTS", value = jsonencode({ control = local.control_address, downstream = aws_db_instance.state["downstream"].address }) },
      { name = "DB_MASTER_SECRET_ARNS", value = jsonencode({ control = local.control_master, downstream = aws_db_instance.state["downstream"].master_user_secret[0].secret_arn }) },
      { name = "RUNTIME_SECRET_ARNS", value = jsonencode({ for name, secret in aws_secretsmanager_secret.runtime : name => secret.arn }) }
    ] : [{ name = "RUNTIME_SECRET_ARN", value = aws_secretsmanager_secret.runtime[local.secret_for[each.key]].arn }]),
    logConfiguration = { logDriver = "awslogs", options = {
      awslogs-group = aws_cloudwatch_log_group.task[each.key].name, awslogs-region = var.region, awslogs-stream-prefix = "task"
    } }
  }])
  tags = { SourceCommit = var.source_commit }
}
resource "aws_ecs_service" "runtime" {
  for_each                           = local.services
  name                               = "${local.name}-${each.key}"
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.runtime[each.key].arn
  desired_count                      = 0
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  enable_execute_command             = false
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.task[each.key].id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = contains(["api", "remote"], each.key) ? [each.key] : []
    content {
      target_group_arn = aws_lb_target_group.application[each.key].arn
      container_name   = each.key
      container_port   = 8000
    }
  }
  # Release tooling owns counts; infrastructure applies cannot reopen a quiesced restore.
  lifecycle { ignore_changes = [desired_count, task_definition] }
  depends_on = [aws_lb_listener.https, aws_lb_listener_rule.application, aws_iam_role_policy.task, aws_iam_role_policy.execution]
}
resource "aws_lb" "private" {
  name                       = local.name
  internal                   = true
  load_balancer_type         = "application"
  subnets                    = aws_subnet.private[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = true
}
resource "aws_lb_target_group" "application" {
  for_each             = toset(["api", "remote"])
  name                 = "${local.name}-${each.key}"
  port                 = 8000
  protocol             = "HTTP"
  vpc_id               = aws_vpc.this.id
  target_type          = "ip"
  deregistration_delay = 30
  health_check {
    path                = "/health"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }
}
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.private.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Unknown host"
      status_code  = "404"
    }
  }
}
resource "aws_lb_listener_rule" "application" {
  for_each     = { api = "api", remote = "effects" }
  listener_arn = aws_lb_listener.https.arn
  priority     = each.key == "api" ? 10 : 20
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.application[each.key].arn
  }
  condition {
    host_header { values = ["${each.value}.${var.internal_domain}"] }
  }
}
resource "aws_route53_zone" "private" {
  name = var.internal_domain
  vpc { vpc_id = aws_vpc.this.id }
}
resource "aws_route53_record" "application" {
  for_each = toset(["api", "effects"])
  zone_id  = aws_route53_zone.private.zone_id
  name     = "${each.key}.${var.internal_domain}"
  type     = "A"
  alias {
    name                   = aws_lb.private.dns_name
    zone_id                = aws_lb.private.zone_id
    evaluate_target_health = true
  }
}
