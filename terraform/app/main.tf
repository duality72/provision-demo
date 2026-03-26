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
  timeout          = 30
  memory_size      = 256
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  layers = [aws_lambda_layer_version.deps.arn]

  environment {
    variables = {
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.main.id
      COGNITO_CLIENT_ID    = aws_cognito_user_pool_client.main.id
      COGNITO_DOMAIN       = "${var.cognito_domain_prefix}.auth.${var.aws_region}.amazoncognito.com"
      APP_DOMAIN           = var.app_domain_name
      APP_NAME             = var.app_name
    }
  }
}

resource "aws_lambda_layer_version" "deps" {
  filename            = "${path.module}/lambda-layer.zip"
  layer_name          = "${var.app_name}-deps"
  compatible_runtimes = ["python3.12"]
  description         = "PyJWT and cryptography dependencies"
}

resource "aws_lambda_permission" "alb" {
  statement_id  = "AllowALBInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.lambda.arn
}

resource "aws_lb_target_group" "lambda" {
  name        = var.app_name
  target_type = "lambda"
  vpc_id      = var.vpc_id
}

resource "aws_lb_target_group_attachment" "lambda" {
  target_group_arn = aws_lb_target_group.lambda.arn
  target_id        = aws_lambda_function.app.arn
  depends_on       = [aws_lambda_permission.alb]
}

resource "aws_lb_listener_rule" "app" {
  listener_arn = data.aws_lb_listener.https.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.lambda.arn
  }

  condition {
    host_header {
      values = [var.app_domain_name]
    }
  }
}
