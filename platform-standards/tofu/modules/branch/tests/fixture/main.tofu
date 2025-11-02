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

module "branch" {
  source = "../.."

  repository_node_id = "R_kgDOExample"
  pattern            = "main"
  status_checks = {
    contexts = ["ci/smoke"]
  }
}
