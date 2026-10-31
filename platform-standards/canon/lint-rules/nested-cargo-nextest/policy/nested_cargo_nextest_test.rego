package canon.lint_rules.nested_cargo_nextest_test

import data.canon.lint_rules.nested_cargo_nextest as policy
import rego.v1

exemptions := [{
	"repository": "leynos/example",
	"path": "tests/ui.rs",
	"rationale": "runs a prebuilt compiler fixture bounded by the job",
	"review_trigger": "review when the fixture starts compiling",
}]

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": []}
}

ids(fixture) := {f.rule_id | some f in findings(fixture)}

severities(fixture, parameters) := {f.severity | some f in policy.deny with input as fixture with data.parameters as parameters}

test_no_spawner_needs_no_configuration if {
	count(findings(data.fixtures.no_spawner)) == 0
}

test_a_bounded_serial_group_is_compliant if {
	count(findings(data.fixtures.bounded)) == 0
}

test_a_missing_configuration_is_noncompliant if {
	fs := findings(data.fixtures.no_config)
	{[f.rule_id, f.verdict] | some f in fs} == {["NC-004", "noncompliant"]}
	count(fs) == 1
}

test_no_serial_group_is_noncompliant if {
	some f in findings(data.fixtures.no_serial_group)
	contains(f.msg, "max-threads = 1")
	ids(data.fixtures.no_serial_group) == {"NC-004"}
}

test_no_grouped_override_is_noncompliant if {
	some f in findings(data.fixtures.no_grouped_override)
	contains(f.msg, "no profile override places tests")
}

test_no_slow_timeout_is_noncompliant if {
	some f in findings(data.fixtures.no_slow_timeout)
	contains(f.msg, "slow-timeout")
}

test_no_global_timeout_is_noncompliant if {
	some f in findings(data.fixtures.no_global_timeout)
	contains(f.msg, "global-timeout")
}

test_an_unnamed_binary_is_noncompliant if {
	fs := findings(data.fixtures.unnamed_binary)
	{[f.rule_id, f.path] | some f in fs} == {["NC-005", "tests/ui.rs"]}
}

test_a_named_exemption_takes_the_spawner_out if {
	fs := policy.deny with input as data.fixtures.no_config with data.parameters as {"exemptions": exemptions}
	count(fs) == 0
}

# An exemption names its repository: the same source elsewhere is audited.
test_an_exemption_does_not_travel if {
	fs := policy.deny with input as data.fixtures.named_elsewhere with data.parameters as {"exemptions": exemptions}
	{f.rule_id | some f in fs} == {"NC-004"}
}

test_an_unreadable_configuration_is_not_a_pass if {
	ids(data.fixtures.unreadable_config) == {"EN-001"}
}

test_an_unreadable_source_is_not_a_pass if {
	ids(data.fixtures.unreadable_source) == {"EN-001"}
}

test_a_missing_envelope_is_not_a_pass if {
	fs := policy.deny with input as {} with data.parameters as {"exemptions": []}
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_a_foreign_envelope_kind_is_not_a_pass if {
	wrong := object.union(data.fixtures.bounded, {"kind": "policy-input/nested-cargo-fixture-manifests"})
	fs := policy.deny with input as wrong with data.parameters as {"exemptions": []}
	{f.rule_id | some f in fs} == {"EN-001"}
}

# An exemption without a rationale or a review trigger is not a record, and
# suppresses nothing.
test_an_exemption_without_a_rationale_suppresses_nothing if {
	blank := [object.union(exemptions[0], {"rationale": "  "})]
	fs := policy.deny with input as data.fixtures.no_config with data.parameters as {"exemptions": blank}
	{f.rule_id | some f in fs} == {"NC-004"}
}

test_an_exemption_without_a_review_trigger_suppresses_nothing if {
	blank := [object.union(exemptions[0], {"review_trigger": ""})]
	fs := policy.deny with input as data.fixtures.no_config with data.parameters as {"exemptions": blank}
	{f.rule_id | some f in fs} == {"NC-004"}
}

# The package ships as a survey: no configured severity means a warning.
test_the_default_severity_is_a_warning if {
	severities(data.fixtures.no_config, {"exemptions": []}) == {"warning"}
}

test_the_configured_severity_applies_to_both_checks if {
	severities(data.fixtures.unnamed_binary, {"exemptions": [], "severity": "error"}) == {"error"}
	severities(data.fixtures.no_config, {"exemptions": [], "severity": "error"}) == {"error"}
}

test_indeterminate_findings_carry_the_configured_severity if {
	severities(data.fixtures.unreadable_config, {"exemptions": [], "severity": "error"}) == {"error"}
}

# An envelope defect says the rule could not run, whatever severity is set.
test_an_envelope_defect_is_always_an_error if {
	fs := policy.deny with input as {} with data.parameters as {"exemptions": [], "severity": "warning"}
	{f.severity | some f in fs} == {"error"}
}
