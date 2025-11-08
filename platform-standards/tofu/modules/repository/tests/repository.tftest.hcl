mock_provider "github" {
  alias = "mock"
}

run "repository_defaults" {
  command = plan

  providers = {
    github = github.mock
  }

  variables {
    name        = "concordat-repo"
    visibility  = "internal"
    topics      = ["standards", "audit"]
    description = "Repository under test"
  }

  assert {
    condition     = github_repository.this.allow_squash_merge == true
    error_message = "squash merge should remain enabled by default"
  }

  assert {
    condition = (
      github_repository.this.allow_merge_commit == false &&
      github_repository.this.allow_rebase_merge == false
    )
    error_message = "merge commits and rebase merges must be disabled by default"
  }
}

run "repository_apply_smoke" {
  command = apply

  providers = {
    github = github.mock
  }

  variables {
    name        = "concordat-repo"
    visibility  = "internal"
    topics      = ["standards", "audit"]
    description = "Repository under test"
  }

  assert {
    condition     = github_repository.this.allow_squash_merge == true
    error_message = "squash merge must stay enabled after apply"
  }

  assert {
    condition = (
      github_repository.this.allow_merge_commit == false &&
      github_repository.this.allow_rebase_merge == false
    )
    error_message = "merge commits and rebase merges must stay disabled after apply"
  }
}
