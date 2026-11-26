# Fixture tests for DB-005, the Dependabot update shape.
package canon.lint_rules.dependabot_update_shape_test

import rego.v1

import data.canon.lint_rules.dependabot_update_shape as policy

profile(findings) := {
  [finding.verdict, finding.path, finding.msg] | some finding in findings
}

# The estate shape: a lockstep group before the catch-all, local actions covered.
test_compliant if {
  findings := policy.deny with input as data.fixtures.compliant
  count(findings) == 0
}

# The `.yaml` spelling is read like the `.yml` one.
test_compliant_yaml_spelling if {
  findings := policy.deny with input as data.fixtures.compliant_yaml_spelling
  count(findings) == 0
}

# No configuration is not a finding: the rule judges shape, not presence.
test_absent if {
  findings := policy.deny with input as data.fixtures.absent
  count(findings) == 0
}

# A configuration that cannot be decoded is indeterminate, never clean.
test_unreadable if {
  findings := policy.deny with input as data.fixtures.unreadable
  profile(findings) == {
    ["indeterminate", ".github/dependabot.yml", "Dependabot configuration could not be read: invalid YAML: boom"],
  }
}

# Another package's envelope is refused rather than read.
test_wrong_kind if {
  findings := policy.deny with input as data.fixtures.wrong_kind
  profile(findings) == {
    ["indeterminate", ".github/dependabot.yml", "policy input is not a policy-input/dependabot-update-shape envelope at schema version 1"],
  }
}

test_no_updates if {
  findings := policy.deny with input as data.fixtures.no_updates
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "Dependabot configuration has no updates list"],
  }
}

test_entry_not_mapping if {
  findings := policy.deny with input as data.fixtures.entry_not_mapping
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] is not a mapping"],
  }
}

test_weekly if {
  findings := policy.deny with input as data.fixtures.weekly
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.interval is weekly, not daily"],
  }
}

test_no_schedule if {
  findings := policy.deny with input as data.fixtures.no_schedule
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) schedule.interval is missing, not daily"],
  }
}

test_no_groups if {
  findings := policy.deny with input as data.fixtures.no_groups
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) has no groups; it needs a catch-all group with patterns [\"*\"] and update-types [minor, patch]"],
  }
}

test_empty_groups if {
  findings := policy.deny with input as data.fixtures.empty_groups
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) has no groups; it needs a catch-all group with patterns [\"*\"] and update-types [minor, patch]"],
  }
}

# A wildcard group without update-types groups majors too.
test_catch_all_groups_majors if {
  findings := policy.deny with input as data.fixtures.catch_all_groups_majors
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) last group \"all\" is not the catch-all: patterns [\"*\"] and update-types [minor, patch], with no other keys"],
  }
}

test_catch_all_major_type if {
  findings := policy.deny with input as data.fixtures.catch_all_major_type
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) last group \"all\" is not the catch-all: patterns [\"*\"] and update-types [minor, patch], with no other keys"],
  }
}

# Security updates only: every version update arrives ungrouped.
test_catch_all_security_only if {
  findings := policy.deny with input as data.fixtures.catch_all_security_only
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) last group \"all\" is not the catch-all: patterns [\"*\"] and update-types [minor, patch], with no other keys"],
  }
}

# The default `applies-to`, spelled out, changes nothing.
test_catch_all_version_updates if {
  findings := policy.deny with input as data.fixtures.catch_all_version_updates
  count(findings) == 0
}

test_catch_all_excludes if {
  findings := policy.deny with input as data.fixtures.catch_all_excludes
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) last group \"all\" is not the catch-all: patterns [\"*\"] and update-types [minor, patch], with no other keys"],
  }
}

test_catch_all_dependency_type if {
  findings := policy.deny with input as data.fixtures.catch_all_dependency_type
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) last group \"all\" is not the catch-all: patterns [\"*\"] and update-types [minor, patch], with no other keys"],
  }
}

# Dependabot takes the first matching group, so a leading catch-all starves the lockstep group.
test_catch_all_first if {
  findings := policy.deny with input as data.fixtures.catch_all_first
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) group \"minor-and-patch\" precedes the catch-all but is not narrower than it: it needs patterns, none of them a bare wildcard"],
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) last group \"rstest-bdd\" is not the catch-all: patterns [\"*\"] and update-types [minor, patch], with no other keys"],
  }
}

# A bare wildcard group ahead of the catch-all takes majors first.
test_second_catch_all if {
  findings := policy.deny with input as data.fixtures.second_catch_all
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) group \"everything\" precedes the catch-all but is not narrower than it: it needs patterns, none of them a bare wildcard"],
  }
}

# A pattern made only of wildcards is as broad as `*`.
test_double_star_group if {
  findings := policy.deny with input as data.fixtures.double_star_group
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) group \"everything\" precedes the catch-all but is not narrower than it: it needs patterns, none of them a bare wildcard"],
  }
}

# A dependency-type group with no patterns spans a whole class.
test_narrow_without_patterns if {
  findings := policy.deny with input as data.fixtures.narrow_without_patterns
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) group \"prod\" precedes the catch-all but is not narrower than it: it needs patterns, none of them a bare wildcard"],
  }
}

test_narrow_empty_patterns if {
  findings := policy.deny with input as data.fixtures.narrow_empty_patterns
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) group \"none\" precedes the catch-all but is not narrower than it: it needs patterns, none of them a bare wildcard"],
  }
}

# A prefix pattern is narrower than `*`, whatever update-types it takes.
test_narrow_prefix_pattern if {
  findings := policy.deny with input as data.fixtures.narrow_prefix_pattern
  count(findings) == 0
}

# The syrupy-mdast shape: workflows covered, the composite action drifting.
test_actions_root_only if {
  findings := policy.deny with input as data.fixtures.actions_root_only
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "github-actions updates do not cover /.github/actions/setup; list it, or a glob such as /.github/actions/*, in directories"],
  }
}

test_actions_without_root if {
  findings := policy.deny with input as data.fixtures.actions_without_root
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "github-actions updates do not cover /; list / in directories, beside the local actions"],
  }
}

# A scalar `directory` covers the root alone.
test_actions_directory_scalar if {
  findings := policy.deny with input as data.fixtures.actions_directory_scalar
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "github-actions updates do not cover /.github/actions/setup; list it, or a glob such as /.github/actions/*, in directories"],
  }
}

# Each action directory listed, with or without the leading or trailing slash.
test_actions_each_listed if {
  findings := policy.deny with input as data.fixtures.actions_each_listed
  count(findings) == 0
}

test_actions_one_missing if {
  findings := policy.deny with input as data.fixtures.actions_one_missing
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "github-actions updates do not cover /.github/actions/b; list it, or a glob such as /.github/actions/*, in directories"],
  }
}

# `*` stays within one path segment, so a nested action escapes it.
test_actions_nested_single_star if {
  findings := policy.deny with input as data.fixtures.actions_nested_single_star
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "github-actions updates do not cover /.github/actions/a/b; list it, or a glob such as /.github/actions/*, in directories"],
  }
}

# `**` spans any depth.
test_actions_nested_double_star if {
  findings := policy.deny with input as data.fixtures.actions_nested_double_star
  count(findings) == 0
}

# Coverage is the union of every github-actions entry.
test_actions_split_entries if {
  findings := policy.deny with input as data.fixtures.actions_split_entries
  count(findings) == 0
}

# Another ecosystem's directories do not update actions.
test_actions_other_ecosystem if {
  findings := policy.deny with input as data.fixtures.actions_other_ecosystem
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "github-actions updates do not cover /.github/actions/setup; list it, or a glob such as /.github/actions/*, in directories"],
  }
}

# Without local actions the root alone suffices.
test_no_actions_directory if {
  findings := policy.deny with input as data.fixtures.no_actions_directory
  count(findings) == 0
}

# Ecosystem coverage is not this rule's clause.
test_no_actions_entry if {
  findings := policy.deny with input as data.fixtures.no_actions_entry
  count(findings) == 0
}

test_cargo_increase if {
  findings := policy.deny with input as data.fixtures.cargo_increase
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) versioning-strategy is increase; cargo allows only auto or lockfile-only"],
  }
}

test_cargo_lockfile_only if {
  findings := policy.deny with input as data.fixtures.cargo_lockfile_only
  count(findings) == 0
}

test_cargo_auto if {
  findings := policy.deny with input as data.fixtures.cargo_auto
  count(findings) == 0
}

# A null strategy is an absent one, as the estate validator reads it.
test_cargo_null_strategy if {
  findings := policy.deny with input as data.fixtures.cargo_null_strategy
  count(findings) == 0
}

# The strategy clause binds cargo alone.
test_uv_increase if {
  findings := policy.deny with input as data.fixtures.uv_increase
  count(findings) == 0
}

# -- Private cadence -----------------------------------------------------------

# The shipped defaults: daily and weekly for a registered private repository,
# with Monday named when a weekly entry names a day.
shipped_parameters := {
  "private_cadence": {
    "accepted_intervals": ["daily", "weekly"],
    "weekly_day": "monday",
  },
  "private_repositories": [{
    "repository": "leynos/dev-env-rocky",
    "rationale": "private CI runs on Ubicloud and GitHub bills private minutes",
    "review_trigger": "review when the repository becomes public",
  }],
}

# A weekly entry need not name a day; Dependabot then chooses.
test_private_weekly_without_a_day_is_accepted if {
  findings := policy.deny with input as data.fixtures.private_weekly_no_day with data.parameters as shipped_parameters
  count(findings) == 0
}

test_private_weekly_on_monday_is_accepted if {
  findings := policy.deny with input as data.fixtures.private_weekly_monday with data.parameters as shipped_parameters
  count(findings) == 0
}

# A named day must be the configured one; a mismatch is reported and no
# interval finding accompanies it.
test_private_weekly_on_another_day_is_reported if {
  findings := policy.deny with input as data.fixtures.private_weekly_tuesday with data.parameters as shipped_parameters
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.day is tuesday, not monday"],
  }
}

# The cadence exception does not relax the catch-all: the weekly entry is
# accepted, and its group defect is still the one finding.
test_private_weekly_bad_catch_all_keeps_the_group_finding if {
  findings := policy.deny with input as data.fixtures.private_weekly_bad_catch_all with data.parameters as shipped_parameters
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[2] (uv) group \"everything\" precedes the catch-all but is not narrower than it: it needs patterns, none of them a bare wildcard"],
  }
}

test_private_monthly_is_reported_with_the_registry if {
  findings := policy.deny with input as data.fixtures.private_monthly with data.parameters as shipped_parameters
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.interval is monthly, not daily or weekly; private repository leynos/dev-env-rocky accepts daily, weekly"],
  }
}

# A repository that is not in the registry keeps the public rule, so the
# finding is the estate's daily message.
test_unregistered_weekly_keeps_the_daily_rule if {
  findings := policy.deny with input as data.fixtures.private_weekly_unregistered with data.parameters as shipped_parameters
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.interval is weekly, not daily"],
  }
}

test_null_repository_name_keeps_the_daily_rule if {
  findings := policy.deny with input as data.fixtures.private_weekly_null_name with data.parameters as shipped_parameters
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.interval is weekly, not daily"],
  }
}

# The identity match is case-insensitive because it is lowered before the
# comparison; the registry keeps its lower-case slug.
test_case_mismatched_name_matches_the_record if {
  identity := object.union(data.fixtures.private_weekly_monday, {"repository": {"path": ".", "name": "Leynos/Dev-Env-Rocky"}})
  findings := policy.deny with input as identity with data.parameters as shipped_parameters
  count(findings) == 0
}

# A record without a rationale asks nobody to revisit the exception, so it
# registers nothing and the daily rule applies.
test_blank_rationale_registers_nothing if {
  parameters := object.union(shipped_parameters, {"private_repositories": [{
    "repository": "leynos/dev-env-rocky",
    "rationale": " ",
    "review_trigger": "review when the repository becomes public",
  }]})
  findings := policy.deny with input as data.fixtures.private_weekly_no_day with data.parameters as parameters
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.interval is weekly, not daily"],
  }
}

test_absent_parameters_keep_the_daily_rule if {
  findings := policy.deny with input as data.fixtures.private_weekly_no_day with data.parameters as {}
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.interval is weekly, not daily"],
  }
}

# A non-array cadence is malformed, so the accepted set falls back to daily.
# The repository is still registered, so the private message reports the
# fallback set it actually applied.
test_malformed_accepted_intervals_keep_the_daily_rule if {
  parameters := object.union(shipped_parameters, {"private_cadence": {"accepted_intervals": "weekly", "weekly_day": "monday"}})
  findings := policy.deny with input as data.fixtures.private_weekly_no_day with data.parameters as parameters
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.interval is weekly, not daily; private repository leynos/dev-env-rocky accepts daily"],
  }
}

# Without a configured weekday the day check is disabled, so a weekly entry
# naming any day is judged on its interval alone.
test_absent_weekly_day_skips_the_day_check if {
  parameters := {
    "private_cadence": {"accepted_intervals": ["daily", "weekly"]},
    "private_repositories": shipped_parameters.private_repositories,
  }
  findings := policy.deny with input as data.fixtures.private_weekly_tuesday with data.parameters as parameters
  count(findings) == 0
}

# A declared but non-string weekday is malformed. It must not silently disable
# the day check and let an arbitrary weekly entry through; the cadence falls
# back to daily instead.
test_malformed_weekly_day_keeps_the_daily_rule if {
  parameters := object.union(shipped_parameters, {"private_cadence": {"accepted_intervals": ["daily", "weekly"], "weekly_day": 17}})
  findings := policy.deny with input as data.fixtures.private_weekly_tuesday with data.parameters as parameters
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.interval is weekly, not daily; private repository leynos/dev-env-rocky accepts daily"],
  }
}

# A blank weekday is malformed in the same way.
test_blank_weekly_day_keeps_the_daily_rule if {
  parameters := object.union(shipped_parameters, {"private_cadence": {"accepted_intervals": ["daily", "weekly"], "weekly_day": " "}})
  findings := policy.deny with input as data.fixtures.private_weekly_tuesday with data.parameters as parameters
  profile(findings) == {
    ["noncompliant", ".github/dependabot.yml", "updates[0] (cargo) schedule.interval is weekly, not daily; private repository leynos/dev-env-rocky accepts daily"],
  }
}
