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

module "team" {
  source = "../.."

  name        = "platform-standards"
  description = "Team fixture"
  maintainers = ["alice"]
  members     = ["alice", "bob"]
  repository_permissions = {
    "fixture-repo" = "push"
  }
}
