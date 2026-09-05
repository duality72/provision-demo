# Containers only. Values are populated by the Populate secrets step in
# terraform-apply.yml, decrypted from terraform/app/secrets.enc.json, so no
# secret material enters Terraform state.
#
# recovery_window_in_days = 0 so a teardown can be reversed immediately rather
# than being blocked for 30 days. See docs/teardown-restore.md.

resource "aws_secretsmanager_secret" "github_app_key" {
  name        = "${var.app_name}/github-app-private-key"
  description = "GitHub App private key for ${var.app_name}"

  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "${var.app_name}/anthropic-api-key"
  description = "Anthropic API key for Claude chat feature"

  recovery_window_in_days = 0
}
