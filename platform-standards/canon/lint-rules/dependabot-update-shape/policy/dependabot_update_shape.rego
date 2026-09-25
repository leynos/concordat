# DB-005: Dependabot updates run daily, group minor and patch updates into one
# catch-all per entry, leave majors ungrouped, and reach every local action.
#
# The Python envelope has already decoded `.github/dependabot.yml`, recorded
# each entry's group names in document order (a Rego object keeps none, and
# Dependabot assigns a dependency to the first group that matches), and listed
# the directories under `.github/actions` that hold an action manifest. A
# repository with no configuration is compliant: this rule judges the shape of
# a configuration, not its presence. A configuration that cannot be decoded is
# indeterminate rather than clean.
package canon.lint_rules.dependabot_update_shape

import rego.v1

finding(verdict, path, message) := {
  "rule_id": "DB-005",
  "severity": "error",
  "verdict": verdict,
  "path": path,
  "line": 0,
  "msg": message,
}

default_path := ".github/dependabot.yml"

envelope_ok if {
  input.schema_version == 1
  input.kind == "policy-input/dependabot-update-shape"
  is_array(input.action_directories)
}

config := object.get(input, "config", null)

config_path := path if {
  is_object(config)
  path := object.get(config, "path", default_path)
}

config_path := default_path if not is_object(config)

parsed := doc if {
  is_object(config)
  object.get(config, "error", null) == null
  doc := object.get(config, "parsed", null)
  is_object(doc)
}

updates := list if {
  list := object.get(parsed, "updates", null)
  is_array(list)
}

group_order := object.get(config, "group_order", [])

deny contains finding("indeterminate", default_path, msg) if {
  not envelope_ok
  msg := "policy input is not a policy-input/dependabot-update-shape envelope at schema version 1"
}

deny contains finding("indeterminate", config_path, msg) if {
  envelope_ok
  is_object(config)
  reason := object.get(config, "error", null)
  reason != null
  msg := sprintf("Dependabot configuration could not be read: %s", [reason])
}

deny contains finding("noncompliant", config_path, msg) if {
  envelope_ok
  parsed
  not updates
  msg := "Dependabot configuration has no updates list"
}

# -- Entries ------------------------------------------------------------------

entry_label(index) := sprintf("updates[%d] (%v)", [index, ecosystem]) if {
  is_object(updates[index])
  ecosystem := object.get(updates[index], "package-ecosystem", "no ecosystem")
}

entry_label(index) := sprintf("updates[%d]", [index]) if {
  not is_object(updates[index])
}

deny contains finding("noncompliant", config_path, msg) if {
  envelope_ok
  some index, entry in updates
  not is_object(entry)
  msg := sprintf("%s is not a mapping", [entry_label(index)])
}

entries contains index if {
  some index, entry in updates
  is_object(entry)
}

# -- Cadence ------------------------------------------------------------------

interval(entry) := value if {
  schedule := object.get(entry, "schedule", null)
  is_object(schedule)
  value := object.get(schedule, "interval", null)
}

interval(entry) := null if {
  schedule := object.get(entry, "schedule", null)
  not is_object(schedule)
}

deny contains finding("noncompliant", config_path, msg) if {
  envelope_ok
  some index in entries
  value := interval(updates[index])
  value != "daily"
  msg := sprintf("%s schedule.interval is %s, not daily", [entry_label(index), interval_text(value)])
}

interval_text(null) := "missing"

interval_text(value) := sprintf("%v", [value]) if value != null

# -- Groups ---------------------------------------------------------------------

# The catch-all takes every minor and patch update and nothing else. Any other
# key changes that: `applies-to: security-updates` leaves version updates
# ungrouped, and `exclude-patterns`, `dependency-type` and `group-by` each
# carve out a class of dependency that then arrives one pull request apiece.
# `applies-to: version-updates` is the default spelled out, so it is allowed.
catch_all_keys := {"patterns", "update-types"}

catch_all_shape(group) if object.keys(group) == catch_all_keys

catch_all_shape(group) if {
  object.keys(group) == catch_all_keys | {"applies-to"}
  group["applies-to"] == "version-updates"
}

catch_all(group) if {
  is_object(group)
  catch_all_shape(group)
  group.patterns == ["*"]
  types := group["update-types"]
  is_array(types)
  count(types) == 2
  {type | some type in types} == {"minor", "patch"}
}

wildcard(pattern) if {
  is_string(pattern)
  regex.match(`^\*+$`, pattern)
}

# A named group before the catch-all must be narrower than it: a lockstep
# family such as `rstest-bdd*`. A group with no patterns, or with a pattern
# that matches every dependency, is a second catch-all, and one without
# update-types would group majors too.
narrow(group) if {
  is_object(group)
  patterns := object.get(group, "patterns", null)
  is_array(patterns)
  count(patterns) > 0
  every pattern in patterns {
    is_string(pattern)
    not wildcard(pattern)
  }
}

groups(index) := value if {
  value := object.get(updates[index], "groups", null)
  is_object(value)
}

ordered_groups(index) := names if {
  names := group_order[index]
  is_array(names)
  count(names) > 0
}

deny contains finding("noncompliant", config_path, msg) if {
  envelope_ok
  some index in entries
  not ordered_groups(index)
  msg := sprintf("%s has no groups; it needs a catch-all group with patterns [\"*\"] and update-types [minor, patch]", [entry_label(index)])
}

deny contains finding("noncompliant", config_path, msg) if {
  envelope_ok
  some index in entries
  names := ordered_groups(index)
  last := names[count(names) - 1]
  not catch_all(groups(index)[last])
  msg := sprintf("%s last group %q is not the catch-all: patterns [\"*\"] and update-types [minor, patch], with no other keys", [entry_label(index), last])
}

deny contains finding("noncompliant", config_path, msg) if {
  envelope_ok
  some index in entries
  names := ordered_groups(index)
  some position, name in names
  position < count(names) - 1
  not narrow(groups(index)[name])
  msg := sprintf("%s group %q precedes the catch-all but is not narrower than it: it needs patterns, none of them a bare wildcard", [entry_label(index), name])
}

# -- Local actions --------------------------------------------------------------

actions_entries contains index if {
  some index in entries
  updates[index]["package-ecosystem"] == "github-actions"
}

# Dependabot reads `directory` or `directories`, each relative to the
# repository root with or without a leading slash, and `directories` accepts
# globs in which `*` stays within one path segment and `**` spans any number.
entry_directories(entry) := {normalized(value) |
  some value in array.concat(listed_directories(entry), [object.get(entry, "directory", null)])
  is_string(value)
}

listed_directories(entry) := values if {
  values := object.get(entry, "directories", [])
  is_array(values)
}

listed_directories(entry) := [] if {
  not is_array(object.get(entry, "directories", []))
}

normalized(value) := concat("", ["/", trim(trim_prefix(value, "./"), "/")])

covered_directories contains directory if {
  some index in actions_entries
  some directory in entry_directories(updates[index])
}

covered(target) if {
  some pattern in covered_directories
  glob.match(pattern, ["/"], target)
}

required_directories contains "/" if count(input.action_directories) > 0

required_directories contains directory if {
  some directory in input.action_directories
}

deny contains finding("noncompliant", config_path, msg) if {
  envelope_ok
  count(actions_entries) > 0
  some directory in required_directories
  not covered(directory)
  msg := sprintf("github-actions updates do not cover %s; %s", [directory, coverage_hint(directory)])
}

coverage_hint("/") := "list / in directories, beside the local actions"

coverage_hint(directory) := "list it, or a glob such as /.github/actions/*, in directories" if {
  directory != "/"
}

# -- Cargo versioning ---------------------------------------------------------------

allowed_cargo_strategies := {"auto", "lockfile-only"}

deny contains finding("noncompliant", config_path, msg) if {
  envelope_ok
  some index in entries
  entry := updates[index]
  entry["package-ecosystem"] == "cargo"
  strategy := object.get(entry, "versioning-strategy", null)
  strategy != null
  not strategy in allowed_cargo_strategies
  msg := sprintf("%s versioning-strategy is %v; cargo allows only auto or lockfile-only", [entry_label(index), strategy])
}
