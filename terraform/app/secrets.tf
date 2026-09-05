resource "aws_secretsmanager_secret" "github_app_key" {
  name        = "${var.app_name}/github-app-private-key"
  description = "GitHub App private key for ${var.app_name}"

  # Deleted immediately rather than held for 30 days, so the stack can be
  # torn down and re-applied on the same names. See docs/teardown-restore.md.
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "github_app_key" {
  secret_id     = aws_secretsmanager_secret.github_app_key.id
  secret_string = var.github_app_private_key_base64
}

resource "aws_secretsmanager_secret" "age_secret_key" {
  name        = "${var.app_name}/age-secret-key"
  description = "Age secret key for decrypting payloads"

  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "age_secret_key" {
  secret_id     = aws_secretsmanager_secret.age_secret_key.id
  secret_string = var.age_secret_key
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "${var.app_name}/anthropic-api-key"
  description = "Anthropic API key for Claude chat feature"

  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "anthropic_api_key" {
  secret_id     = aws_secretsmanager_secret.anthropic_api_key.id
  secret_string = var.anthropic_api_key
}
