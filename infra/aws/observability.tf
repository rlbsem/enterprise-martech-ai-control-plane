locals {
  database_identifiers = {
    control    = coalesce(var.restored_control_identifier, aws_db_instance.state["control"].identifier)
    downstream = aws_db_instance.state["downstream"].identifier
  }
  alarms = {
    backlog_age = { metric = "EligibleAgeSeconds", service = "observer", threshold = 120, statistic = "Maximum", missing = "breaching" }
    uncertainty = { metric = "Uncertain", service = "observer", threshold = 20, statistic = "Maximum", missing = "missing" }
    dead        = { metric = "Dead", service = "observer", threshold = 0, statistic = "Maximum", missing = "missing" }
    worker      = { metric = "WorkerPoll", service = "worker", threshold = 1, statistic = "Sum", missing = "breaching" }
    api_errors  = { metric = "ApiError", service = "api", threshold = 5, statistic = "Sum", missing = "notBreaching" }
    dependency  = { metric = "DependencyFailure", service = "worker", threshold = 5, statistic = "Sum", missing = "notBreaching" }
  }
}
resource "aws_cloudwatch_metric_alarm" "control" {
  for_each            = local.alarms
  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = "See docs/cloud-operations.md; drain states intentionally silence service-heartbeat expectations."
  namespace           = "EnterpriseControl"
  metric_name         = each.value.metric
  dimensions          = { Environment = var.environment, Service = each.value.service }
  statistic           = each.value.statistic
  period              = 60
  evaluation_periods  = 3
  threshold           = each.value.threshold
  comparison_operator = each.key == "worker" ? "LessThanThreshold" : "GreaterThanThreshold"
  treat_missing_data  = each.value.missing
  alarm_actions       = [var.alarm_topic_arn]
  ok_actions          = [var.alarm_topic_arn]
}
resource "aws_cloudwatch_metric_alarm" "database" {
  for_each            = local.database_identifiers
  alarm_name          = "${local.name}-${each.key}-storage"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = each.value }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 5368709120
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [var.alarm_topic_arn]
  treat_missing_data  = "missing"
}
resource "aws_cloudwatch_metric_alarm" "target" {
  for_each            = aws_lb_target_group.application
  alarm_name          = "${local.name}-${each.key}-unhealthy"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { TargetGroup = each.value.arn_suffix, LoadBalancer = aws_lb.private.arn_suffix }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [var.alarm_topic_arn]
  treat_missing_data  = "missing"
}
resource "aws_cloudwatch_dashboard" "operations" {
  dashboard_name = local.name
  dashboard_body = jsonencode({ widgets = [
    { type = "metric", x = 0, y = 0, width = 12, height = 6, properties = {
      title   = "Eligible work age and unresolved outcomes", region = var.region, period = 60, stat = "Maximum",
      metrics = [for metric in ["EligibleAgeSeconds", "Backlog", "Uncertain", "Dead"] : ["EnterpriseControl", metric, "Environment", var.environment, "Service", "observer"]]
    } },
    { type = "metric", x = 12, y = 0, width = 12, height = 6, properties = {
      title   = "Worker decisions and dependency health", region = var.region, period = 60, stat = "Sum",
      metrics = [for metric in ["WorkerPoll", "Retry", "DependencyFailure", "Reconciled", "RuntimeError"] : ["EnterpriseControl", metric, "Environment", var.environment, "Service", "worker"]]
    } },
    { type = "metric", x = 0, y = 6, width = 24, height = 6, properties = {
      title   = "PostgreSQL connections", region = var.region, period = 60, stat = "Average",
      metrics = [for identifier in local.database_identifiers : ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", identifier]]
    } }
  ] })
}
