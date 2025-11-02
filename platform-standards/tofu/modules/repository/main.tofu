locals {
  merge_defaults = {
    allow_merge_commit = false
    allow_rebase_merge = false
    allow_squash_merge = true
    allow_auto_merge   = false
  }

  sanitized_merge_preferences = {
    for key, value in var.merge_strategies :
    key => value if value != null
  }

  merge_preferences = merge(local.merge_defaults, local.sanitized_merge_preferences)

  enabled_release_paths = [
    for mode, enabled in local.merge_preferences :
    mode if enabled && mode != "allow_auto_merge"
  ]
}

resource "github_repository" "this" {
  name                   = var.name
  description            = var.description
  visibility             = var.visibility
  homepage_url           = var.homepage_url != "" ? var.homepage_url : null
  topics                 = var.topics
  has_issues             = var.has_issues
  has_projects           = var.has_projects
  has_discussions        = var.has_discussions
  delete_branch_on_merge = var.delete_branch_on_merge
  allow_merge_commit     = local.merge_preferences.allow_merge_commit
  allow_rebase_merge     = local.merge_preferences.allow_rebase_merge
  allow_squash_merge     = local.merge_preferences.allow_squash_merge
  allow_auto_merge       = local.merge_preferences.allow_auto_merge
  auto_init              = var.auto_init
  is_template            = var.is_template
  vulnerability_alerts   = var.vulnerability_alerts
  archive_on_destroy     = false
  default_branch         = var.default_branch != "" ? var.default_branch : null

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(local.enabled_release_paths) > 0
      error_message = "Enable at least one of merge_commit, rebase_merge, or squash_merge."
    }
  }
}

output "repository_name" {
  description = "Repository name for downstream modules such as branch protection."
  value       = github_repository.this.name
}

output "repository_node_id" {
  description = "Repository node ID required by the GraphQL-based branch protection resource."
  value       = github_repository.this.node_id
}

output "merge_preferences" {
  description = "Resolved merge strategy booleans after applying defaults and overrides."
  value       = local.merge_preferences
}

output "delete_branch_on_merge" {
  description = "Whether branches are deleted after merge; exposed for policy checks."
  value       = var.delete_branch_on_merge
}
