# Policy tests for the rust-makefile-baseline rule package.
#
# Fixture envelopes are supplied via `conftest verify --data fixtures/data.json`
# and appear under `data.fixtures`. Each test pins the exact finding set a
# fixture must produce, so any drift in policy semantics fails loudly.
package canon.lint_rules.rust_makefile_baseline_test

import rego.v1

import data.canon.lint_rules.rust_makefile_baseline as policy

# -- helpers ---------------------------------------------------------------

profile(findings) := {[f.rule_id, f.verdict] | some f in findings}

# -- clean fixtures --------------------------------------------------------

test_compliant_has_no_findings if {
	findings := policy.deny with input as data.fixtures.compliant
	count(findings) == 0
}

test_one_hop_delegation_is_compliant if {
	findings := policy.deny with input as data.fixtures.one_hop
	count(findings) == 0
}

# -- FP-003 ----------------------------------------------------------------

test_missing_makefile_is_fp003 if {
	findings := policy.deny with input as data.fixtures.no_makefile
	count(findings) == 1
	profile(findings) == {["FP-003", "noncompliant"]}
}

test_missing_lint_target_is_fp003_and_qg001 if {
	findings := policy.deny with input as data.fixtures.missing_target
	count(findings) == 2
	profile(findings) == {["FP-003", "noncompliant"], ["QG-001", "noncompliant"]}
	some f in findings
	f.rule_id == "FP-003"
	contains(f.msg, `"lint"`)
}

test_conditional_lint_target_is_fp003 if {
	findings := policy.deny with input as data.fixtures.conditional_lint
	count(findings) == 1
	profile(findings) == {["FP-003", "noncompliant"]}
}

# -- QG-001 noncompliant ---------------------------------------------------

test_overridable_gate_is_qg001_with_line if {
	findings := policy.deny with input as data.fixtures.overridable_gate
	count(findings) == 1
	some f in findings
	f.rule_id == "QG-001"
	f.verdict == "noncompliant"
	f.line == 1
	contains(f.msg, "?=")
}

test_soft_skip_guard_is_qg001 if {
	findings := policy.deny with input as data.fixtures.soft_skip
	count(findings) == 1
	profile(findings) == {["QG-001", "noncompliant"]}
	some f in findings
	contains(f.msg, "command -v")
}

test_suppressed_recipes_are_two_qg001_findings if {
	findings := policy.deny with input as data.fixtures.suppressed
	count(findings) == 2
	profile(findings) == {["QG-001", "noncompliant"]}
}

# -- QG-001 indeterminate (fail closed) ------------------------------------

test_include_makes_qg001_indeterminate if {
	findings := policy.deny with input as data.fixtures.with_include
	count(findings) == 1
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_two_hop_delegation_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.two_hop
	count(findings) == 1
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_duplicate_lint_rules_are_indeterminate if {
	findings := policy.deny with input as data.fixtures.duplicate_lint
	count(findings) == 1
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_recovered_parse_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.recovered
	count(findings) == 1
	profile(findings) == {["QG-001", "indeterminate"]}
}

# -- applicability and envelope guards -------------------------------------

test_not_rust_is_single_applicability_finding if {
	findings := policy.deny with input as data.fixtures.not_rust
	count(findings) == 1
	profile(findings) == {["AP-001", "indeterminate"]}
}

test_unknown_schema_version_is_rejected if {
	findings := policy.deny with input as {"schema_version": 2}
	count(findings) == 1
	profile(findings) == {["EN-001", "indeterminate"]}
}
