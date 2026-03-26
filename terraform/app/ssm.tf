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

resource "aws_ssm_parameter" "cognito_client_id" {
  name        = "/${var.app_name}/cognito-client-id"
  description = "Cognito User Pool Client ID"
  type        = "String"
  value       = aws_cognito_user_pool_client.main.id
}

resource "aws_ssm_parameter" "app_url" {
  name        = "/${var.app_name}/app-url"
  description = "Application base URL (Lambda Function URL)"
  type        = "String"
  value       = aws_lambda_function_url.app.function_url
}
