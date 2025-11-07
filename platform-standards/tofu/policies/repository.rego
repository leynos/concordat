package platform_standards.repository

import rego.v1

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_repository"
  coalesce(change.change.after.delete_branch_on_merge, false) == false
  msg := sprintf("repository %s disables delete_branch_on_merge", [change.address])
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_repository"
  merge_commit := coalesce(change.change.after.allow_merge_commit, false)
  rebase_merge := coalesce(change.change.after.allow_rebase_merge, false)
  squash_merge := coalesce(change.change.after.allow_squash_merge, false)
  merge_commit == false
  rebase_merge == false
  squash_merge == false
  msg := sprintf("repository %s disables all human merge strategies", [change.address])
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_repository"
  squash_merge := coalesce(change.change.after.allow_squash_merge, false)
  squash_merge == false
  msg := sprintf("repository %s disables squash merging, which the platform standard requires", [change.address])
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_repository"
  merge_commit := coalesce(change.change.after.allow_merge_commit, false)
  merge_commit == true
  msg := sprintf("repository %s enables merge commits, which are disallowed", [change.address])
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_repository"
  rebase_merge := coalesce(change.change.after.allow_rebase_merge, false)
  rebase_merge == true
  msg := sprintf("repository %s enables rebase merges, which are disallowed", [change.address])
}

coalesce(value, fallback) := value if {
  not is_null(value)
}

coalesce(value, fallback) := fallback if {
  is_null(value)
}
