package platform_standards.branch

import rego.v1

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_branch_protection"
  coalesce(change.change.after.requires_conversation_resolution, false) == false
  msg := sprintf("branch protection %s must require conversation resolution", [change.address])
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_branch_protection"
  coalesce(change.change.after.required_status_checks.strict, false)
  count(coalesce(change.change.after.required_status_checks.contexts, [])) == 0
  msg := sprintf("branch protection %s enforces strict status checks without contexts", [change.address])
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_branch_protection"
  approvals := coalesce(change.change.after.required_pull_request_reviews.required_approving_review_count, 0)
  approvals < 2
  msg := sprintf("branch protection %s requires fewer than two approvals", [change.address])
}

coalesce(value, fallback) := value if {
  not is_null(value)
}

coalesce(value, fallback) := fallback if {
  is_null(value)
}
