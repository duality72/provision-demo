output "lambda_function_arn" {
  description = "ARN of the Lambda function"
  value       = aws_lambda_function.app.arn
}

output "function_url" {
  description = "Lambda Function URL"
  value       = aws_lambda_function_url.app.function_url
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "Cognito User Pool Client ID"
  value       = aws_cognito_user_pool_client.main.id
}

output "cognito_domain" {
  description = "Cognito hosted UI domain"
  value       = "https://${var.cognito_domain_prefix}.auth.${var.aws_region}.amazoncognito.com"
}

output "kms_key_arn" {
  description = "KMS key ARN for SOPS encryption"
  value       = aws_kms_key.sops.arn
}
