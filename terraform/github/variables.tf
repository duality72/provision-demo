variable "github_token" {
  description = "GitHub personal access token with repo and admin:org scope"
  type        = string
  sensitive   = true
}

variable "github_owner" {
  description = "GitHub organization or user that owns the repos"
  type        = string
}

variable "platform_repo_name" {
  description = "Name of the platform repository"
  type        = string
  default     = "provision-demo-platform"
}

variable "age_secret_key" {
  description = "Age secret key for decrypting payloads in GitHub Actions"
  type        = string
  sensitive   = true
}

variable "sops_kms_arn" {
  description = "ARN of the KMS key used for SOPS encryption"
  type        = string
}

variable "aws_role_arn" {
  description = "ARN of the IAM role for GitHub Actions OIDC"
  type        = string
}

variable "github_app_installation_id" {
  description = "GitHub App installation ID"
  type        = string
}

variable "demo_repo_name" {
  description = "Name of the provision-demo repository"
  type        = string
  default     = "provision-demo"
}

variable "demo_ci_aws_role_arn" {
  description = "IAM role ARN for provision-demo CI (from bootstrap)"
  type        = string
}
