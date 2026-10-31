package canon.lint_rules.nested_cargo_ci_wiring_test

import data.canon.lint_rules.nested_cargo_ci_wiring as policy
import rego.v1

patterns := [
	`(?i)runner\.temp|RUNNER_TEMP`,
	`(?i)github\.(run_id|run_number|run_attempt|sha)|GITHUB_(RUN_ID|RUN_NUMBER|RUN_ATTEMPT|SHA)`,
	`(?i)mktemp|TMPDIR|(^|[^[:alnum:]_.-])/tmp(/|$)|^/tmp`,
	`\$\(`,
]

parameters := {"temporary_patterns": patterns, "exemptions": []}

exemptions := [{
	"repository": "leynos/example",
	"path": ".github/workflows/ci.yml",
	"rationale": "benchmark lane needs a private tree",
	"review_trigger": "review when the benchmark lane moves",
}]

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as parameters
}

ids(fixture) := {f.rule_id | some f in findings(fixture)}

test_a_clean_workflow_is_compliant if {
	count(findings(data.fixtures.compliant)) == 0
}

test_no_workflows_is_compliant if {
	count(findings(data.fixtures.no_workflows)) == 0
}

test_a_stable_target_is_compliant if {
	count(findings(data.fixtures.stable_target)) == 0
}

test_the_runner_temp_directory_is_temporary if {
	ids(data.fixtures.runner_temp_target) == {"NC-006"}
}

test_a_run_id_path_is_temporary if {
	ids(data.fixtures.run_id_target) == {"NC-006"}
}

test_a_mktemp_path_is_temporary if {
	ids(data.fixtures.mktemp_target) == {"NC-006"}
}

test_tmp_is_temporary if {
	ids(data.fixtures.tmp_target) == {"NC-006"}
}

test_the_message_names_the_value_and_place if {
	some f in findings(data.fixtures.run_id_target)
	contains(f.msg, "job test")
	contains(f.msg, "github.run_id")
}

test_opting_out_without_naming_the_wrapper_is_noncompliant if {
	ids(data.fixtures.opt_out_unnamed) == {"NC-006"}
}

test_opting_out_and_scoping_the_wrapper_is_compliant if {
	count(findings(data.fixtures.opt_out_scoped)) == 0
}

test_a_named_exemption_takes_the_workflow_out if {
	fs := policy.deny with input as data.fixtures.runner_temp_target with data.parameters as object.union(parameters, {"exemptions": exemptions})
	count(fs) == 0
}

# An exemption names its repository: the same workflow elsewhere is audited.
test_an_exemption_does_not_travel if {
	fs := policy.deny with input as data.fixtures.exempt_elsewhere with data.parameters as object.union(parameters, {"exemptions": exemptions})
	{f.rule_id | some f in fs} == {"NC-006"}
}

test_an_unreadable_workflow_is_not_a_pass if {
	ids(data.fixtures.unreadable_workflow) == {"EN-001"}
}

test_a_missing_envelope_is_not_a_pass if {
	fs := policy.deny with input as {} with data.parameters as parameters
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_a_foreign_envelope_kind_is_not_a_pass if {
	wrong := object.union(data.fixtures.compliant, {"kind": "policy-input/nested-cargo-nextest"})
	fs := policy.deny with input as wrong with data.parameters as parameters
	{f.rule_id | some f in fs} == {"EN-001"}
}

# An exemption without a rationale or a review trigger is not a record, and
# suppresses nothing.
test_an_exemption_without_a_rationale_suppresses_nothing if {
	blank := [object.union(exemptions[0], {"rationale": "  "})]
	fs := policy.deny with input as data.fixtures.runner_temp_target with data.parameters as object.union(parameters, {"exemptions": blank})
	count(fs) > 0
}

test_an_exemption_without_a_review_trigger_suppresses_nothing if {
	blank := [object.union(exemptions[0], {"review_trigger": ""})]
	fs := policy.deny with input as data.fixtures.runner_temp_target with data.parameters as object.union(parameters, {"exemptions": blank})
	count(fs) > 0
}
