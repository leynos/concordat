package canon.lint_rules.nested_cargo_fixture_lockfiles_test

import data.canon.lint_rules.nested_cargo_fixture_lockfiles as policy
import rego.v1

exemptions := [{
	"repository": "leynos/example",
	"path": "crates/a/tests/fixtures/minimal",
	"rationale": "separate lane owns this lock",
	"review_trigger": "review when the lane is retired",
}]

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": []}
}

profile(fixture) := {[f.rule_id, f.verdict, f.path] | some f in findings(fixture)}

test_a_subset_lock_is_compliant if {
	count(findings(data.fixtures.compliant)) == 0
}

test_no_root_lock_means_no_graph_to_align_to if {
	count(findings(data.fixtures.no_root_lock)) == 0
}

test_a_lock_with_foreign_packages_is_noncompliant if {
	profile(data.fixtures.drifted_lock) == {["NC-001", "noncompliant", "crates/a/tests/fixtures/minimal/Cargo.lock"]}
}

test_the_message_names_the_foreign_packages if {
	some f in findings(data.fixtures.drifted_lock)
	contains(f.msg, "anyhow 1.0.99")
	contains(f.msg, "2 registry package(s)")
}

test_an_unlocked_fixture_manifest_is_noncompliant if {
	profile(data.fixtures.unlocked_manifest) == {["NC-002", "noncompliant", "tests/ui/cli_configuration_pass/Cargo.toml"]}
}

test_a_named_exemption_takes_the_fixture_out if {
	fs := policy.deny with input as data.fixtures.exempt_directory with data.parameters as {"exemptions": exemptions}
	count(fs) == 0
}

# An exemption names its repository: the same directory elsewhere is audited.
test_an_exemption_does_not_travel if {
	fs := policy.deny with input as data.fixtures.named_elsewhere with data.parameters as {"exemptions": exemptions}
	{f.rule_id | some f in fs} == {"NC-001"}
}

test_an_exemption_covers_only_its_directory if {
	other := [{"repository": "leynos/example", "path": "elsewhere", "rationale": "r", "review_trigger": "t"}]
	fs := policy.deny with input as data.fixtures.drifted_lock with data.parameters as {"exemptions": other}
	{f.rule_id | some f in fs} == {"NC-001"}
}

test_an_unreadable_root_lock_is_not_a_pass if {
	profile(data.fixtures.unreadable_root_lock) == {["EN-001", "indeterminate", "Cargo.lock"]}
}

test_an_unreadable_fixture_is_not_a_pass if {
	profile(data.fixtures.unreadable_fixture) == {["EN-001", "indeterminate", ""]}
}

test_a_missing_envelope_is_not_a_pass if {
	fs := policy.deny with input as {} with data.parameters as {"exemptions": []}
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_a_foreign_envelope_kind_is_not_a_pass if {
	wrong := object.union(data.fixtures.compliant, {"kind": "policy-input/dependabot-update-shape"})
	fs := policy.deny with input as wrong with data.parameters as {"exemptions": []}
	{f.rule_id | some f in fs} == {"EN-001"}
}
