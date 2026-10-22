package canon.lint_rules.nested_cargo_fixture_manifests_test

import data.canon.lint_rules.nested_cargo_fixture_manifests as policy
import rego.v1

exemptions := [{
	"repository": "leynos/example",
	"path": "tests/fixtures/a",
	"reason": "vendored patch left alone on purpose",
}]

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": []}
}

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
