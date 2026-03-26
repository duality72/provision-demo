data "github_repository" "platform" {
  full_name = "${var.github_owner}/${var.platform_repo_name}"
}

resource "github_actions_secret" "age_secret_key" {
  repository      = var.platform_repo_name
  secret_name     = "AGE_SECRET_KEY"
  plaintext_value = var.age_secret_key
}

resource "github_actions_secret" "sops_kms_arn" {
  repository      = var.platform_repo_name
  secret_name     = "SOPS_KMS_ARN"
  plaintext_value = var.sops_kms_arn
}

resource "github_actions_secret" "aws_role_arn" {
  repository      = var.platform_repo_name
  secret_name     = "AWS_ROLE_ARN"
  plaintext_value = var.aws_role_arn
}

resource "github_app_installation_repository" "platform" {
  installation_id = var.github_app_installation_id
  repository      = var.platform_repo_name
}
