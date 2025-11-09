package platform_standards.repository

import rego.v1

merge_strategy_requirements := {
  "allow_squash_merge": true,
  "allow_merge_commit": false,
  "allow_rebase_merge": false,
}

merge_strategy_template_ids := {
  "allow_squash_merge": "squash_merge_disabled",
  "allow_merge_commit": "merge_commit_enabled",
  "allow_rebase_merge": "rebase_merge_enabled",
}

violation_templates := {
  "delete_branch_disabled": "repository %s disables delete_branch_on_merge",
  "all_merge_strategies_disabled": "repository %s disables all human merge strategies",
  "squash_merge_disabled": "repository %s disables squash merging, which the platform standard requires",
  "merge_commit_enabled": "repository %s enables merge commits, which are disallowed",
  "rebase_merge_enabled": "repository %s enables rebase merges, which are disallowed",
}

violation_message(template_id, address) := sprintf(violation_templates[template_id], [address]) if {
  violation_templates[template_id]
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_repository"
  delete_branch := bool_setting(change, "delete_branch_on_merge", true)
  not delete_branch
  msg := violation_message("delete_branch_disabled", change.address)
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_repository"
  no_merge_strategies_enabled(change)
  msg := violation_message("all_merge_strategies_disabled", change.address)
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_repository"
  merge_strategy_requirements[attr_key]
  expected := merge_strategy_requirements[attr_key]
  actual := bool_setting(change, attr_key, expected)
  actual != expected
  template_id := merge_strategy_template_ids[attr_key]
  msg := violation_message(template_id, change.address)
}

bool_setting(change, attr, fallback) := result if {
  after := object.get(change.change, "after", {})
  value := object.get(after, attr, fallback)
  result := coalesce_null(value, fallback)
}

coalesce_null(value, fallback) := value if {
  value != null
}

coalesce_null(value, fallback) := fallback if {
  value == null
}

no_merge_strategies_enabled(change) if {
  bool_setting(change, "allow_merge_commit", false) == false
  bool_setting(change, "allow_rebase_merge", false) == false
  bool_setting(change, "allow_squash_merge", true) == false
}
