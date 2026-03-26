resource "aws_ssm_parameter" "age_public_key" {
  name        = "/${var.app_name}/age-public-key"
  description = "Age public key for encrypting payloads"
  type        = "String"
  value       = var.age_public_key
}

resource "aws_ssm_parameter" "github_app_id" {
  name        = "/${var.app_name}/github-app-id"
  description = "GitHub App ID"
  type        = "String"
  value       = var.github_app_id
}

resource "aws_ssm_parameter" "platform_repo" {
  name        = "/${var.app_name}/platform-repo"
  description = "Full name of the platform repository"
  type        = "String"
  value       = var.platform_repo_full_name
}

resource "aws_ssm_parameter" "github_app_installation_id" {
  name        = "/${var.app_name}/github-app-installation-id"
  description = "GitHub App installation ID"
  type        = "String"
  value       = var.github_app_installation_id
}
