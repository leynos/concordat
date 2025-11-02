terraform {
  required_providers {
    github = {
      source  = "hashicorp/github"
      version = "~> 6.3"
    }
  }
}

provider "github" {
  token = "placeholder"
  owner = "platform"
}

module "repository" {
  source = "../.."

  name        = "fixture-repo"
  description = "Fixture for Terratest"
  visibility  = "private"
  topics      = ["fixture"]
}
