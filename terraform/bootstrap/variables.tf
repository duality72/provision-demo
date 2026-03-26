variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "github_owner" {
  description = "GitHub organization or user that owns the repos"
  type        = string
}

variable "state_bucket_name" {
  description = "S3 bucket name for Terraform state"
  type        = string
  default     = "provision-demo-tfstate"
}

variable "state_lock_table_name" {
  description = "DynamoDB table name for Terraform state locking"
  type        = string
  default     = "provision-demo-tflock"
}

variable "provision_demo_repo_name" {
  description = "Name of the provision-demo repository"
  type        = string
  default     = "provision-demo"
}

variable "platform_repo_name" {
  description = "Name of the provision-demo-platform repository"
  type        = string
  default     = "provision-demo-platform"
}
