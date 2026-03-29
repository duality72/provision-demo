data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.module}/lambda.zip"
}

resource "aws_lambda_function" "app" {
  function_name    = var.app_name
  role             = aws_iam_role.lambda.arn
  handler          = "dispatch.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  layers = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.main.id
      COGNITO_DOMAIN       = "${var.cognito_domain_prefix}.auth.${var.aws_region}.amazoncognito.com"
      APP_NAME             = var.app_name
    }
  }
}

resource "aws_lambda_layer_version" "deps" {
  filename            = "${path.module}/lambda-layer.zip"
  layer_name          = "${var.app_name}-deps"
  compatible_runtimes = ["python3.12"]
  source_code_hash    = filebase64sha256("${path.module}/lambda-layer.zip")
  description         = "PyJWT and cryptography dependencies"
}

resource "aws_lambda_function_url" "app" {
  function_name      = aws_lambda_function.app.function_name
  authorization_type = "NONE"

  cors {
    allow_origins     = ["*"]
    allow_methods     = ["*"]
    allow_headers     = ["content-type", "authorization"]
    max_age           = 3600
  }
}

resource "aws_lambda_permission" "function_url" {
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.app.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "function_url_invoke" {
  statement_id  = "FunctionURLAllowPublicInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "*"
}
