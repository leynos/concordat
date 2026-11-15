# Tests for rust-test-environment. Each fixture in fixtures/data.json is the
# production envelope for a synthetic checkout (see fixtures/generate.py); the
# expected set is the exact rule and verdict pairs the policy must report, so a
# test fails on a missing finding and on an extra one.
package canon.lint_rules.rust_test_environment_test

import rego.v1

import data.canon.lint_rules.rust_test_environment as policy

reported(envelope) := {sprintf("%s/%s", [f.rule_id, f.verdict]) |
	some f in policy.deny with input as envelope
}

findings(envelope) := [f | some f in policy.deny with input as envelope]

# -- compliant and out of scope -----------------------------------------------------

test_compliant if reported(data.fixtures.compliant) == set()

test_compliant_dotted_config if reported(data.fixtures.compliant_dotted_config) == set()

test_compliant_string_entries if reported(data.fixtures.compliant_string_entries) == set()

test_compliant_expect_on_statement if reported(data.fixtures.compliant_expect_on_statement) == set()

test_compliant_expect_on_impl_adapter if reported(data.fixtures.compliant_expect_on_impl_adapter) == set()

test_compliant_comments_and_strings if reported(data.fixtures.compliant_comments_and_strings) == set()

test_compliant_other_lint_allowed if reported(data.fixtures.compliant_other_lint_allowed) == set()

test_compliant_syntax_error_elsewhere if reported(data.fixtures.compliant_syntax_error_elsewhere) == set()

test_compliant_workspace if reported(data.fixtures.compliant_workspace) == set()

test_compliant_member_own_config if reported(data.fixtures.compliant_member_own_config) == set()

test_compliant_unlisted_fixture_crate if reported(data.fixtures.compliant_unlisted_fixture_crate) == set()

test_compliant_wrapper_disallowed if reported(data.fixtures.compliant_wrapper_disallowed) == set()

test_compliant_figment_without_test_feature if reported(data.fixtures.compliant_figment_without_test_feature) == set()

test_cargo_lints_deny_is_fine if reported(data.fixtures.cargo_lints_deny_is_fine) == set()

test_cargo_config_unrelated_flag if reported(data.fixtures.cargo_config_unrelated_flag) == set()

test_no_cargo_manifest_is_not_audited if reported(data.fixtures.no_cargo_manifest) == set()

# -- TE-001 ------------------------------------------------------------------------

test_no_clippy_config if reported(data.fixtures.no_clippy_config) == {"TE-001/noncompliant"}

test_no_clippy_config_names_the_member if {
	[f] := findings(data.fixtures.no_clippy_config)
	f.path == "."
	contains(f.msg, "std::env::set_current_dir")
}

test_config_without_disallowed_methods if reported(data.fixtures.config_without_disallowed_methods) == {"TE-001/noncompliant"}

test_missing_set_current_dir if reported(data.fixtures.missing_set_current_dir) == {"TE-001/noncompliant"}

test_missing_set_current_dir_names_the_path if {
	[f] := findings(data.fixtures.missing_set_current_dir)
	contains(f.msg, "std::env::set_current_dir")
	not contains(f.msg, "std::env::set_var")
	f.path == "clippy.toml"
}

test_member_config_shadows_root if {
	[f] := findings(data.fixtures.member_config_shadows_root)
	f.rule_id == "TE-001"
	f.path == "crates/a/clippy.toml"
}

test_members_sharing_a_config_are_one_finding if {
	[f] := findings(data.fixtures.workspace_sharing_an_incomplete_config)
	f.rule_id == "TE-001"
	f.path == "clippy.toml"
	contains(f.msg, "2 workspace member(s) (crates/a, crates/b)")
}

test_member_without_inherited_config if reported(data.fixtures.member_without_inherited_config) == {"TE-001/noncompliant"}

# -- TE-002 ------------------------------------------------------------------------

test_wrapper_not_disallowed if reported(data.fixtures.wrapper_not_disallowed) == {"TE-002/noncompliant"}

test_wrapper_names_the_missing_paths if {
	[f] := findings(data.fixtures.wrapper_not_disallowed)
	contains(f.msg, "temp_env::with_var")
	contains(f.msg, "temp_env::with_vars_unset")
}

test_wrapper_renamed if reported(data.fixtures.wrapper_renamed) == {"TE-002/noncompliant"}

test_wrapper_async_feature_inherited if {
	[f] := findings(data.fixtures.wrapper_inherited_from_workspace)
	f.rule_id == "TE-002"
	contains(f.msg, "temp_env::async_with_vars")
}

test_wrapper_target_table if reported(data.fixtures.wrapper_target_table) == {"TE-002/noncompliant"}

test_wrapper_figment_test_feature if reported(data.fixtures.wrapper_figment_test_feature) == {"TE-002/noncompliant"}

# -- TE-003 ------------------------------------------------------------------------

test_allow_on_function if reported(data.fixtures.allow_on_function) == {"TE-003/noncompliant"}

test_allow_reports_the_line if {
	[f] := findings(data.fixtures.allow_on_function)
	f.path == "src/lib.rs"
	f.line == 1
}

test_allow_crate_level if reported(data.fixtures.allow_crate_level) == {"TE-003/noncompliant"}

test_allow_in_cfg_attr if reported(data.fixtures.allow_in_cfg_attr) == {"TE-003/noncompliant"}

test_allow_group_style if reported(data.fixtures.allow_group_style) == {"TE-003/noncompliant"}

test_allow_group_all_in_a_list if reported(data.fixtures.allow_group_all_in_a_list) == {"TE-003/noncompliant"}

test_test_file_allow_is_read if {
	[f] := findings(data.fixtures.test_file_allow_is_read)
	f.path == "tests/it.rs"
}

test_expect_without_reason if reported(data.fixtures.expect_without_reason) == {"TE-003/noncompliant"}

test_expect_blank_reason if reported(data.fixtures.expect_blank_reason) == {"TE-003/noncompliant"}

test_expect_crate_level if reported(data.fixtures.expect_crate_level) == {"TE-003/noncompliant"}

test_expect_on_module if reported(data.fixtures.expect_on_module) == {"TE-003/noncompliant"}

test_expect_on_group if reported(data.fixtures.expect_on_group) == {"TE-003/noncompliant"}

# -- TE-004 ------------------------------------------------------------------------

test_cargo_lints_allow if reported(data.fixtures.cargo_lints_allow) == {"TE-004/noncompliant"}

test_cargo_lints_table_form if reported(data.fixtures.cargo_lints_table_form) == {"TE-004/noncompliant"}

test_cargo_workspace_lints if reported(data.fixtures.cargo_workspace_lints) == {"TE-004/noncompliant"}

test_cargo_config_rustflags if reported(data.fixtures.cargo_config_rustflags) == {"TE-004/noncompliant"}

test_cargo_config_joined_flag if reported(data.fixtures.cargo_config_joined_flag) == {"TE-004/noncompliant"}

# -- EN-001 ------------------------------------------------------------------------

test_clippy_conf_dir_set if reported(data.fixtures.clippy_conf_dir_set) == {"EN-001/indeterminate"}

test_clippy_toml_invalid_is_indeterminate_not_missing if reported(data.fixtures.clippy_toml_invalid) == {"EN-001/indeterminate"}

test_cargo_manifest_invalid if reported(data.fixtures.cargo_manifest_invalid) == {"EN-001/indeterminate"}

test_syntax_error_names_the_lint if reported(data.fixtures.syntax_error_names_the_lint) == {"EN-001/indeterminate"}

test_not_an_envelope_is_indeterminate if reported({"kind": "something-else"}) == {"EN-001/indeterminate"}
