provider "github" {
  owner = var.github_owner
  token = var.github_token != "" ? var.github_token : null
}
