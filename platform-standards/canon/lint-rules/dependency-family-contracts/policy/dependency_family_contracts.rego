# DF-001 to DF-003 and DF-006: dependency relationships the estate has recorded
# are protected by the repository's Dependabot configuration.
#
# The Python envelope has already read every governed manifest and lock, decided
# for each declaration which Dependabot entry covers it and which group takes its
# published name first (document order matters; a Rego object keeps none), and
# loaded the relationship data and the repository's recorded exceptions. This
# policy only compares those facts. It opens nothing, runs no repository code and
# contacts no registry. A relationship applies only where the repository declares
# every member. Missing evidence is indeterminate, never compliant.
package canon.lint_rules.dependency_family_contracts

import rego.v1

finding(rule_id, verdict, path, message) := {
  "rule_id": rule_id,
  "severity": "warn",
  "verdict": verdict,
  "path": path,
  "line": 0,
  "msg": message,
}

envelope_ok if {
  input.schema_version == 1
  input.kind == "policy-input/dependency-family-contracts"
  is_array(input.relationships)
  is_array(input.declarations)
}

declarations_of(pkg) := [d |
  some d in input.declarations
  d["package"] == pkg
]

all_members_declared(rel) if {
  every member in rel.members {
    count(declarations_of(member)) > 0
  }
}

excepted(rel) if {
  some exception in input.exceptions
  exception.relationship == rel.id
}

applicable contains rel if {
  envelope_ok
  some rel in input.relationships
  all_members_declared(rel)
  not excepted(rel)
}

member_declarations(rel) := [d |
  some member in rel.members
  some d in declarations_of(member)
]

first_path(rel) := path if {
  decls := member_declarations(rel)
  path := sort([d.manifest | some d in decls])[0]
}

# The groups that take each member's declarations first, as a set of names.
taking_groups(rel) := {g |
  some d in member_declarations(rel)
  g := d.coverage.group
}

taken_names(rel) := {n |
  some d in member_declarations(rel)
  n := d.coverage.group
  n != null
}

group_names(rel) := sort(taken_names(rel)) if {
  count(taken_names(rel)) > 0
}

group_names(rel) := ["no group"] if {
  count(taken_names(rel)) == 0
}

uncovered(rel) := [d |
  some d in member_declarations(rel)
  d.coverage.reason != null
]

# True when one narrow group takes every member declaration first.
shared_narrow_group(rel) if {
  groups := taking_groups(rel)
  count(groups) == 1
  every d in member_declarations(rel) {
    d.coverage.group != null
    not d.coverage.catch_all
  }
}

# A narrow group that matches every member declaration, whether or not it takes
# them first.
matching_all_narrow(rel) := {g |
  some g in member_declarations(rel)[0].coverage.matching_narrow
  every d in member_declarations(rel) {
    g in d.coverage.matching_narrow
  }
}

group_family(rel) if {
  rel.kind in {"family", "type-identity"}
}

requirements(rel) := sort([text |
  some d in member_declarations(rel)
  text := sprintf("%s %s", [d["package"], d.requirement])
])

protected_tuple(rel) if {
  some d in member_declarations(rel)
  d.upper_bounded
}

protected_tuple(rel) if {
  some d in member_declarations(rel)
  d.coverage.ignored
}

deny contains finding("DF-006", "indeterminate", ".", msg) if {
  not envelope_ok
  msg := "policy input is not a policy-input/dependency-family-contracts envelope at schema version 1"
}

deny contains finding("DF-006", "indeterminate", ".", msg) if {
  envelope_ok
  some problem in input.problems
  msg := sprintf("dependency facts could not be established: %s", [problem])
}

deny contains finding("DF-006", "indeterminate", first_path(rel), msg) if {
  some rel in applicable
  count(uncovered(rel)) > 0
  reasons := {d.coverage.reason | some d in uncovered(rel)}
  msg := sprintf("%s cannot be judged: %s", [rel.id, concat("; ", sort(reasons))])
}

deny contains finding("DF-006", "indeterminate", first_path(rel), msg) if {
  some rel in applicable
  rel.kind == "type-identity"
  missing := {d["package"] | some d in member_declarations(rel); d.resolved.reason != null}
  count(missing) > 0
  msg := sprintf(
    "%s needs the resolved package identities, but the lock does not give them for %s",
    [rel.id, concat(", ", sort(missing))],
  )
}

# DF-001: a family or type-identity relationship whose members no single narrow
# group takes ahead of the catch-all, and no narrow group could.
deny contains finding("DF-001", "noncompliant", first_path(rel), msg) if {
  some rel in applicable
  group_family(rel)
  count(uncovered(rel)) == 0
  not shared_narrow_group(rel)
  count(matching_all_narrow(rel)) == 0
  msg := sprintf(
    "%s: %s are not taken together by one Dependabot group ahead of the catch-all (taken by: %s); add a group for the family, or record a reviewed exception",
    [rel.id, concat(", ", sort(rel.members)), concat(", ", group_names(rel))],
  )
}

# DF-003: a narrow group matches every member, but an earlier group takes one
# of them first, so the family still splits.
deny contains finding("DF-003", "noncompliant", first_path(rel), msg) if {
  some rel in applicable
  group_family(rel)
  count(uncovered(rel)) == 0
  not shared_narrow_group(rel)
  count(matching_all_narrow(rel)) > 0
  msg := sprintf(
    "%s: group %s matches every member of the family, but another group takes one of them first (taken by: %s); move it ahead of the shadowing group",
    [rel.id, concat(", ", sort(matching_all_narrow(rel))), concat(", ", group_names(rel))],
  )
}

# DF-002: independently versioned packages. A group cannot select mutually
# compatible latest versions, so a member needs an upper bound or an `ignore`
# rule; an exact pin is the thing a version update replaces.
deny contains finding("DF-002", "noncompliant", first_path(rel), msg) if {
  some rel in applicable
  rel.kind == "tuple"
  count(uncovered(rel)) == 0
  not protected_tuple(rel)
  msg := sprintf(
    "%s: %s are independently versioned and no member has an upper bound or an ignore rule (requirements: %s); add a documented bound or an ignore rule, or record a reviewed exception",
    [rel.id, concat(", ", sort(rel.members)), concat(", ", requirements(rel))],
  )
}
