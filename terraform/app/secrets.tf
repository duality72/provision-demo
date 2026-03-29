resource "aws_secretsmanager_secret" "github_app_key" {
  name        = "${var.app_name}/github-app-private-key"
  description = "GitHub App private key for ${var.app_name}"
}

resource "aws_secretsmanager_secret_version" "github_app_key" {
  secret_id     = aws_secretsmanager_secret.github_app_key.id
  secret_string = var.github_app_private_key_base64
}

resource "aws_secretsmanager_secret" "age_secret_key" {
  name        = "${var.app_name}/age-secret-key"
  description = "Age secret key for decrypting payloads"
}

resource "aws_secretsmanager_secret_version" "age_secret_key" {
  secret_id     = aws_secretsmanager_secret.age_secret_key.id
  secret_string = var.age_secret_key
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "${var.app_name}/anthropic-api-key"
  description = "Anthropic API key for Claude chat feature"
}

resource "aws_secretsmanager_secret_version" "anthropic_api_key" {
  secret_id     = aws_secretsmanager_secret.anthropic_api_key.id
  secret_string = var.anthropic_api_key
}
