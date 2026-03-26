data "github_repository" "platform" {
  full_name = "${var.github_owner}/${var.platform_repo_name}"
}

data "github_repository" "demo" {
  full_name = "${var.github_owner}/${var.demo_repo_name}"
}

# ---------------------------------------------------------------------------
# Platform repo: Actions secrets
# ---------------------------------------------------------------------------

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

# Note: GitHub App installation on repos is managed manually via the App settings.
# The PAT doesn't have permission to modify app installations.

# ---------------------------------------------------------------------------
# Demo repo: Actions secrets
# ---------------------------------------------------------------------------

resource "github_actions_secret" "demo_aws_role_arn" {
  repository      = var.demo_repo_name
  secret_name     = "AWS_ROLE_ARN"
  plaintext_value = var.demo_ci_aws_role_arn
}

# ---------------------------------------------------------------------------
# Branch protection: provision-demo
# ---------------------------------------------------------------------------

resource "github_branch_protection" "demo_main" {
  repository_id = data.github_repository.demo.node_id
  pattern       = "main"

  required_pull_request_reviews {
    required_approving_review_count = 1
  }

  required_status_checks {
    strict   = true
    contexts = ["plan-app", "plan-github"]
  }

  enforce_admins = false
}

# ---------------------------------------------------------------------------
# Branch protection: provision-demo-platform
# ---------------------------------------------------------------------------

resource "github_branch_protection" "platform_main" {
  repository_id = data.github_repository.platform.node_id
  pattern       = "main"

  required_pull_request_reviews {
    required_approving_review_count = 1
  }

  required_status_checks {
    strict   = true
    contexts = ["terraform-plan"]
  }

  enforce_admins = false
}
