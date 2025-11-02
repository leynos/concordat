package platform_standards.team

import rego.v1

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_team"
  permission := lower(change.change.after.default_repository_permission)
  not allowed_permission(permission)
  msg := sprintf("team %s uses disallowed default permission %s", [change.address, permission])
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_team_repository"
  permission := lower(change.change.after.permission)
  not allowed_permission(permission)
  msg := sprintf("team repo binding %s uses disallowed permission %s", [change.address, permission])
}

deny contains msg if {
  change := input.resource_changes[_]
  change.type == "github_team_membership"
  role := lower(change.change.after.role)
  not allowed_role(role)
  msg := sprintf("team membership %s uses unexpected role %s", [change.address, role])
}

allowed_permissions := {"pull", "triage", "push", "maintain", "admin"}

allowed_permission(p) if {
  allowed_permissions[p]
}

allowed_roles := {"maintainer", "member"}

allowed_role(r) if {
  allowed_roles[r]
}
