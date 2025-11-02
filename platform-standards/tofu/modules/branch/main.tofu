locals {
  status_checks = {
    strict   = try(var.status_checks.strict, true)
    contexts = distinct([for ctx in try(var.status_checks.contexts, []) : trimspace(ctx)])
  }

  pull_request_reviews = {
    required_approvals         = try(var.pull_request_reviews.required_approvals, 2)
    dismiss_stale_reviews      = try(var.pull_request_reviews.dismiss_stale_reviews, true)
    require_code_owner_reviews = try(var.pull_request_reviews.require_code_owner_reviews, true)
  }

  restrictions = {
    teams = distinct([for team in try(var.restrictions.teams, []) : trimspace(team)])
    users = distinct([for user in try(var.restrictions.users, []) : trimspace(user)])
  }
}

resource "github_branch_protection" "this" {
  repository_id                     = var.repository_node_id
  pattern                           = var.pattern
  enforce_admins                    = var.enforce_admins
  require_signed_commits            = var.require_signed_commits
  required_linear_history           = var.required_linear_history
  require_conversation_resolution   = var.require_conversation_resolution
  allows_deletions                  = var.allows_deletions
  allows_force_pushes               = var.allows_force_pushes

  required_status_checks {
    strict   = local.status_checks.strict
    contexts = local.status_checks.contexts
  }

  required_pull_request_reviews {
    dismiss_stale_reviews           = local.pull_request_reviews.dismiss_stale_reviews
    require_code_owner_reviews      = local.pull_request_reviews.require_code_owner_reviews
    required_approving_review_count = local.pull_request_reviews.required_approvals
  }

  lifecycle {
    precondition {
      condition     = length(local.restrictions.teams) == 0 && length(local.restrictions.users) == 0
      error_message = "Push restrictions are not yet implemented; leave restrictions empty."
    }
    precondition {
      condition     = !local.status_checks.strict || length(local.status_checks.contexts) > 0
      error_message = "Provide at least one status check context when strict enforcement is enabled."
    }
  }
}

output "branch_pattern" {
  description = "Branch pattern protected by the module, surfaced for documentation."
  value       = github_branch_protection.this.pattern
}

output "enforces_conversation_resolution" {
  description = "Expose whether conversation resolution is enforced for policy tests."
  value       = github_branch_protection.this.require_conversation_resolution
}

output "required_approvals" {
  description = "Number of required reviews enforced on the protected branch."
  value       = local.pull_request_reviews.required_approvals
}
