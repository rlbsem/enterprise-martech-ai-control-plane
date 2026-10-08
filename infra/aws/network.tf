resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = local.name }
}
resource "aws_subnet" "private" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.name}-private-${count.index}" }
}
resource "aws_route_table" "private" { vpc_id = aws_vpc.this.id }
resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
resource "aws_security_group" "task" {
  for_each    = local.tasks
  name        = "${local.name}-${each.key}"
  description = "Explicit egress and ingress for ${each.key}"
  vpc_id      = aws_vpc.this.id
}
resource "aws_security_group" "database" {
  for_each    = toset(["control", "downstream"])
  name        = "${local.name}-db-${each.key}"
  description = "Private PostgreSQL ${each.key}"
  vpc_id      = aws_vpc.this.id
}
resource "aws_security_group" "endpoint" {
  name        = "${local.name}-endpoints"
  description = "AWS private service endpoints"
  vpc_id      = aws_vpc.this.id
}
resource "aws_security_group" "alb" {
  name        = "${local.name}-ingress"
  description = "Private TLS boundary"
  vpc_id      = aws_vpc.this.id
}
resource "aws_vpc_security_group_ingress_rule" "client" {
  for_each          = var.client_cidrs
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_ingress_rule" "worker_https" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.task["worker"].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_egress_rule" "worker_https" {
  security_group_id            = aws_security_group.task["worker"].id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_ingress_rule" "application" {
  for_each                     = toset(["api", "remote"])
  security_group_id            = aws_security_group.task[each.key].id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}
resource "aws_vpc_security_group_egress_rule" "application" {
  for_each                     = toset(["api", "remote"])
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.task[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}
locals {
  database_access = { api = ["control"], worker = ["control"], remote = ["downstream"],
  observer = ["control"], verifier = ["control", "downstream"], migration = ["control", "downstream"] }
  db_edges = { for pair in flatten([for task, databases in local.database_access : [for database in databases : {
    key = "${task}-${database}", task = task, database = database
  }]]) : pair.key => pair }
}
resource "aws_vpc_security_group_ingress_rule" "database" {
  for_each                     = local.db_edges
  security_group_id            = aws_security_group.database[each.value.database].id
  referenced_security_group_id = aws_security_group.task[each.value.task].id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_vpc_security_group_egress_rule" "database" {
  for_each                     = local.db_edges
  security_group_id            = aws_security_group.task[each.value.task].id
  referenced_security_group_id = aws_security_group.database[each.value.database].id
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
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoint.id]
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = "*",
  Action = ["s3:GetObject"], Resource = ["arn:${local.partition}:s3:::prod-${var.region}-starport-layer-bucket/*"] }] })
}
resource "aws_vpc_security_group_egress_rule" "ecr_layers" {
  for_each          = local.tasks
  security_group_id = aws_security_group.task[each.key].id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
