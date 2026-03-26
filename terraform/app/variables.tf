variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "app_name" {
  description = "Application name used for resource naming"
  type        = string
  default     = "provision-demo"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

variable "alb_arn" {
  description = "ARN of the existing Application Load Balancer"
  type        = string
}

variable "alb_listener_arn" {
  description = "ARN of the existing ALB HTTPS listener"
  type        = string
}

variable "hosted_zone_name" {
  description = "Route53 hosted zone name (e.g., example.com)"
  type        = string
}

variable "app_domain_name" {
  description = "Domain name for the application (e.g., provision.example.com)"
  type        = string
}

variable "github_app_id" {
  description = "GitHub App ID"
  type        = string
}

variable "github_app_private_key_base64" {
  description = "Base64-encoded GitHub App private key (PEM)"
  type        = string
  sensitive   = true
}

variable "age_public_key" {
  description = "Age public key for encrypting payloads"
  type        = string
}

variable "age_secret_key" {
  description = "Age secret key for decrypting payloads"
  type        = string
  sensitive   = true
}

variable "cognito_callback_urls" {
  description = "Allowed callback URLs for Cognito"
  type        = list(string)
  default     = []
}

variable "platform_repo_full_name" {
  description = "Full name of the platform repo (owner/repo)"
  type        = string
  default     = "provision-demo-platform"
}

variable "vpc_id" {
  description = "VPC ID for the ALB target group"
  type        = string
}

variable "cognito_domain_prefix" {
  description = "Prefix for the Cognito hosted UI domain"
  type        = string
  default     = "provision-demo"
}

variable "github_app_installation_id" {
  description = "GitHub App installation ID"
  type        = string
}
