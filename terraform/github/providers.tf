terraform {
  required_version = ">= 1.5.0"

  backend "s3" {
    bucket         = "provision-demo-tfstate"
    key            = "provision-demo/github/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "provision-demo-tflock"
    encrypt        = true
  }

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
}

provider "github" {
  token = var.github_token
  owner = var.github_owner
}
