package platform_standards.branch

import rego.v1

violations_for(cfg) := {msg |
  data.platform_standards.branch.deny[msg] with input as cfg
}

test_branch_defaults_pass if {
  cfg := {
    "resource_changes": [
      {
        "address": "module.branch.github_branch_protection.this",
        "type": "github_branch_protection",
        "change": {
          "after": {
            "require_conversation_resolution": true,
            "required_status_checks": {
              "strict": true,
              "contexts": ["concordat/auditor"]
            },
            "required_pull_request_reviews": {
              "required_approving_review_count": 2
            }
          }
        }
      }
    ]
  }

  count(violations_for(cfg)) == 0
}

test_branch_requires_contexts_when_strict if {
  cfg := {
    "resource_changes": [
      {
        "address": "branch.main",
        "type": "github_branch_protection",
        "change": {
          "after": {
            "require_conversation_resolution": true,
            "required_status_checks": {
              "strict": true,
              "contexts": []
            },
            "required_pull_request_reviews": {
              "required_approving_review_count": 2
            }
          }
        }
      }
    ]
  }

  violations := violations_for(cfg)
  expected := "branch protection branch.main enforces strict status checks without contexts"
  violations[expected]
}

test_branch_requires_two_approvals if {
  cfg := {
    "resource_changes": [
      {
        "address": "branch.dev",
        "type": "github_branch_protection",
        "change": {
          "after": {
            "require_conversation_resolution": true,
            "required_status_checks": {
              "strict": true,
              "contexts": ["ci/unit"]
            },
            "required_pull_request_reviews": {
              "required_approving_review_count": 1
            }
          }
        }
      }
    ]
  }

  violations := violations_for(cfg)
  expected := "branch protection branch.dev requires fewer than two approvals"
  violations[expected]
}
