package canon.lint_rules.rust_lint_tables_test

import data.canon.lint_rules.rust_lint_tables as policy
import rego.v1

default_parameters := {"severity": "warning", "exemptions": [], "boundary_crates": []}

with_repository(fixture) := object.union(fixture, {"repository": {"name": "leynos/example", "path": "."}})

findings_with(fixture, parameters) := fs if {
	fs := policy.deny with input as with_repository(fixture) with data.parameters as parameters
}

findings(fixture) := findings_with(fixture, default_parameters)

ids(fixture) := {f.rule_id | some f in findings(fixture)}

paths_of(fixture, id) := {f.path | some f in findings(fixture); f.rule_id == id}

messages_of(fixture, id) := [f.msg | some f in findings(fixture); f.rule_id == id]

boundary := [{"repository": "leynos/example", "crate": "ffi", "adr": "ADR 0015"}]

with_boundary(fixture) := findings_with(fixture, object.union(default_parameters, {"boundary_crates": boundary}))

exemption(lint, level) := {
	"repository": "leynos/example",
	"crate": "a",
	"lint": lint,
	"level": level,
	"reason": "the generated code cannot meet it",
	"review_trigger": "review when the generator moves",
}

exempted(fixture, list) := findings_with(fixture, object.union(default_parameters, {"exemptions": list}))

# -- not applicable and clean shapes --------------------------------------------------

test_a_checkout_without_a_manifest_is_not_audited if {
	count(findings(data.fixtures.no_cargo_manifest)) == 0
}

test_fixture_and_vendored_crates_are_not_governed if {
	count(findings(data.fixtures.fixture_crate_is_not_governed)) == 0
}

test_a_workspace_designed_as_one_is_clean if {
	count(findings(data.fixtures.designed_clean)) == 0
}

test_stricter_levels_and_the_table_form_are_clean if {
	count(findings(data.fixtures.designed_stricter_levels_and_table_form)) == 0
}

test_a_nested_workspace_with_inheriting_members_is_clean if {
	count(findings(data.fixtures.designed_nested_workspace_clean)) == 0
}

test_a_member_outside_the_root_directory_inherits_when_the_workspace_lists_it if {
	count(findings(data.fixtures.designed_member_outside_the_root_directory)) == 0
}

test_a_member_that_names_its_workspace_inherits_from_it if {
	count(findings(data.fixtures.designed_member_names_its_workspace)) == 0
}

test_a_member_outside_the_root_directory_that_does_not_opt_in_is_lt_001 if {
	paths_of(data.fixtures.designed_member_outside_the_root_directory_bare, "LT-001") == {"crate/Cargo.toml"}
}

test_a_grown_workspace_that_inherits_everywhere_is_clean if {
	count(findings(data.fixtures.grown_clean)) == 0
}

test_a_single_crate_with_its_own_tables_is_clean if {
	count(findings(data.fixtures.grown_single_crate_own_tables)) == 0
}

test_a_python_extension_with_its_own_tables_is_clean if {
	count(findings(data.fixtures.extension_clean)) == 0
}

test_an_extension_workspace_with_a_test_macro_member_is_clean if {
	count(findings(data.fixtures.extension_with_test_macro_workspace_clean)) == 0
}

# -- LT-001 ---------------------------------------------------------------------------

test_a_member_that_does_not_inherit_is_lt_001 if {
	ids(data.fixtures.designed_member_not_inheriting) == {"LT-001"}
	paths_of(data.fixtures.designed_member_not_inheriting, "LT-001") == {"crates/b/Cargo.toml"}
}

test_the_finding_names_the_package_and_the_fix if {
	[msg] := messages_of(data.fixtures.designed_member_not_inheriting, "LT-001")
	contains(msg, "package b")
	contains(msg, "workspace = true")
	contains(msg, "crates/b/Cargo.toml")
}

test_members_of_a_workspace_without_lint_tables_inherit_nothing if {
	ids(data.fixtures.designed_inherits_nothing) == {"LT-001"}
	[msg] := messages_of(data.fixtures.designed_inherits_nothing, "LT-001")
	contains(msg, "no workspace above it defines [workspace.lints]")
}

test_a_member_of_a_nested_workspace_is_judged_against_that_workspace if {
	paths_of(data.fixtures.designed_nested_workspace_member_bare, "LT-001") == {"tools/xtask/Cargo.toml"}
}

test_members_added_to_a_crate_with_own_tables_are_lt_001 if {
	paths_of(data.fixtures.grown_root_own_tables_member_bare, "LT-001") == {"crates/b/Cargo.toml"}
}

test_a_root_package_that_does_not_opt_in_is_lt_001 if {
	paths_of(data.fixtures.grown_root_not_opted_in, "LT-001") == {"Cargo.toml"}
	[msg] := messages_of(data.fixtures.grown_root_not_opted_in, "LT-001")
	contains(msg, "root package")
}

test_default_members_do_not_hide_a_member_from_the_audit if {
	paths_of(data.fixtures.grown_default_members_hide_a_member, "LT-001") == {"crates/b/Cargo.toml"}
}

test_a_single_crate_with_no_tables_is_lt_001 if {
	ids(data.fixtures.grown_single_crate_no_tables) == {"LT-001"}
}

test_an_extension_with_no_tables_is_lt_001 if {
	ids(data.fixtures.extension_no_tables) == {"LT-001"}
	paths_of(data.fixtures.extension_no_tables, "LT-001") == {"rust_extension/Cargo.toml"}
}

test_an_extension_workspace_member_that_does_not_inherit_is_lt_001 if {
	paths_of(data.fixtures.extension_test_macros_member_bare, "LT-001") == {"rust_extension/test_macros/Cargo.toml"}
}

# -- LT-002 ---------------------------------------------------------------------------

test_weak_own_tables_in_a_member_are_lt_002 if {
	"LT-002" in ids(data.fixtures.designed_member_with_weak_own_tables)
	paths_of(data.fixtures.designed_member_with_weak_own_tables, "LT-002") == {"crates/b/Cargo.toml"}
}

test_a_workspace_missing_the_new_lints_is_lt_002_for_every_member if {
	paths_of(data.fixtures.designed_workspace_misses_new_lints, "LT-002") == {"crates/a/Cargo.toml", "crates/b/Cargo.toml"}
	[msg, _] := messages_of(data.fixtures.designed_workspace_misses_new_lints, "LT-002")
	contains(msg, "clippy::missing_assert_message should be \"deny\" (it is not set)")
	contains(msg, "clippy::missing_docs_in_private_items")
	contains(msg, "[workspace.lints.*] tables in Cargo.toml")
}

test_a_weakened_level_is_lt_002_and_says_what_it_is if {
	[msg] := messages_of(data.fixtures.designed_weakened_level, "LT-002")
	contains(msg, "clippy::unwrap_used should be \"deny\" (it is \"warn\")")
}

test_losing_the_pedantic_priority_is_lt_002 if {
	[msg] := messages_of(data.fixtures.designed_pedantic_priority_lost, "LT-002")
	contains(msg, "clippy::pedantic must keep priority -1")
}

test_the_femtologging_extension_shape_is_lt_002_for_the_missing_entries if {
	{"LT-002", "LT-003"} == ids(data.fixtures.extension_femtologging_shape)
	[msg] := messages_of(data.fixtures.extension_femtologging_shape, "LT-002")
	contains(msg, "clippy::missing_assert_message")
	contains(msg, "rust::unknown_lints")
	contains(msg, "rustdoc::bare_urls")
	contains(msg, "[lints.*] tables in rust_extension/Cargo.toml")
	not contains(msg, "clippy::disallowed_methods")
	not contains(msg, "rust::unexpected_cfgs")
}

test_the_lt_002_message_counts_its_entries if {
	[msg] := messages_of(data.fixtures.designed_weakened_level, "LT-002")
	contains(msg, "1 baseline entry")
}

# -- LT-003 ---------------------------------------------------------------------------

test_unsafe_code_that_is_only_warned_is_lt_003 if {
	"LT-003" in ids(data.fixtures.designed_unsafe_allowed)
	[msg] := messages_of(data.fixtures.designed_unsafe_allowed, "LT-003")
	contains(msg, "\"warn\"")
}

test_unsafe_code_left_unset_is_lt_003 if {
	[msg] := messages_of(data.fixtures.designed_unsafe_unset, "LT-003")
	contains(msg, "not set")
}

test_an_extension_that_does_not_forbid_unsafe_is_lt_003 if {
	ids(data.fixtures.extension_unsafe_unset) == {"LT-003"}
}

# -- LT-004 and the boundary allow-list ---------------------------------------------

test_a_named_boundary_crate_with_deny_and_miri_is_clean if {
	count(with_boundary(data.fixtures.boundary_with_miri)) == 0
}

test_a_boundary_crate_is_clean_with_miri_in_a_workflow if {
	count(with_boundary(data.fixtures.boundary_with_miri_in_workflow)) == 0
}

test_the_same_crate_not_on_the_allow_list_is_lt_003 if {
	ids(data.fixtures.boundary_with_miri) == {"LT-003"}
}

test_a_boundary_crate_without_miri_is_lt_004 if {
	fs := with_boundary(data.fixtures.boundary_without_miri)
	{f.rule_id | some f in fs} == {"LT-004"}
	some f in fs
	contains(f.msg, "no Miri command")
}

test_miri_named_only_in_an_echo_is_not_a_command if {
	{f.rule_id | some f in with_boundary(data.fixtures.boundary_miri_only_named_in_echo)} == {"LT-004"}
}

test_miri_quoted_in_an_echo_or_printf_is_not_a_command if {
	fs := with_boundary(data.fixtures.boundary_miri_quoted_in_an_echo)
	{f.rule_id | some f in fs} == {"LT-004"}
	some f in fs
	contains(f.msg, "no Miri command")
}

test_miri_in_a_shell_branch_that_cannot_run_is_not_a_command if {
	fs := with_boundary(data.fixtures.boundary_miri_in_a_branch_that_cannot_run)
	{f.rule_id | some f in fs} == {"LT-004"}
	some f in fs
	contains(f.msg, "no Miri command")
}

test_miri_behind_an_environment_prefix_is_a_command if {
	count(with_boundary(data.fixtures.boundary_miri_behind_an_environment_prefix)) == 0
}

test_a_boundary_crate_that_forbids_unsafe_is_lt_004 if {
	fs := with_boundary(data.fixtures.boundary_forbids_unsafe)
	some f in fs
	f.rule_id == "LT-004"
	contains(f.msg, "remove the crate from boundary_crates")
}

test_a_boundary_entry_with_a_blank_adr_is_lt_004 if {
	blank := [{"repository": "leynos/example", "crate": "ffi", "adr": "  "}]
	fs := findings_with(data.fixtures.boundary_with_miri, object.union(default_parameters, {"boundary_crates": blank}))
	some f in fs
	contains(f.msg, "names no ADR")
}

test_a_boundary_entry_for_another_repository_allows_nothing if {
	other := [{"repository": "leynos/other", "crate": "ffi", "adr": "ADR 0015"}]
	fs := findings_with(data.fixtures.boundary_with_miri, object.union(default_parameters, {"boundary_crates": other}))
	{f.rule_id | some f in fs} == {"LT-003"}
}

test_a_boundary_entry_naming_no_package_is_lt_004 if {
	stale := [{"repository": "leynos/example", "crate": "gone", "adr": "ADR 0015"}]
	fs := findings_with(data.fixtures.designed_clean, object.union(default_parameters, {"boundary_crates": stale}))
	some f in fs
	f.rule_id == "LT-004"
	contains(f.msg, "no such package")
}

test_an_unreadable_gate_makes_a_missing_miri_command_indeterminate if {
	fs := with_boundary(data.fixtures.boundary_gate_unreadable)
	some f in fs
	f.rule_id == "EN-001"
	f.verdict == "indeterminate"
	contains(f.msg, "Miri")
	not "LT-004" in {x.rule_id | some x in fs}
}

# -- cognitive complexity: two gates ------------------------------------------------

test_clippy_complexity_gate_is_clean if {
	count(findings(data.fixtures.cognitive_by_clippy)) == 0
}

test_the_macro_aware_gate_over_the_whole_workspace_replaces_clippys if {
	count(findings(data.fixtures.cognitive_by_whitaker)) == 0
}

test_whitaker_commands_that_cover_the_members_between_them_count if {
	count(findings(data.fixtures.cognitive_whitaker_two_runs_cover)) == 0
}

test_a_package_list_that_misses_a_member_does_not_count if {
	paths_of(data.fixtures.cognitive_whitaker_packages_miss_a_member, "LT-002") == {"Cargo.toml", "crates/b/Cargo.toml"}
}

test_the_macro_aware_gate_without_whole_workspace_whitaker_does_not_count if {
	[msg] := messages_of(data.fixtures.cognitive_whitaker_not_whole_workspace, "LT-002")
	contains(msg, "clippy::cognitive_complexity")
}

test_unplaceable_gate_lines_leave_the_macro_aware_gate_indeterminate if {
	fs := findings(data.fixtures.cognitive_whitaker_line_unreadable)
	{f.rule_id | some f in fs} == {"EN-001"}
	some f in fs
	f.verdict == "indeterminate"
	contains(f.msg, "whole workspace")
}

test_a_whitaker_run_only_some_matrix_entries_run_leaves_the_gate_indeterminate if {
	fs := findings(data.fixtures.cognitive_whitaker_conditional_run)
	{f.rule_id | some f in fs} == {"EN-001"}
	some f in fs
	contains(f.msg, "could not be placed")
}

test_a_leftover_clippy_threshold_means_two_gates_and_does_not_count if {
	[msg] := messages_of(data.fixtures.cognitive_whitaker_threshold_left, "LT-002")
	contains(msg, "clippy::cognitive_complexity")
}

test_a_macro_aware_ceiling_above_the_limit_does_not_count if {
	[msg] := messages_of(data.fixtures.cognitive_whitaker_ceiling_too_high, "LT-002")
	contains(msg, "clippy::cognitive_complexity")
}

test_a_float_ceiling_does_not_count if {
	[msg] := messages_of(data.fixtures.cognitive_whitaker_float_ceiling, "LT-002")
	contains(msg, "clippy::cognitive_complexity")
}

test_no_gate_at_all_is_lt_002 if {
	[msg] := messages_of(data.fixtures.cognitive_unset_and_no_optin, "LT-002")
	contains(msg, "clippy::cognitive_complexity")
}

# -- reviewed divergences -----------------------------------------------------------

test_an_exemption_with_a_reason_and_trigger_waives_the_lint if {
	fs := exempted(data.fixtures.designed_weakened_level, [exemption("clippy::unwrap_used", null)])
	count(fs) == 0
}

test_an_exemption_with_a_level_still_requires_that_level if {
	warned := exempted(data.fixtures.designed_weakened_level, [exemption("clippy::unwrap_used", "warn")])
	count(warned) == 0
	denied := exempted(data.fixtures.designed_weakened_level, [exemption("clippy::unwrap_used", "deny")])
	count(denied) == 1
}

test_an_exemption_with_a_blank_reason_waives_nothing if {
	blank := object.union(exemption("clippy::unwrap_used", null), {"reason": " "})
	count(exempted(data.fixtures.designed_weakened_level, [blank])) == 1
}

test_an_exemption_with_a_blank_trigger_waives_nothing if {
	blank := object.union(exemption("clippy::unwrap_used", null), {"review_trigger": ""})
	count(exempted(data.fixtures.designed_weakened_level, [blank])) == 1
}

test_an_exemption_for_another_package_waives_nothing if {
	other := object.union(exemption("clippy::unwrap_used", null), {"crate": "b"})
	count(exempted(data.fixtures.designed_weakened_level, [other])) == 1
}

test_an_exemption_for_another_repository_waives_nothing if {
	other := object.union(exemption("clippy::unwrap_used", null), {"repository": "leynos/other"})
	count(exempted(data.fixtures.designed_weakened_level, [other])) == 1
}

test_an_exemption_cannot_waive_unsafe_code if {
	fs := exempted(data.fixtures.designed_unsafe_allowed, [exemption("rust::unsafe_code", null)])
	"LT-003" in {f.rule_id | some f in fs}
}

test_a_pyo3_divergence_lowers_the_requirement_for_a_pyo3_package if {
	divergent := object.union(data.baseline, {"pyo3_divergences": [{
		"lint": "clippy::missing_const_for_fn",
		"level": "allow",
		"reason": "PyO3 wrappers cannot be const",
		"evidence": "https://example.invalid/measured",
	}]})
	fs := policy.deny with input as with_repository(data.fixtures.pyo3_renamed_dependency)
		with data.parameters as default_parameters
		with data.baseline as divergent
	count(fs) == 0
}

test_the_same_divergence_does_not_lower_it_for_a_package_without_pyo3 if {
	divergent := object.union(data.baseline, {"pyo3_divergences": [{
		"lint": "clippy::missing_const_for_fn",
		"level": "allow",
		"reason": "PyO3 wrappers cannot be const",
		"evidence": "https://example.invalid/measured",
	}]})
	fixture := data.fixtures.grown_single_crate_no_tables
	fs := policy.deny with input as with_repository(fixture)
		with data.parameters as default_parameters
		with data.baseline as divergent
	{f.rule_id | some f in fs} == {"LT-001"}
}

test_a_pyo3_package_without_the_divergence_misses_the_lint if {
	[msg] := messages_of(data.fixtures.pyo3_renamed_dependency, "LT-002")
	contains(msg, "clippy::missing_const_for_fn")
}

# -- indeterminate, never guessed -------------------------------------------------------

test_an_unreadable_root_manifest_is_en_001 if {
	"EN-001" in {f.rule_id | some f in findings(data.fixtures.unreadable_root_manifest)}
}

test_an_unreadable_member_manifest_is_en_001_and_the_rest_is_still_judged if {
	fs := findings(data.fixtures.unreadable_member_manifest)
	{f.rule_id | some f in fs} == {"EN-001"}
	some f in fs
	contains(f.msg, "crates/b/Cargo.toml")
	f.verdict == "indeterminate"
}

test_an_unreadable_extension_manifest_is_en_001 if {
	ids(data.fixtures.unreadable_extension_manifest) == {"EN-001"}
}

test_a_document_of_another_kind_is_an_envelope_error if {
	fs := policy.deny with input as {"schema_version": 1, "kind": "policy-input/other"}
		with data.parameters as default_parameters
	some f in fs
	f.rule_id == "EN-001"
	f.severity == "error"
}

# -- severity -------------------------------------------------------------------------

test_findings_default_to_warning if {
	every f in findings(data.fixtures.designed_member_not_inheriting) {
		f.severity == "warning"
	}
}

test_the_severity_parameter_promotes_findings_to_error if {
	fs := findings_with(data.fixtures.designed_member_not_inheriting, object.union(default_parameters, {"severity": "error"}))
	every f in fs {
		f.severity == "error"
	}
}
