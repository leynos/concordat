package platform_standards.team

import rego.v1

violations_for(cfg) := {msg |
  data.platform_standards.team.deny[msg] with input as cfg
}

test_team_default_permission_allowed if {
  cfg := {
    "resource_changes": [
      {
        "address": "module.team.github_team.this",
        "type": "github_team",
        "change": {
          "after": {
            "default_repository_permission": "pull"
          }
        }
      },
      {
        "address": "module.team.github_team_repository.default_permissions[\"repo\"]",
        "type": "github_team_repository",
        "change": {
          "after": {
            "permission": "maintain"
          }
        }
      }
    ]
  }

  count(violations_for(cfg)) == 0
}

test_team_rejects_unknown_permission if {
  cfg := {
    "resource_changes": [
      {
        "address": "team.repo",
        "type": "github_team_repository",
        "change": {
          "after": {
            "permission": "owner"
          }
        }
      }
    ]
  }

  violations := violations_for(cfg)
  expected := "team repo binding team.repo uses disallowed permission owner"
  violations[expected]
}
