package canon.lint_rules.gate_workspace_coverage_test

import data.canon.lint_rules.gate_workspace_coverage as policy
import rego.v1

exemptions := [{
	"repository": "leynos/example",
	"crate": "c",
	"tools": ["whitaker"],
	"rationale": "the suite's pinned nightly cannot compile this crate",
	"review_trigger": "review when the suite's nightly moves",
}]

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": []}
}

with_repository(fixture) := object.union(fixture, {"repository": {"name": "leynos/example", "path": "."}})

exempted(fixture, list) := fs if {
	fs := policy.deny with input as with_repository(fixture) with data.parameters as {"exemptions": list}
}

ids(fixture) := {f.rule_id | some f in findings(fixture)}

# -- compliant shapes -------------------------------------------------------------

test_a_checkout_without_a_manifest_is_not_audited if {
	count(findings(data.fixtures.no_cargo_manifest)) == 0
}

test_a_single_package_cannot_be_narrowed if {
	count(findings(data.fixtures.single_package)) == 0
}

test_a_gate_with_no_tool_runs_needs_none if {
	count(findings(data.fixtures.no_tool_runs)) == 0
}

test_workspace_selection_is_compliant_for_every_tool if {
	every name in [
		"clippy_workspace", "clippy_all_alias", "nextest_workspace", "nextest_llvm_cov",
		"test_workspace", "test_doc_workspace", "whitaker_workspace",
		"whitaker_all_after_separator", "keyword_prefix", "ci_workspace",
		"variables_and_prerequisites", "ci_matrix_complete", "ci_matrix_axis",
	] {
		count(findings(data.fixtures[name])) == 0
	}
}

test_a_complete_package_list_is_compliant if {
	count(findings(data.fixtures.clippy_packages_complete)) == 0
}

test_a_virtual_workspace_selects_every_member_by_default if {
	count(findings(data.fixtures.clippy_virtual_bare)) == 0
}

test_a_second_run_covers_what_the_first_excludes if {
	count(findings(data.fixtures.whitaker_two_runs_cover)) == 0
}

test_a_doc_example_beside_a_workspace_gate_is_not_a_finding if {
	count(findings(data.fixtures.docs_outside_fences_ignored)) == 0
}

# -- GC-001: each tool ---------------------------------------------------------------

test_a_bare_clippy_in_a_root_package_workspace_is_noncompliant if {
	fs := findings(data.fixtures.clippy_bare)
	{[f.rule_id, f.verdict, f.severity] | some f in fs} == {["GC-001", "noncompliant", "warning"]}
	some f in fs
	contains(f.msg, "clippy")
	contains(f.msg, "b, c")
}

test_a_bare_nextest_is_noncompliant if {
	{f.rule_id | some f in findings(data.fixtures.nextest_bare)} == {"GC-001"}
}

test_a_bare_cargo_test_is_noncompliant if {
	some f in findings(data.fixtures.test_bare)
	f.rule_id == "GC-001"
	contains(f.msg, "test commands")
}

test_doctests_are_judged_apart_from_unit_tests if {
	fs := findings(data.fixtures.test_doc_bare)
	{f.rule_id | some f in fs} == {"GC-001"}
	some f in fs
	contains(f.msg, "test-doc commands")
}

test_whitakers_own_all_does_not_select_the_workspace if {
	{f.rule_id | some f in findings(data.fixtures.whitaker_all_before_separator)} == {"GC-001"}
	{f.rule_id | some f in findings(data.fixtures.whitaker_no_separator)} == {"GC-001"}
}

test_an_incomplete_package_list_names_what_it_misses if {
	some f in findings(data.fixtures.clippy_packages_incomplete)
	f.rule_id == "GC-001"
	contains(f.msg, "leave 1 of 3 workspace member(s) out: c")
}

test_default_members_narrow_a_virtual_workspace if {
	some f in findings(data.fixtures.clippy_default_members)
	f.rule_id == "GC-001"
	contains(f.msg, "leave 1 of 2 workspace member(s) out: c")
}

# -- exclusions -------------------------------------------------------------------------

test_an_unexplained_exclusion_is_noncompliant if {
	some f in findings(data.fixtures.whitaker_exclusion_alone)
	f.rule_id == "GC-001"
	contains(f.msg, "out: c")
}

test_a_named_exemption_with_a_reason_clears_the_exclusion if {
	count(exempted(data.fixtures.whitaker_exclusion_alone, exemptions)) == 0
}

test_an_exemption_covers_only_its_tools if {
	narrow := {"repository": {"name": "leynos/example", "path": "."}}
	fixture := object.union(data.fixtures.clippy_packages_incomplete, narrow)
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": exemptions}
	{f.rule_id | some f in fs} == {"GC-001"}
}

test_an_exemption_for_another_repository_exempts_nothing if {
	other := [object.union(exemptions[0], {"repository": "leynos/elsewhere"})]
	{f.rule_id | some f in exempted(data.fixtures.whitaker_exclusion_alone, other)} == {"GC-001"}
}

test_an_exemption_without_a_rationale_or_review_trigger_exempts_nothing if {
	blank := [object.union(exemptions[0], {"rationale": "  "})]
	unreviewed := [object.union(exemptions[0], {"review_trigger": ""})]
	{f.rule_id | some f in exempted(data.fixtures.whitaker_exclusion_alone, blank)} == {"GC-001"}
	{f.rule_id | some f in exempted(data.fixtures.whitaker_exclusion_alone, unreviewed)} == {"GC-001"}
}

test_an_exemption_without_a_tool_list_covers_every_tool if {
	all_tools := [object.remove(exemptions[0], ["tools"])]
	count(exempted(data.fixtures.whitaker_exclusion_alone, all_tools)) == 0
}

# -- CI and prose --------------------------------------------------------------------------

test_a_bare_ci_command_is_noncompliant_and_names_its_step if {
	some f in findings(data.fixtures.ci_bare)
	f.rule_id == "GC-001"
	contains(f.path, ".github/workflows/ci.yml")
}

test_a_matrix_whose_every_entry_narrows_is_noncompliant if {
	fs := findings(data.fixtures.ci_matrix_narrow_entry)
	{[f.rule_id, f.verdict] | some f in fs} == {["GC-001", "noncompliant"]}
	some f in fs
	contains(f.msg, "--features sqlite")
	contains(f.msg, "--features postgres")
}

test_a_narrow_matrix_entry_beside_a_covering_one_is_compliant if {
	count(findings(data.fixtures.ci_matrix_union_covers)) == 0
}

test_a_composite_action_is_a_gate_command if {
	some f in findings(data.fixtures.ci_composite_action)
	f.rule_id == "GC-001"
}

test_a_narrowing_example_in_prose_is_named_in_the_finding if {
	some f in findings(data.fixtures.docs_example_for_a_narrow_gate)
	f.rule_id == "GC-001"
	contains(f.msg, "AGENTS.md:2")
}

test_a_correct_example_beside_a_workspace_gate_is_silent if {
	count(findings(data.fixtures.docs_example_narrows)) == 0
}

# -- indeterminate, never guessed -----------------------------------------------------------

test_an_unresolved_selection_is_indeterminate if {
	fs := findings(data.fixtures.unresolved_selection)
	{[f.rule_id, f.verdict] | some f in fs} == {["EN-001", "indeterminate"]}
}

test_an_unresolved_word_beside_a_spelled_out_workspace_is_read if {
	count(findings(data.fixtures.unresolved_selection_beside_workspace)) == 0
}

test_an_unresolved_exclusion_is_indeterminate if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.unresolved_exclusion)} == {["EN-001", "indeterminate"]}
}

test_a_wrapper_script_is_indeterminate if {
	fs := findings(data.fixtures.wrapper_script)
	{[f.rule_id, f.verdict] | some f in fs} == {["EN-001", "indeterminate"]}
	some f in fs
	contains(f.msg, "wrapper script")
}

test_another_directory_or_manifest_is_indeterminate if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.changes_directory)} == {["EN-001", "indeterminate"]}
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.other_manifest)} == {["EN-001", "indeterminate"]}
}

test_a_skipped_makefile_target_makes_a_narrow_gate_indeterminate if {
	fs := findings(data.fixtures.make_delegation)
	{[f.rule_id, f.verdict] | some f in fs} == {["EN-001", "indeterminate"]}
}

test_a_matrix_computed_by_an_expression_is_unreadable if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.ci_matrix_expression)} == {["EN-001", "indeterminate"]}
}

test_an_unfilled_ci_expression_in_the_selection_is_indeterminate if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.ci_unresolved_expression)} == {["EN-001", "indeterminate"]}
}

test_an_undecodable_workflow_is_unreadable if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.ci_undecodable_workflow)} == {["EN-001", "indeterminate"]}
}

test_an_unreadable_root_manifest_is_indeterminate if {
	fs := findings(data.fixtures.unreadable_root_manifest)
	{f.verdict | some f in fs} == {"indeterminate"}
	some f in fs
	contains(f.msg, "root Cargo.toml")
}

test_an_unreadable_member_manifest_is_indeterminate if {
	some f in findings(data.fixtures.unreadable_member_manifest)
	f.rule_id == "EN-001"
	contains(f.msg, "crates/c/Cargo.toml")
}

# -- severity and the envelope ----------------------------------------------------------------

test_severity_follows_the_parameter if {
	fs := policy.deny with input as data.fixtures.clippy_bare with data.parameters as {"severity": "error", "exemptions": []}
	{f.severity | some f in fs} == {"error"}
}

test_an_envelope_defect_is_always_an_error if {
	fs := policy.deny with input as {"schema_version": 2} with data.parameters as {"severity": "warning", "exemptions": []}
	{[f.rule_id, f.severity, f.verdict] | some f in fs} == {["EN-001", "error", "indeterminate"]}
}
