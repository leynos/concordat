locals {
  maintainer_usernames = distinct([for username in var.maintainers : trimspace(username)])
  member_usernames     = distinct([for username in var.members : trimspace(username)])
  parent_team_id       = trimspace(var.parent_team_id)
}

resource "github_team" "this" {
  name                       = var.name
  description                = var.description
  privacy                    = var.privacy
  parent_team_id             = local.parent_team_id != "" ? local.parent_team_id : null
  create_default_maintainer  = false
}

resource "github_team_membership" "maintainers" {
  for_each = toset(local.maintainer_usernames)

  team_id  = github_team.this.id
  username = each.value
  role     = "maintainer"
}

resource "github_team_membership" "members" {
  for_each = toset(setsubtract(local.member_usernames, local.maintainer_usernames))

  team_id  = github_team.this.id
  username = each.value
  role     = "member"
}

resource "github_team_repository" "default_permissions" {
  for_each = var.repository_permissions

  team_id    = github_team.this.id
  repository = each.key
  permission = each.value
}

output "team_slug" {
  description = "Team slug usable in GitHub branch protections and CODEOWNERS files."
  value       = github_team.this.slug
}

output "team_id" {
  description = "Team ID for downstream automation such as feature flag mapping."
  value       = github_team.this.id
}

output "repository_permissions" {
  description = "Resolved map of repositories to permissions for audit visibility."
  value       = var.repository_permissions
}

output "maintainer_usernames" {
  description = "Distinct maintainer usernames after deduplication, for validation and documentation."
  value       = local.maintainer_usernames
}
