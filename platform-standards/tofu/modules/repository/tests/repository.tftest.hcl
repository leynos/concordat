mock_provider "github" {
  alias = "mock"
}

run "repository_defaults" {
  command = plan

  providers = {
    github = github.mock
  }

  module {
    source = "./.."
  }

  variables {
    name        = "concordat-repo"
    visibility  = "internal"
    topics      = ["standards", "audit"]
    description = "Repository under test"
  }

}
