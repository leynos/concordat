# Tests for documentation-presence. Each fixture under fixtures/envelopes is the
# production envelope for a synthetic checkout (see fixtures/generate.py); the
# expected set is the exact rule and verdict pairs the policy must report, so a
# test fails on a missing finding and on an extra one.
package canon.lint_rules.documentation_presence_test

import rego.v1

import data.canon.lint_rules.documentation_presence as policy

reported(envelope) := {sprintf("%s/%s", [f.rule_id, f.verdict]) |
	some f in policy.deny with input as envelope
}

findings(envelope) := [f | some f in policy.deny with input as envelope]

test_compliant if reported(data.fixtures.compliant) == set()

test_config_by_make_functions if reported(data.fixtures.config_by_make_functions) == set()

test_config_missing if reported(data.fixtures.config_missing) == {"DG-001/indeterminate"}

test_config_not_toml if reported(data.fixtures.config_not_toml) == {"DG-001/indeterminate"}

test_exclude_fixtures_allowed if reported(data.fixtures.exclude_fixtures_allowed) == set()

test_exclude_tests if reported(data.fixtures.exclude_tests) == {"DG-001/noncompliant"}

test_exclude_tests_config if reported(data.fixtures.exclude_tests_config) == {"DG-001/noncompliant"}

test_gate_outside_lint if reported(data.fixtures.gate_outside_lint) == {"DG-001/noncompliant"}

test_gate_through_sub_make if reported(data.fixtures.gate_through_sub_make) == set()

test_gate_unresolved if reported(data.fixtures.gate_unresolved) == {"DG-001/indeterminate"}

test_gate_variable_twice if reported(data.fixtures.gate_variable_twice) == {"DG-001/indeterminate"}

test_ignore_config if reported(data.fixtures.ignore_config) == {"DG-001/noncompliant"}

test_ignore_config_false if reported(data.fixtures.ignore_config_false) == set()

test_ignore_flag if reported(data.fixtures.ignore_flag) == {"DG-001/noncompliant"}

test_ignore_overloaded_allowed if reported(data.fixtures.ignore_overloaded_allowed) == set()

test_ignore_regex_flag if reported(data.fixtures.ignore_regex_flag) == {"DG-001/noncompliant"}

test_ignore_short_flags if reported(data.fixtures.ignore_short_flags) == {"DG-001/noncompliant"}

test_lint_unresolved_prerequisite if reported(data.fixtures.lint_unresolved_prerequisite) == {"DG-001/indeterminate"}

test_no_gate if reported(data.fixtures.no_gate) == {"DG-001/noncompliant"}

test_no_lint_target if reported(data.fixtures.no_lint_target) == {"DG-001/noncompliant"}

test_no_makefile if reported(data.fixtures.no_makefile) == {"DG-001/noncompliant"}

test_not_applicable if reported(data.fixtures.not_applicable) == set()

test_only_fixtures if reported(data.fixtures.only_fixtures) == set()

test_scope_dot if reported(data.fixtures.scope_dot) == set()

test_scope_file_target if reported(data.fixtures.scope_file_target) == {"DG-001/noncompliant"}

test_scope_partial if reported(data.fixtures.scope_partial) == {"DG-001/noncompliant"}

test_scope_single_directory_missing if reported(data.fixtures.scope_single_directory_missing) == {"DG-001/noncompliant"}

test_scope_two_runs if reported(data.fixtures.scope_two_runs) == set()

test_threshold_cli_beats_config if reported(data.fixtures.threshold_cli_beats_config) == set()

test_threshold_from_config if reported(data.fixtures.threshold_from_config) == set()

test_threshold_from_config_low if reported(data.fixtures.threshold_from_config_low) == {"DG-001/noncompliant"}

test_threshold_low if reported(data.fixtures.threshold_low) == {"DG-001/noncompliant"}

test_threshold_missing if reported(data.fixtures.threshold_missing) == {"DG-001/noncompliant"}

test_threshold_not_a_number if reported(data.fixtures.threshold_not_a_number) == {"DG-001/indeterminate"}

test_unguarded if reported(data.fixtures.unguarded) == {"DG-002/noncompliant"}

test_unguarded_and_quiet if reported(data.fixtures.unguarded_and_quiet) == {"DG-002/noncompliant"}

test_vendored_python_ignored if reported(data.fixtures.vendored_python_ignored) == set()

test_verbose_from_config if reported(data.fixtures.verbose_from_config) == set()

test_verbose_missing if reported(data.fixtures.verbose_missing) == {"DG-002/noncompliant"}

test_verbose_one if reported(data.fixtures.verbose_one) == {"DG-002/noncompliant"}

test_verbose_split if reported(data.fixtures.verbose_split) == set()

test_gate_named_only_in_echo if reported(data.fixtures.gate_named_only_in_echo) == {"DG-001/noncompliant"}

test_guard_and_exit if reported(data.fixtures.guard_and_exit) == set()

test_guard_negated if reported(data.fixtures.guard_negated) == set()

test_guard_result_ignored if reported(data.fixtures.guard_result_ignored) == {"DG-002/noncompliant"}

test_ignore_config_and_regex if reported(data.fixtures.ignore_config_and_regex) == {"DG-001/noncompliant"}

# A partial scope names each directory it misses, once, with a file to look at.
test_scope_partial_names_every_directory if {
	messages := {f.msg | some f in findings(data.fixtures.scope_partial)}
	count(messages) == 3
	some message in messages
	contains(message, "`tests`")
}

# The verdict for a threshold of 95 names the threshold.
test_threshold_low_names_the_value if {
	some f in findings(data.fixtures.threshold_low)
	contains(f.msg, "95")
}

# A finding is placed on the Makefile line of the run it concerns.
test_unguarded_points_at_the_recipe if {
	some f in findings(data.fixtures.unguarded)
	f.path == "Makefile"
	f.line > 0
}

# A policy fed another package's envelope refuses rather than guessing.
test_wrong_envelope_is_indeterminate if {
	reported(object.union(data.fixtures.compliant, {"kind": "policy-input/other"})) == {"EN-001/indeterminate"}
}

# The required threshold is a parameter: a lower bar accepts a run at 95.
test_parameter_lowers_the_bar if {
	reported(data.fixtures.threshold_low) == set() with data.parameters.required_threshold as 90
}

# A configuration with a switch and a pattern reports both, in one finding.
test_ignore_config_and_regex_names_both if {
	some f in findings(data.fixtures.ignore_config_and_regex)
	contains(f.msg, "ignore-magic")
	contains(f.msg, "ignore-regex")
}
