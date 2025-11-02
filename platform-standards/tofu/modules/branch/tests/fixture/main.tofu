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
  source = "../../repository"

  name        = "branch-fixture"
  visibility  = "private"
  topics      = ["fixture"]
}

module "branch" {
  source = "../.."

  repository_node_id = module.repository.repository_node_id
  pattern            = "main"
  status_checks = {
    contexts = ["ci/smoke"]
  }
}
