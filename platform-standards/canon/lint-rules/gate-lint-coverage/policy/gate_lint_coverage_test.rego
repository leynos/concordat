package canon.lint_rules.gate_lint_coverage_test

import data.canon.lint_rules.gate_lint_coverage as policy
import rego.v1

default_parameters := {"severity": "warning", "exemptions": []}

with_repository(fixture) := object.union(fixture, {"repository": {"name": "leynos/example", "path": "."}})

findings_with(fixture, parameters) := fs if {
	fs := policy.deny with input as with_repository(fixture) with data.parameters as parameters
}

findings(fixture) := findings_with(fixture, default_parameters)

ids(fixture) := {f.rule_id | some f in findings(fixture)}

messages_of(fixture, id) := [f.msg | some f in findings(fixture); f.rule_id == id]

exemption(surface, tool) := {
	"repository": "leynos/example",
	"surface": surface,
	"tool": tool,
	"reason": "the lane leaves out the fixture crates that break the lint on purpose",
	"review_trigger": "review when the fixtures move",
}

# -- clean shapes ---------------------------------------------------------------------

test_a_checkout_without_a_manifest_is_not_audited if {
	count(findings(data.fixtures.no_cargo_manifest)) == 0
}

test_a_designed_workspace_that_checks_everything_is_clean if {
	count(findings(data.fixtures.designed_clean)) == 0
}

test_a_grown_workspace_that_checks_everything_is_clean if {
	count(findings(data.fixtures.grown_clean)) == 0
}

test_an_extension_through_manifest_path_is_clean if {
	count(findings(data.fixtures.extension_clean)) == 0
}

test_an_extension_through_cd_is_clean if {
	count(findings(data.fixtures.extension_cd_form)) == 0
}

test_whitaker_with_the_extension_manifest_is_clean if {
	count(findings(data.fixtures.extension_with_whitaker)) == 0
}

test_an_explicit_list_of_every_target_kind_counts_as_all_targets if {
	count(findings(data.fixtures.designed_explicit_target_list)) == 0
}

test_feature_lanes_whose_union_covers_every_feature_are_clean if {
	count(findings(data.fixtures.designed_feature_lanes_cover_everything)) == 0
}

test_package_qualified_features_cover_the_feature if {
	count(findings(data.fixtures.designed_package_qualified_features)) == 0
}

test_the_gate_not_running_clippy_is_not_a_finding_here if {
	count(findings(data.fixtures.designed_clippy_not_run)) == 0
}

# -- LC-001 ---------------------------------------------------------------------------

test_clippy_without_all_targets_is_lc_001 if {
	ids(data.fixtures.designed_no_all_targets) == {"LC-001"}
	[msg] := messages_of(data.fixtures.designed_no_all_targets, "LC-001")
	contains(msg, "no clippy command checks all targets")
	contains(msg, "Makefile:4 (lint)")
	contains(msg, "--all-targets")
}

test_whitaker_without_all_targets_is_lc_001 if {
	ids(data.fixtures.extension_whitaker_misses_targets) == {"LC-001"}
	[msg] := messages_of(data.fixtures.extension_whitaker_misses_targets, "LC-001")
	contains(msg, "whitaker")
	contains(msg, "rust_extension/Cargo.toml")
}

test_a_reviewed_exemption_stands_for_the_target_lane if {
	list := [exemption("Cargo.toml", "clippy")]
	fs := findings_with(data.fixtures.designed_no_all_targets, object.union(default_parameters, {"exemptions": list}))
	count(fs) == 0
}

test_an_exemption_with_a_blank_reason_does_not_stand if {
	blank := object.union(exemption("Cargo.toml", "clippy"), {"reason": " "})
	fs := findings_with(data.fixtures.designed_no_all_targets, object.union(default_parameters, {"exemptions": [blank]}))
	count(fs) == 1
}

test_an_exemption_for_another_tool_or_surface_does_not_stand if {
	other_tool := exemption("Cargo.toml", "whitaker")
	other_surface := exemption("rust_extension/Cargo.toml", "clippy")
	fs := findings_with(data.fixtures.designed_no_all_targets, object.union(default_parameters, {"exemptions": [other_tool, other_surface]}))
	count(fs) == 1
}

# -- LC-002 ---------------------------------------------------------------------------

test_a_missing_feature_lane_is_lc_002_and_names_the_features if {
	ids(data.fixtures.designed_missing_feature_lane) == {"LC-002"}
	[msg] := messages_of(data.fixtures.designed_missing_feature_lane, "LC-002")
	contains(msg, "feature a/slow")
	not contains(msg, "a/fast")
	contains(msg, "--all-features")
}

test_a_package_qualified_feature_covers_only_its_own_package if {
	ids(data.fixtures.designed_both_packages_declare_fast) == {"LC-002"}
	[msg] := messages_of(data.fixtures.designed_both_packages_declare_fast, "LC-002")
	contains(msg, "feature b/fast")
	contains(msg, "feature b/slow")
	not contains(msg, "a/fast")
}

test_a_shell_conditional_lane_is_indeterminate_not_a_combined_lane if {
	fixture := data.fixtures.designed_feature_lanes_in_a_shell_conditional
	"EN-001" in ids(fixture)
	not "LC-002" in ids(fixture)
}

test_a_package_selection_limits_the_features_a_run_covers if {
	fixture := data.fixtures.designed_package_selected_features
	ids(fixture) == {"LC-002"}
	[msg] := messages_of(fixture, "LC-002")
	contains(msg, "feature b/slow")
	not contains(msg, "feature a/")
	not contains(msg, "b/fast")
}

test_a_bare_command_in_a_grown_workspace_selects_only_the_root_package if {
	fixture := data.fixtures.grown_bare_command_selects_only_the_root_package
	ids(fixture) == {"LC-002"}
	[msg] := messages_of(fixture, "LC-002")
	contains(msg, "feature b/fast")
	not contains(msg, "feature app/")
}

test_an_excluded_member_is_not_covered_by_a_workspace_run if {
	fixture := data.fixtures.designed_workspace_run_excludes_a_member
	ids(fixture) == {"LC-002"}
	[msg] := messages_of(fixture, "LC-002")
	contains(msg, "feature b/fast")
	not contains(msg, "feature a/")
}

test_an_exclude_pattern_leaves_out_every_package_it_matches if {
	fixture := data.fixtures.designed_workspace_run_excludes_a_pattern
	ids(fixture) == {"LC-002"}
	[msg] := messages_of(fixture, "LC-002")
	contains(msg, "feature b/fast")
	not contains(msg, "feature a/")
}

test_the_default_set_is_judged_for_each_package if {
	fixture := data.fixtures.designed_default_set_checked_for_one_package_only
	ids(fixture) == {"LC-002"}
	[msg] := messages_of(fixture, "LC-002")
	contains(msg, "the default feature set of b")
	not contains(msg, "the default feature set of a")
	not contains(msg, "feature b/")
	not contains(msg, "feature a/")
}

test_default_members_with_a_glob_leave_a_bare_run_indeterminate if {
	ids(data.fixtures.designed_default_members_glob_is_not_expanded) == {"EN-001"}
}

test_a_conditional_cd_leaves_the_commands_after_it_unplaced if {
	ids(data.fixtures.designed_conditional_cd_unplaces_what_follows) == {"EN-001"}
}

test_exported_flags_that_end_with_warn_do_not_deny if {
	ids(data.fixtures.designed_exported_flags_end_with_warn) == {"LC-003"}
}

test_a_workflow_run_is_not_settled_by_a_denying_cargo_config if {
	ids(data.fixtures.designed_config_denies_but_a_workflow_run_may_override) == {"EN-001"}
}

test_a_later_warn_flag_undoes_an_earlier_deny if {
	ids(data.fixtures.designed_later_warn_undoes_the_deny) == {"LC-003"}
}

test_a_makefile_export_does_not_reach_a_workflow_run if {
	ids(data.fixtures.designed_makefile_export_does_not_reach_a_workflow_run) == {"EN-001"}
}

test_unreadable_inputs_are_reported_even_without_a_surface if {
	ids(data.fixtures.no_surface_but_an_unreadable_workflow) == {"EN-001"}
}

test_a_lane_that_switches_the_default_set_off_never_checks_it if {
	ids(data.fixtures.designed_default_set_never_checked) == {"LC-002"}
	[msg] := messages_of(data.fixtures.designed_default_set_never_checked, "LC-002")
	contains(msg, "the default feature set")
}

test_a_grown_workspace_missing_a_lane_is_lc_002 if {
	ids(data.fixtures.grown_missing_feature_lane) == {"LC-002"}
}

test_an_extension_missing_lanes_is_lc_002_for_each_feature if {
	[msg] := messages_of(data.fixtures.extension_missing_lane, "LC-002")
	contains(msg, "feature extension-module")
	contains(msg, "feature test-util")
	not contains(msg, "feature python")
}

# -- LC-003 ---------------------------------------------------------------------------

test_a_command_that_does_not_deny_warnings_is_lc_003 if {
	ids(data.fixtures.designed_warnings_not_denied) == {"LC-003"}
	[msg] := messages_of(data.fixtures.designed_warnings_not_denied, "LC-003")
	contains(msg, "Makefile:4 (lint)")
	contains(msg, "RUSTFLAGS")
}

test_clippys_own_dash_d_warnings_after_the_separator_counts if {
	count(findings(data.fixtures.designed_warnings_denied_after_separator)) == 0
}

test_a_rustflags_assignment_without_the_deny_is_lc_003 if {
	ids(data.fixtures.designed_rustflags_without_deny) == {"LC-003"}
}

test_flags_the_makefile_exports_count if {
	count(findings(data.fixtures.designed_rustflags_exported)) == 0
}

test_exported_flags_without_the_deny_are_lc_003 if {
	ids(data.fixtures.designed_rustflags_exported_without_deny) == {"LC-003"}
}

test_exported_flags_the_audit_cannot_expand_leave_the_doc_gate_open if {
	fs := findings(data.fixtures.designed_rustdocflags_exported_unexpandable)
	{f.rule_id | some f in fs} == {"EN-001"}
	some f in fs
	contains(f.msg, "cannot tell whether warnings are denied")
}

test_a_cargo_config_that_denies_warnings_counts_for_clippy if {
	count(findings(data.fixtures.designed_config_denies_warnings)) == 0
}

test_a_workflow_command_that_says_nothing_is_indeterminate_not_a_violation if {
	ids(data.fixtures.designed_workflow_clippy_without_deny_is_unknown) == {"EN-001"}
}

test_a_workflow_command_that_denies_is_clean if {
	count(findings(data.fixtures.designed_workflow_clippy_denies)) == 0
}

# -- LC-004 ---------------------------------------------------------------------------

test_a_doc_run_that_excludes_a_package_or_documents_binaries_only_is_no_gate if {
	ids(data.fixtures.designed_doc_excludes_a_package) == {"LC-004"}
	ids(data.fixtures.designed_doc_of_binaries_only) == {"LC-004"}
}

test_no_cargo_doc_is_lc_004 if {
	ids(data.fixtures.designed_no_cargo_doc) == {"LC-004"}
	[msg] := messages_of(data.fixtures.designed_no_cargo_doc, "LC-004")
	contains(msg, "reaches no `cargo doc`")
	contains(msg, "cargo doc --no-deps --workspace")
}

test_cargo_doc_without_no_deps_is_lc_004 if {
	ids(data.fixtures.designed_doc_without_no_deps) == {"LC-004"}
	[msg] := messages_of(data.fixtures.designed_doc_without_no_deps, "LC-004")
	contains(msg, "--no-deps")
}

test_cargo_doc_over_one_package_of_two_is_lc_004 if {
	ids(data.fixtures.designed_doc_one_package_only) == {"LC-004"}
}

test_cargo_doc_naming_every_package_counts if {
	count(findings(data.fixtures.designed_doc_every_package_named)) == 0
}

test_cargo_doc_that_does_not_deny_warnings_is_lc_004_and_lc_003 if {
	ids(data.fixtures.designed_doc_warnings_not_denied) == {"LC-003", "LC-004"}
}

test_a_doc_target_lint_does_not_reach_is_not_a_gate if {
	ids(data.fixtures.designed_doc_only_in_a_target_lint_does_not_reach) == {"LC-004"}
}

test_a_grown_workspace_whose_doc_selects_the_root_package_only_is_lc_004 if {
	ids(data.fixtures.grown_doc_selects_the_root_package_only) == {"LC-004"}
}

test_an_extension_with_no_doc_gate_is_lc_004_at_its_manifest if {
	fs := findings(data.fixtures.extension_no_doc_gate)
	{f.rule_id | some f in fs} == {"LC-004"}
	some f in fs
	f.path == "rust_extension/Cargo.toml"
	contains(f.msg, "--manifest-path rust_extension/Cargo.toml")
}

# -- indeterminate, never guessed -----------------------------------------------------

test_a_lane_script_the_audit_does_not_read_is_en_001_with_its_lane_inputs if {
	fs := findings(data.fixtures.extension_lanes_built_by_a_script)
	some f in fs
	f.rule_id == "EN-001"
	f.verdict == "indeterminate"
	contains(f.msg, "scripts/lint_rust_lanes.py")
	contains(f.msg, "INPUT_LANES=none python test-util default all")
}

test_a_lane_script_is_not_judged_for_targets_features_or_warnings if {
	fs := findings(data.fixtures.extension_lanes_built_by_a_script)
	{f.rule_id | some f in fs} == {"EN-001", "LC-004"}
}

test_an_unresolved_word_among_the_arguments_is_en_001 if {
	fs := findings(data.fixtures.designed_unresolved_flags)
	{f.rule_id | some f in fs} == {"EN-001"}
	some f in fs
	contains(f.msg, "could not resolve")
}

test_an_unresolved_make_walk_makes_the_answer_indeterminate if {
	fs := findings(data.fixtures.designed_make_c_leaves_the_walk_unresolved)
	{f.rule_id | some f in fs} == {"EN-001"}
	some f in fs
	f.path == "Makefile"
}

test_a_command_for_a_manifest_no_surface_holds_is_unplaced if {
	fs := findings(data.fixtures.extension_manifest_no_surface_holds)
	"EN-001" in {f.rule_id | some f in fs}
	not "LC-001" in {f.rule_id | some f in fs}
}

test_an_unreadable_manifest_is_en_001 if {
	"EN-001" in ids(data.fixtures.unreadable_manifest)
	not "LC-001" in ids(data.fixtures.unreadable_manifest)
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
	every f in findings(data.fixtures.designed_no_all_targets) {
		f.severity == "warning"
	}
}

test_the_severity_parameter_promotes_findings_to_error if {
	fs := findings_with(data.fixtures.designed_no_all_targets, object.union(default_parameters, {"severity": "error"}))
	every f in fs {
		f.severity == "error"
	}
}
