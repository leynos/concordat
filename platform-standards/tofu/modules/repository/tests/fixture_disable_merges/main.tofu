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

  name   = "fixture-repo"
  topics = ["fixture"]
  merge_strategies = {
    allow_merge_commit = false
    allow_rebase_merge = false
    allow_squash_merge = false
    allow_auto_merge   = false
  }
}
