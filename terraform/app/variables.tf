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

variable "platform_repo_full_name" {
  description = "Full name of the platform repo (owner/repo)"
  type        = string
  default     = "provision-demo-platform"
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

variable "anthropic_api_key" {
  description = "Anthropic API key for Claude chat feature"
  type        = string
  sensitive   = true
}
