mock_provider "github" {
  alias = "mock"
}

run "branch_default_rules" {
  command = plan

  providers = {
    github = github.mock
  }

  module {
    source = "./.."
  }

  variables {
    repository_node_id = "R_kgDOExample"
    pattern            = "main"
    status_checks = {
      strict   = true
      contexts = ["concordat/auditor", "ci/unit-tests"]
    }
  }

}
