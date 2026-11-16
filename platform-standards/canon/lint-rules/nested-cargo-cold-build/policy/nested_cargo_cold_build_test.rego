package canon.lint_rules.nested_cargo_cold_build_test

import data.canon.lint_rules.nested_cargo_cold_build as policy
import rego.v1

exemptions := [{
	"repository": "leynos/example",
	"path": "tests/scenario.rs",
	"rationale": "the scenario builds a fresh fixture on purpose",
	"review_trigger": "review when the fixture gains a registry dependency",
}]

blank_rationale := [{
	"repository": "leynos/example",
	"path": "tests/scenario.rs",
	"rationale": "  ",
	"review_trigger": "review when the fixture gains a registry dependency",
}]

other_repository := [{
	"repository": "leynos/other",
	"path": "tests/scenario.rs",
	"rationale": "the scenario builds a fresh fixture on purpose",
	"review_trigger": "review when the fixture gains a registry dependency",
}]

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": []}
}

findings_with(fixture, listed) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": listed}
}

profile(fixture) := {[f.rule_id, f.verdict, f.path] | some f in findings(fixture)}

test_a_prepared_build_with_an_inherited_target_is_compliant if {
	count(findings(data.fixtures.compliant)) == 0
}

test_a_private_target_is_noncompliant if {
	profile(data.fixtures.private_target) == {["NC-009", "noncompliant", "tests/scenario.rs"]}
}

# A command that compiles nothing builds nothing, so where it would build is not
# a finding.
test_a_private_target_without_a_compile_is_not_judged if {
	count(findings(data.fixtures.private_target_without_compile)) == 0
}

test_an_unreadable_target_value_is_indeterminate if {
	profile(data.fixtures.unknown_target) == {["EN-001", "indeterminate", "tests/steps.rs"]}
}

# A fixed directory in the crate persists between runs, so it is the
# preparation question and not a private target; this one is prepared.
test_a_dedicated_target_that_is_prepared_is_compliant if {
	count(findings(data.fixtures.dedicated_target)) == 0
}

test_an_unprepared_build_is_noncompliant if {
	profile(data.fixtures.unprepared_build) == {["NC-010", "noncompliant", "tests/cli.rs"]}
}

# Two kinds of build in one file are one problem with one fix, reported once at
# the first compile and naming both.
test_one_finding_per_file_naming_every_kind if {
	found := findings(data.fixtures.unprepared_two_kinds)
	count(found) == 1
	some f in found
	f.line == 57
	contains(f.msg, "cargo, trybuild")
}

# A cargo command whose subcommand the reader cannot see may be `metadata`, so
# it is never a noncompliant finding on its own.
test_an_unseen_subcommand_alone_is_indeterminate if {
	profile(data.fixtures.unprepared_unknown_subcommand) == {["EN-001", "indeterminate", "tests/wrapper.rs"]}
}

test_an_unseen_subcommand_beside_a_definite_build_is_one_noncompliant_finding if {
	found := findings(data.fixtures.unprepared_unknown_beside_definite)
	count(found) == 1
	some f in found
	f.rule_id == "NC-010"
	f.line == 57
}

test_the_message_names_the_remedy_and_both_examples if {
	some f in findings(data.fixtures.unprepared_build)
	contains(f.msg, "nextest setup script")
	contains(f.msg, "https://github.com/leynos/rstest-bdd/pull/916")
	contains(f.msg, "https://github.com/leynos/whitaker/issues/518")
}

test_the_default_severity_is_warning if {
	some f in findings(data.fixtures.private_target)
	f.severity == "warning"
}

test_the_severity_parameter_raises_every_finding if {
	fs := policy.deny with input as data.fixtures.unprepared_build
		with data.parameters as {"severity": "error", "exemptions": []}
	every f in fs {
		f.severity == "error"
	}
	count(fs) == 1
}

test_an_exemption_suppresses_nc_009 if {
	count(findings_with(data.fixtures.private_target, exemptions)) == 0
}

test_an_exemption_with_a_blank_rationale_suppresses_nothing if {
	count(findings_with(data.fixtures.private_target, blank_rationale)) == 1
}

test_an_exemption_for_another_repository_suppresses_nothing if {
	count(findings_with(data.fixtures.private_target, other_repository)) == 1
}

test_an_exemption_does_not_hide_indeterminate_evidence if {
	count(findings_with(data.fixtures.with_problem, exemptions)) == 1
}

# An exemption suppresses NC-009 and NC-010 only, so an exempt file cannot hide
# the rule's own uncertainty: an unreadable target and an unseen subcommand both
# stay reported.
test_an_exemption_does_not_hide_an_unreadable_target if {
	exempt_steps := [{
		"repository": "leynos/example",
		"path": "tests/steps.rs",
		"rationale": "the scenario builds a fresh fixture on purpose",
		"review_trigger": "review when the fixture gains a registry dependency",
	}]
	found := findings_with(data.fixtures.unknown_target, exempt_steps)
	{[f.rule_id, f.verdict] | some f in found} == {["EN-001", "indeterminate"]}
}

test_an_exemption_does_not_hide_an_unseen_subcommand if {
	exempt_wrapper := [{
		"repository": "leynos/example",
		"path": "tests/wrapper.rs",
		"rationale": "the wrapper runs cargo metadata only",
		"review_trigger": "review when the wrapper runs another subcommand",
	}]
	found := findings_with(data.fixtures.unprepared_unknown_subcommand, exempt_wrapper)
	{[f.rule_id, f.verdict] | some f in found} == {["EN-001", "indeterminate"]}
}

test_an_exemption_still_suppresses_nc_010 if {
	exempt_cli := [{
		"repository": "leynos/example",
		"path": "tests/cli.rs",
		"rationale": "the binary is built by the package's own test target",
		"review_trigger": "review when the test builds another package",
	}]
	count(findings_with(data.fixtures.unprepared_build, exempt_cli)) == 0
}

test_a_reader_problem_is_indeterminate if {
	profile(data.fixtures.with_problem) == {["EN-001", "indeterminate", ""]}
}

# A malformed fact is reported once, as an error, and not twice with a garbled
# line number.
test_a_malformed_spawn_is_reported_once_as_an_error if {
	found := findings(data.fixtures.malformed_spawn)
	count(found) == 1
	some f in found
	f.severity == "error"
	f.verdict == "indeterminate"
}

test_an_unknown_target_state_is_an_error if {
	found := findings(data.fixtures.unknown_target_state)
	count(found) == 1
	some f in found
	contains(f.msg, "unknown target state")
}

test_a_build_fact_without_a_preparation_list_is_an_error if {
	found := findings(data.fixtures.malformed_build)
	count(found) == 1
	some f in found
	f.severity == "error"
}

# `{}` is malformed twice over (every field missing, no preparation list); the
# findings are a set and must not be a Rego conflict.
test_an_empty_build_fact_reports_each_defect_once if {
	found := findings(data.fixtures.empty_build)
	count(found) == 2
	every f in found {
		f.rule_id == "EN-001"
		f.severity == "error"
	}
}

# An exempt file with a definite build keeps its unseen-subcommand builds quiet:
# they are the compile the exempt build already explains.
test_an_exempt_definite_build_silences_its_unseen_subcommand if {
	exempt_macros := [{
		"repository": "leynos/example",
		"path": "tests/macros.rs",
		"rationale": "the macros suite builds a fresh fixture on purpose",
		"review_trigger": "review when the fixture gains a registry dependency",
	}]
	count(findings_with(data.fixtures.unprepared_unknown_beside_definite, exempt_macros)) == 0
}

test_an_envelope_of_another_kind_is_an_error if {
	profile(data.fixtures.wrong_kind) == {["EN-001", "indeterminate", ""]}
	some f in findings(data.fixtures.wrong_kind)
	f.severity == "error"
}
