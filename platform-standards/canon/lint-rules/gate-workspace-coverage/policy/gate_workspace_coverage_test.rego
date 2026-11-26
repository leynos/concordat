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

test_an_unresolved_word_beside_a_spelled_out_workspace_is_indeterminate if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.unresolved_selection_beside_workspace)} == {["EN-001", "indeterminate"]}
}

test_an_unresolved_value_of_a_non_selecting_option_may_still_add_arguments if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.unresolved_profile_beside_workspace)} == {["EN-001", "indeterminate"]}
}

test_a_glob_exclusion_removes_every_member_it_matches if {
	some f in findings(data.fixtures.glob_exclusion)
	f.rule_id == "GC-001"
	contains(f.msg, "out: b, c")
}

test_a_glob_package_selector_selects_every_member_it_matches if {
	count(findings(data.fixtures.glob_package_all)) == 0
}

test_a_versioned_package_spec_names_its_package if {
	count(findings(data.fixtures.versioned_packages)) == 0
}

test_two_commands_on_one_line_are_both_counted if {
	fs := findings(data.fixtures.two_commands_one_line)
	{f.rule_id | some f in fs} == {"GC-001"}
	some f in fs
	contains(f.msg, "the test commands")
}

test_two_same_tool_commands_on_one_line_cover_together if {
	count(findings(data.fixtures.two_clippy_one_line)) == 0
}

test_a_matrix_exclude_removes_the_job_that_does_not_run if {
	count(findings(data.fixtures.ci_matrix_exclude_removes_narrow)) == 0
}

test_a_later_include_overrides_an_earlier_one if {
	count(findings(data.fixtures.ci_matrix_include_override)) == 0
}

test_a_working_directory_moves_the_step if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.ci_working_directory)} == {["EN-001", "indeterminate"]}
}

test_a_cd_on_an_earlier_line_moves_the_later_lines if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.ci_cd_on_earlier_line)} == {["EN-001", "indeterminate"]}
}

test_an_unresolved_library_value_before_whitakers_separator_hides_the_selection if {
	fs := findings(data.fixtures.whitaker_unresolved_library)
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_a_step_that_never_runs_does_not_cover if {
	some f in findings(data.fixtures.ci_step_never_runs)
	f.rule_id == "GC-001"
	contains(f.msg, "out: b, c")
}

test_a_computed_matrix_axis_is_unreadable_not_dropped if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.ci_computed_axis)} == {["EN-001", "indeterminate"]}
}

test_a_shell_expansion_after_a_benign_option_may_add_arguments if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.shell_expansion_after_benign_option)} == {["EN-001", "indeterminate"]}
}

test_an_unresolved_variable_may_supply_whitakers_separator if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.whitaker_flags_variable)} == {["EN-001", "indeterminate"]}
}

test_a_known_command_does_not_hide_an_unreadable_one_beside_it if {
	fs := findings(data.fixtures.known_command_beside_unreadable)
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_unreadable_input_prevents_a_noncompliant_verdict if {
	fs := findings(data.fixtures.unreadable_beside_narrow_gate)
	{[f.rule_id, f.verdict] | some f in fs} == {["EN-001", "indeterminate"]}
}

test_oneshell_carries_a_cd_to_later_recipe_lines if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.oneshell_cd)} == {["EN-001", "indeterminate"]}
}

test_cargos_global_directory_option_moves_the_command if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.cargo_global_directory)} == {["EN-001", "indeterminate"]}
}

test_a_conditional_recipe_is_indeterminate if {
	fs := findings(data.fixtures.conditional_rule)
	{f.verdict | some f in fs} == {"indeterminate"}
	some f in fs
	contains(f.msg, "Make condition")
}

test_an_example_that_omits_other_members_is_not_the_same_narrowing if {
	fs := findings(data.fixtures.docs_example_different_narrowing)
	some f in fs
	f.rule_id == "GC-001"
	not contains(f.msg, "documentation shows the same narrowing")
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

test_a_probe_branch_that_one_reading_lacks_leaves_coverage_to_the_probe if {
	fs := findings(data.fixtures.probe_branch_absent_in_one_reading)
	{[f.rule_id, f.verdict] | some f in fs} == {["EN-001", "indeterminate"]}
}

test_mutually_exclusive_probe_tools_are_not_counted_together if {
	fs := findings(data.fixtures.probe_exclusive_tools)
	{[f.rule_id, f.verdict] | some f in fs} == {["EN-001", "indeterminate"]}
}

test_a_multiline_if_false_in_one_step_skips_its_body if {
	some f in findings(data.fixtures.shell_if_false_multiline)
	f.rule_id == "GC-001"
	contains(f.msg, "out: b")
}

test_a_variable_subcommand_after_cargos_global_options_is_unreadable if {
	fs := findings(data.fixtures.cargo_global_option_then_variable)
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_a_computed_matrix_with_a_variable_command_head_is_unreadable if {
	fs := findings(data.fixtures.ci_computed_matrix_variable_head)
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_a_probe_that_only_picks_the_runner_keeps_both_readings_placed if {
	count(findings(data.fixtures.probe_runner_only)) == 0
}

test_a_cargo_subcommand_from_a_variable_is_unreadable_whatever_follows if {
	fs := findings(data.fixtures.cargo_subcommand_variable)
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_a_computed_matrix_that_runs_cargo_is_unreadable if {
	fs := findings(data.fixtures.ci_computed_matrix_command)
	{f.rule_id | some f in fs} == {"EN-001"}
}

test_a_command_under_a_false_shell_condition_does_not_cover if {
	some f in findings(data.fixtures.shell_if_false)
	f.rule_id == "GC-001"
	contains(f.msg, "out: b, c")
}

test_a_delegated_make_alone_is_reported_without_any_known_command if {
	fs := findings(data.fixtures.make_delegation_alone)
	{[f.rule_id, f.verdict] | some f in fs} == {["EN-001", "indeterminate"]}
	some f in fs
	contains(f.msg, "skipped a target")
}

test_a_job_that_never_runs_does_not_cover if {
	some f in findings(data.fixtures.ci_job_never_runs)
	f.rule_id == "GC-001"
}

test_a_matrix_conditional_step_is_unplaced if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.ci_matrix_conditional_step)} == {["EN-001", "indeterminate"]}
}

test_a_prerequisite_a_make_condition_adds_is_unplaced if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.conditional_prerequisite)} == {["EN-001", "indeterminate"]}
}

test_probe_readings_that_select_differently_are_alternatives_not_a_union if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.probe_alternatives)} == {["EN-001", "indeterminate"]}
}

test_a_make_value_after_an_option_may_add_arguments if {
	{[f.rule_id, f.verdict] | some f in findings(data.fixtures.make_value_after_option)} == {["EN-001", "indeterminate"]}
}

test_a_shell_variable_command_head_is_not_dropped if {
	fs := findings(data.fixtures.shell_variable_command_head)
	{f.rule_id | some f in fs} == {"EN-001"}
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
