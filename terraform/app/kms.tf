# WARNING: this key encrypts the committed connectors/*/secrets.enc.json files in
# the platform repo. It deliberately survives a teardown of this stack — the
# destroy workflow removes it from state rather than destroying it, because
# losing the key material would permanently orphan those files.
#
# If the stack has been torn down, IMPORT the key and alias before any apply,
# or Terraform will mint a duplicate key and fail on the existing alias.
# See docs/teardown-restore.md.

resource "aws_kms_key" "sops" {
  description             = "KMS key for SOPS encryption in ${var.app_name}"
  deletion_window_in_days = 14
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "LambdaEncrypt"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.lambda.arn
        }
        Action = [
          "kms:Encrypt",
          "kms:DescribeKey"
        ]
        Resource = "*"
      }
    ]
  })

  # Backstop for the invariant above: a bare `terraform destroy` that skipped the
  # detach-kms step fails here instead of scheduling the key for deletion. The
  # guarded teardown removes this resource from state first, so it is unaffected.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "sops" {
  name          = "alias/${var.app_name}-sops"
  target_key_id = aws_kms_key.sops.key_id
}
