package platform_standards.repository

import rego.v1

violations_for(cfg) := {msg |
  data.platform_standards.repository.deny[msg] with input as cfg
}

test_delete_branch_on_merge_required if {
  cfg := {
    "resource_changes": [
      {
        "address": "module.repository.github_repository.this",
        "type": "github_repository",
        "change": {
          "after": {
            "delete_branch_on_merge": true,
            "allow_merge_commit": false,
            "allow_rebase_merge": false,
            "allow_squash_merge": true
          }
        }
      }
    ]
  }

  count(violations_for(cfg)) == 0
}

test_all_merge_modes_disabled_raises_violation if {
  cfg := {
    "resource_changes": [
      {
        "address": "module.repository.github_repository.this",
        "type": "github_repository",
        "change": {
          "after": {
            "delete_branch_on_merge": true,
            "allow_merge_commit": false,
            "allow_rebase_merge": false,
            "allow_squash_merge": false
          }
        }
      }
    ]
  }

  violations := violations_for(cfg)
  repo := "module.repository.github_repository.this"
  expected_all := violation_message("all_merge_strategies_disabled", repo)
  expected_squash := violation_message("squash_merge_disabled", repo)
  violations[expected_all]
  violations[expected_squash]
  count(violations) == 2
}

test_delete_branch_on_merge_violation_message if {
  cfg := {
    "resource_changes": [
      {
        "address": "github_repository.core",
        "type": "github_repository",
        "change": {
          "after": {
            "delete_branch_on_merge": false,
            "allow_merge_commit": false,
            "allow_rebase_merge": false,
            "allow_squash_merge": true
          }
        }
      }
    ]
  }

  violations := violations_for(cfg)
  expected := violation_message("delete_branch_disabled", "github_repository.core")
  violations[expected]
  count(violations) == 1
}

test_merge_commit_violation_message if {
  cfg := {
    "resource_changes": [
      {
        "address": "github_repository.core",
        "type": "github_repository",
        "change": {
          "after": {
            "delete_branch_on_merge": true,
            "allow_merge_commit": true,
            "allow_rebase_merge": false,
            "allow_squash_merge": true
          }
        }
      }
    ]
  }

  violations := violations_for(cfg)
  expected := violation_message("merge_commit_enabled", "github_repository.core")
  violations[expected]
  count(violations) == 1
}

test_rebase_merge_violation_message if {
  cfg := {
    "resource_changes": [
      {
        "address": "github_repository.core",
        "type": "github_repository",
        "change": {
          "after": {
            "delete_branch_on_merge": true,
            "allow_merge_commit": false,
            "allow_rebase_merge": true,
            "allow_squash_merge": true
          }
        }
      }
    ]
  }

  violations := violations_for(cfg)
  expected := violation_message("rebase_merge_enabled", "github_repository.core")
  violations[expected]
  count(violations) == 1
}

test_squash_merge_disabled_violation_message if {
  cfg := {
    "resource_changes": [
      {
        "address": "github_repository.core",
        "type": "github_repository",
        "change": {
          "after": {
            "delete_branch_on_merge": true,
            "allow_merge_commit": false,
            "allow_rebase_merge": false,
            "allow_squash_merge": false
          }
        }
      }
    ]
  }

  violations := violations_for(cfg)
  expected := violation_message("squash_merge_disabled", "github_repository.core")
  violations[expected]
}
