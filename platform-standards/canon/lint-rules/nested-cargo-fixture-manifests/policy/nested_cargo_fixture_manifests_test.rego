package canon.lint_rules.nested_cargo_fixture_manifests_test

import data.canon.lint_rules.nested_cargo_fixture_manifests as policy
import rego.v1

exemptions := [{
	"repository": "leynos/example",
	"path": "tests/fixtures/a",
	"rationale": "vendored patch left alone on purpose",
	"review_trigger": "review when the patch is dropped",
}]

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": []}
}

severities(fixture, parameters) := {f.severity | some f in policy.deny with input as fixture with data.parameters as parameters}

profile(fixture) := {[f.rule_id, f.verdict, f.path] | some f in findings(fixture)}

test_no_standalone_manifest_is_compliant if {
	count(findings(data.fixtures.compliant)) == 0
}

test_a_covered_fixture_is_compliant if {
	count(findings(data.fixtures.covered_fixture)) == 0
}

test_an_uncovered_fixture_is_noncompliant if {
	profile(data.fixtures.uncovered_fixture) == {["NC-003", "noncompliant", "tests/fixtures/a/Cargo.toml"]}
}

test_no_configuration_leaves_the_fixture_uncovered if {
	profile(data.fixtures.no_configuration) == {["NC-003", "noncompliant", "tests/fixtures/a/Cargo.toml"]}
}

test_the_message_names_the_directory if {
	some f in findings(data.fixtures.uncovered_fixture)
	contains(f.msg, "covers /tests/fixtures/a")
}

test_a_named_exemption_takes_the_manifest_out if {
	fs := policy.deny with input as data.fixtures.uncovered_fixture with data.parameters as {"exemptions": exemptions}
	count(fs) == 0
}

# An exemption names its repository: the same directory elsewhere is audited.
test_an_exemption_does_not_travel if {
	fs := policy.deny with input as data.fixtures.named_elsewhere with data.parameters as {"exemptions": exemptions}
	{f.rule_id | some f in fs} == {"NC-003"}
}

test_an_unreadable_configuration_is_not_a_pass if {
	profile(data.fixtures.unreadable_configuration) == {["EN-001", "indeterminate", ".github/dependabot.yml"]}
}

test_an_unreadable_manifest_is_not_a_pass if {
	profile(data.fixtures.unreadable_manifest) == {["EN-001", "indeterminate", ""]}
}

test_a_missing_envelope_is_not_a_pass if {
	fs := policy.deny with input as {} with data.parameters as {"exemptions": []}
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_a_foreign_envelope_kind_is_not_a_pass if {
	wrong := object.union(data.fixtures.compliant, {"kind": "policy-input/nested-cargo-fixture-lockfiles"})
	fs := policy.deny with input as wrong with data.parameters as {"exemptions": []}
	{f.rule_id | some f in fs} == {"EN-001"}
}

# An exemption without a rationale or a review trigger is not a record, and
# suppresses nothing.
test_an_exemption_without_a_rationale_suppresses_nothing if {
	blank := [object.union(exemptions[0], {"rationale": "  "})]
	fs := policy.deny with input as data.fixtures.uncovered_fixture with data.parameters as {"exemptions": blank}
	{f.rule_id | some f in fs} == {"NC-003"}
}

test_an_exemption_without_a_review_trigger_suppresses_nothing if {
	blank := [object.union(exemptions[0], {"review_trigger": ""})]
	fs := policy.deny with input as data.fixtures.uncovered_fixture with data.parameters as {"exemptions": blank}
	{f.rule_id | some f in fs} == {"NC-003"}
}

# The package ships as a survey: no configured severity means a warning.
test_the_default_severity_is_a_warning if {
	severities(data.fixtures.uncovered_fixture, {"exemptions": []}) == {"warning"}
}

test_the_configured_severity_applies if {
	severities(data.fixtures.uncovered_fixture, {"exemptions": [], "severity": "error"}) == {"error"}
}

test_indeterminate_findings_carry_the_configured_severity if {
	severities(data.fixtures.unreadable_configuration, {"exemptions": [], "severity": "error"}) == {"error"}
}

# An envelope defect says the rule could not run, whatever severity is set.
test_an_envelope_defect_is_always_an_error if {
	fs := policy.deny with input as {} with data.parameters as {"exemptions": [], "severity": "warning"}
	{f.severity | some f in fs} == {"error"}
}
