variable "region" {
  type    = string
  default = "ca-central-1"
  validation {
    condition     = var.region == "ca-central-1"
    error_message = "This deployment boundary is reviewed for ca-central-1. Review networking, pricing and IAM before another region."
  }
}
variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "stage", "prod"], var.environment)
    error_message = "Use a separate state/account per dev, stage or prod environment."
  }
}
variable "image_digest" {
  type = string
  validation {
    condition     = can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
    error_message = "Supply an immutable ECR digest, never a mutable tag."
  }
}
variable "source_commit" {
  type = string
  validation {
    condition     = can(regex("^[a-f0-9]{40}$", var.source_commit))
    error_message = "Supply the full source commit corresponding to the image."
  }
}
variable "certificate_arn" {
  type        = string
  description = "Existing ACM certificate for api.<domain> and effects.<domain>, trusted by clients and the worker."
  validation {
    condition     = can(regex("^arn:aws:acm:ca-central-1:[0-9]{12}:certificate/", var.certificate_arn))
    error_message = "Supply a certificate in the deployment region."
  }
}
variable "internal_domain" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]+\\.[a-z]{2,}$", var.internal_domain))
    error_message = "Use an enterprise-owned DNS domain covered by the ACM certificate."
  }
}
variable "client_cidrs" {
  type        = set(string)
  description = "Routed corporate/VPN CIDRs permitted to reach the private HTTPS listener."
  validation {
    condition     = length(var.client_cidrs) > 0 && alltrue([for c in var.client_cidrs : can(cidrhost(c, 0)) && try(tonumber(split("/", c)[1]) >= 16, false)])
    error_message = "Use explicit IPv4 client ranges /16 or narrower, never public universal ingress."
  }
}
variable "vpc_cidr" { default = "10.42.0.0/16" }
variable "db_class" { default = "db.t4g.medium" }
variable "restored_control_identifier" {
  type        = string
  default     = null
  description = "Existing, reviewed PITR instance to bind while the original is retained. Never restore downstream with it."
}
variable "multi_az" { default = true }
variable "alarm_topic_arn" {
  type        = string
  description = "Existing SNS on-call topic with confirmed subscriptions."
}
variable "github_oidc_provider_arn" {
  type        = string
  description = "Account-level GitHub OIDC provider, created once by the foundation administrator."
}
variable "github_oidc_subject" {
  type        = string
  description = "Exact protected-environment subject from the repository's OIDC configuration. No wildcard."
  validation {
    condition     = startswith(var.github_oidc_subject, "repo:rlbsem/enterprise-martech-ai-control-plane:") && endswith(var.github_oidc_subject, ":environment:aws-release") && !strcontains(var.github_oidc_subject, "*")
    error_message = "Trust only this repository's protected aws-release environment subject."
  }
}
