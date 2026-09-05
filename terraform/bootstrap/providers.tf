terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # State lives in the bucket this stack manages. Safe because the bucket and
  # lock table already exist; prevent_destroy on both stops a destroy from
  # orphaning this state.
  backend "s3" {
    bucket         = "provision-demo-tfstate"
    key            = "provision-demo/bootstrap/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "provision-demo-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
}
