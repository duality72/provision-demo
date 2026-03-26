output "state_bucket_name" {
  description = "S3 bucket name for Terraform state"
  value       = aws_s3_bucket.state.id
}

output "state_lock_table_name" {
  description = "DynamoDB table name for state locking"
  value       = aws_dynamodb_table.lock.name
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub Actions OIDC provider"
  value       = aws_iam_openid_connect_provider.github.arn
}

output "ci_role_arn_provision_demo" {
  description = "IAM role ARN for provision-demo CI"
  value       = aws_iam_role.ci_provision_demo.arn
}

output "ci_role_arn_platform" {
  description = "IAM role ARN for provision-demo-platform CI"
  value       = aws_iam_role.ci_platform.arn
}
