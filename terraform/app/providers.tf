terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_lb" "main" {
  arn = var.alb_arn
}

data "aws_lb_listener" "https" {
  arn = var.alb_listener_arn
}

data "aws_route53_zone" "main" {
  name = var.hosted_zone_name
}
