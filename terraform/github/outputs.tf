output "platform_repo_secrets" {
  description = "GitHub Actions secrets configured on the platform repo"
  value = [
    github_actions_secret.age_secret_key.secret_name,
    github_actions_secret.sops_kms_arn.secret_name,
    github_actions_secret.aws_role_arn.secret_name,
  ]
}

output "app_installation_repo" {
  description = "Repository with GitHub App installation"
  value       = github_app_installation_repository.platform.repository
}
