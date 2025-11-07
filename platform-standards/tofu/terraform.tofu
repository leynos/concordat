terraform {
  required_version = "~> 1.10.0"

  required_providers {
    github = {
      source  = "hashicorp/github"
      version = "~> 6.3"
    }
  }
}

provider "github" {
  owner = var.github_owner
  token = var.github_token != "" ? var.github_token : null
}
