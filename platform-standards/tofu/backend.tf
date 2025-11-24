terraform {
  required_version = ">= 1.10.7, < 2.0.0"

  required_providers {
    github = {
      source  = "hashicorp/github"
      version = "~> 6.3"
    }
  }

  backend "s3" {}
}
