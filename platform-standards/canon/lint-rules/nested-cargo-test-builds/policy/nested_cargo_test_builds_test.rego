package canon.lint_rules.nested_cargo_test_builds_test

import data.canon.lint_rules.nested_cargo_test_builds as policy
import rego.v1

exemptions := [{
	"repository": "leynos/example",
	"path": "tests/cli.rs",
	"rationale": "the plugin must be built by the test that drives it",
	"review_trigger": "review when the plugin gains a library entry point",
}]

blank_rationale := [{
	"repository": "leynos/example",
	"path": "tests/cli.rs",
	"rationale": "  ",
	"review_trigger": "review when the plugin gains a library entry point",
}]

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": []}
}

findings_with(fixture, listed) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": listed}
}

profile(fixture) := {[f.rule_id, f.verdict, f.path] | some f in findings(fixture)}

test_a_declared_builder_with_a_resolved_warm_up_is_compliant if {
	count(findings(data.fixtures.compliant)) == 0
}

test_a_declared_trybuild_suite_is_compliant if {
	count(findings(data.fixtures.trybuild_declared)) == 0
}

test_a_scratch_crate_with_no_harness_reference_is_not_nc012 if {
	count(findings(data.fixtures.scratch_only)) == 0
}

test_an_undeclared_spawn_is_nc011 if {
	profile(data.fixtures.undeclared) == {["NC-011", "noncompliant", "tests/cli.rs"]}
}

test_an_exemption_suppresses_nc011 if {
	count(findings_with(data.fixtures.undeclared, exemptions)) == 0
}

test_a_blank_rationale_suppresses_nothing if {
	count(findings_with(data.fixtures.undeclared, blank_rationale)) == 1
}

test_a_builder_declared_as_a_harness_test_is_nc012 if {
	profile(data.fixtures.harness_declared) == {["NC-012", "noncompliant", "tests/report.rs"]}
}

test_a_scratch_crate_that_uses_the_harness_is_nc012_even_when_declared if {
	profile(data.fixtures.scratch_harness) == {["NC-012", "noncompliant", "tests/report.rs"]}
}

test_nc012_cannot_be_exempted if {
	listed := [{
		"repository": "leynos/example",
		"path": "tests/report.rs",
		"rationale": "it needs the real thing",
		"review_trigger": "review quarterly",
	}]
	count({f | some f in findings_with(data.fixtures.harness_declared, listed); f.rule_id == "NC-012"}) == 1
	count({f | some f in findings_with(data.fixtures.scratch_harness, listed); f.rule_id == "NC-012"}) == 1
}

test_a_non_string_harness_reference_is_an_error if {
	builder := object.union(data.fixtures.scratch_harness.builders[0], {"harness_references": [42]})
	fixture := object.union(data.fixtures.scratch_harness, {"builders": [builder]})
	some f in findings(fixture)
	f.rule_id == "EN-001"
	f.severity == "error"
}

test_a_harness_name_alone_is_not_nc012 if {
	count({f | some f in findings(data.fixtures.harness_only); f.rule_id == "NC-012"}) == 0
}

test_an_unreadable_spawn_is_indeterminate_not_nc011 if {
	profile(data.fixtures.unreadable_spawn) == {["EN-001", "indeterminate", "tests/shell.rs"]}
}

test_a_declaration_does_not_make_an_unreadable_spawn_readable if {
	profile(data.fixtures.declared_unreadable_spawn) == {["EN-001", "indeterminate", "tests/shell.rs"]}
}

test_a_stale_declaration_is_nc011 if {
	profile(data.fixtures.stale_declaration) == {["NC-011", "noncompliant", "tests/gone.rs"]}
}

test_a_warm_up_that_does_not_resolve_is_nc013 if {
	profile(data.fixtures.uncovered) == {["NC-013", "noncompliant", "tests/cli.rs"]}
}

test_a_warm_up_the_reader_cannot_check_is_indeterminate if {
	profile(data.fixtures.unreadable_coverage) == {["EN-001", "indeterminate", "tests/cli.rs"]}
}

test_an_unknown_subject_is_indeterminate if {
	profile(data.fixtures.unknown_subject) == {["EN-001", "indeterminate", "tests/cli.rs"]}
}

test_a_problem_fact_is_indeterminate if {
	profile(data.fixtures.problem) == {["EN-001", "indeterminate", ""]}
}

test_a_malformed_builder_is_an_error if {
	some f in findings(data.fixtures.malformed_builder)
	f.rule_id == "EN-001"
	f.severity == "error"
}

test_the_wrong_kind_is_an_error if {
	profile(data.fixtures.wrong_kind) == {["EN-001", "indeterminate", ""]}
	some f in findings(data.fixtures.wrong_kind)
	f.severity == "error"
}

test_the_severity_parameter_reaches_the_findings if {
	fs := policy.deny with input as data.fixtures.undeclared with data.parameters as {"severity": "error", "exemptions": []}
	every f in fs {
		f.severity == "error"
	}
}
