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
    condition     = output.merge_preferences.allow_squash_merge == true
    error_message = "squash merge must stay enabled during apply"
  }

  assert {
    condition = (
      output.merge_preferences.allow_merge_commit == false &&
      output.merge_preferences.allow_rebase_merge == false
    )
    error_message = "merge commits and rebase merges must stay disabled"
  }
}
