terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Bootstrap state is local — this creates the remote backend for everything else
}

provider "aws" {
  region = var.aws_region
}
