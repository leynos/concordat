mock_provider "github" {
  alias = "mock"
}

run "team_baseline" {
  command = plan

  providers = {
    github = github.mock
  }

  module {
    source = "./.."
  }

  variables {
    name        = "platform-standards"
    description = "Owns Concordat platform controls"
    maintainers = ["alice", "bob"]
    members     = ["carol", "dave", "alice"]
    repository_permissions = {
      "standards-repo" = "maintain"
      "auditor"        = "push"
    }
  }

}
