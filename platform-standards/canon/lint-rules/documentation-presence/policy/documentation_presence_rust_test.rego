# Tests for the Rust rules, DG-003 and DG-004. The fixtures are the production
# envelopes for synthetic checkouts (see fixtures/generate.py); the expected
# set is the exact rule and verdict pairs the policy must report.
package canon.lint_rules.documentation_presence_test

import rego.v1

import data.canon.lint_rules.documentation_presence as policy

rust_reported(envelope) := {sprintf("%s/%s", [f.rule_id, f.verdict]) |
	some f in policy.deny with input as envelope
}

rust_findings(envelope) := [f | some f in policy.deny with input as envelope]

# -- compliant ------------------------------------------------------------------------

test_rust_compliant_workspace if rust_reported(data.fixtures.rust_compliant_workspace) == set()

test_rust_compliant_own_table if rust_reported(data.fixtures.rust_compliant_own_table) == set()

test_rust_compliant_root_attribute if rust_reported(data.fixtures.rust_compliant_root_attribute) == set()

test_rust_compliant_cfg_attr_not_test if rust_reported(data.fixtures.rust_compliant_cfg_attr_not_test) == set()

test_rust_compliant_expect_with_reason if rust_reported(data.fixtures.rust_compliant_expect_with_reason) == set()

test_rust_binary_only if rust_reported(data.fixtures.rust_binary_only) == set()

test_rust_fixture_crate_ignored if rust_reported(data.fixtures.rust_fixture_crate_ignored) == set()

test_rust_clippy_through_prerequisite if rust_reported(data.fixtures.rust_clippy_through_prerequisite) == set()

# Flags built with Make functions the builder cannot expand do not hide the tool.
test_rust_clippy_with_unresolved_flags if rust_reported(data.fixtures.rust_clippy_with_unresolved_flags) == set()

test_rust_and_python_compliant if rust_reported(data.fixtures.rust_and_python_compliant) == set()

test_rust_root_attribute_beats_manifest if rust_reported(data.fixtures.rust_root_attribute_beats_manifest) == set()

# -- DG-003 ---------------------------------------------------------------------------

test_rust_manifest_unset if rust_reported(data.fixtures.rust_manifest_unset) == {"DG-003/noncompliant"}

test_rust_workspace_not_inherited if rust_reported(data.fixtures.rust_workspace_not_inherited) == {"DG-003/noncompliant"}

test_rust_one_member_unset if rust_reported(data.fixtures.rust_one_member_unset) == {"DG-003/noncompliant"}

test_rust_level_warn if rust_reported(data.fixtures.rust_level_warn) == {"DG-003/noncompliant"}

test_rust_root_allow_beats_manifest if rust_reported(data.fixtures.rust_root_allow_beats_manifest) == {"DG-003/noncompliant"}

test_rust_expect_without_reason if rust_reported(data.fixtures.rust_expect_without_reason) == {"DG-003/noncompliant"}

test_rust_cfg_attr_other_condition if rust_reported(data.fixtures.rust_cfg_attr_other_condition) == {"DG-003/indeterminate"}

test_rust_manifest_invalid if rust_reported(data.fixtures.rust_manifest_invalid) == {"DG-003/indeterminate"}

test_rust_root_missing if rust_reported(data.fixtures.rust_root_missing) == {"DG-003/indeterminate"}

# Module and item level suppressions need a reason; tests are other crates.
test_rust_item_expect_with_reason if rust_reported(data.fixtures.rust_item_expect_with_reason) == set()

test_rust_item_allow_without_reason if rust_reported(data.fixtures.rust_item_allow_without_reason) == {"DG-003/noncompliant"}

test_rust_item_expect_without_reason if rust_reported(data.fixtures.rust_item_expect_without_reason) == {"DG-003/noncompliant"}

test_rust_suppression_in_tests_ignored if rust_reported(data.fixtures.rust_suppression_in_tests_ignored) == set()

test_rust_item_allow_points_at_the_file if {
	findings := rust_findings(data.fixtures.rust_item_allow_without_reason)
	findings[0].path == "src/lib.rs"
	findings[0].line == 0
	contains(findings[0].msg, "line 2")
}

# Only the crate that fails is named, and the finding points at its manifest.
test_rust_one_member_unset_names_the_crate if {
	findings := rust_findings(data.fixtures.rust_one_member_unset)
	count(findings) == 1
	findings[0].path == "crates/extra/Cargo.toml"
	contains(findings[0].msg, "demo")
}

# A reason is what makes an `expect` an accepted exception.
test_rust_expect_reason_is_the_difference if {
	rust_reported(data.fixtures.rust_expect_without_reason) != set()
	rust_reported(data.fixtures.rust_compliant_expect_with_reason) == set()
}

# -- DG-004 ---------------------------------------------------------------------------

test_rust_no_makefile if rust_reported(data.fixtures.rust_no_makefile) == {"DG-004/noncompliant"}

test_rust_no_lint_target if rust_reported(data.fixtures.rust_no_lint_target) == {"DG-004/noncompliant"}

test_rust_lint_without_clippy if rust_reported(data.fixtures.rust_lint_without_clippy) == {"DG-004/noncompliant"}

test_rust_clippy_unresolved if rust_reported(data.fixtures.rust_clippy_unresolved) == {"DG-004/indeterminate"}

# A checkout that owns no Rust library is outside DG-003 and DG-004 entirely.
test_rust_rules_ignore_a_python_only_checkout if {
	findings := [f | some f in rust_findings(data.fixtures.compliant); f.rule_id in {"DG-003", "DG-004"}]
	findings == []
}

# Naming clippy is not running it: a component install or a probe is no gate.
test_rust_clippy_only_mentioned if rust_reported(data.fixtures.rust_clippy_only_mentioned) == {"DG-004/noncompliant"}

test_rust_clippy_through_sub_make if rust_reported(data.fixtures.rust_clippy_through_sub_make) == set()

# Attribute text in a doc comment or a string is not a suppression.
test_rust_suppression_text_in_string if rust_reported(data.fixtures.rust_suppression_text_in_string) == set()

# -- DG-004 exception: a recorded wrapper script ------------------------------------

test_rust_exception_valid if rust_reported(data.fixtures.rust_exception_valid) == set()

test_rust_exception_script_missing if rust_reported(data.fixtures.rust_exception_script_missing) == {"DG-004/noncompliant"}

test_rust_exception_script_without_clippy if rust_reported(data.fixtures.rust_exception_script_without_clippy) == {"DG-004/noncompliant"}

test_rust_exception_script_not_reached if rust_reported(data.fixtures.rust_exception_script_not_reached) == {"DG-004/noncompliant"}

test_rust_exception_names_no_script if rust_reported(data.fixtures.rust_exception_names_no_script) == {"DG-004/noncompliant"}

test_rust_exception_path_escape if rust_reported(data.fixtures.rust_exception_path_escape) == {"DG-004/noncompliant"}

# A recipe that already runs cargo clippy needs no exception, and a stale record is ignored.
test_rust_exception_unneeded if rust_reported(data.fixtures.rust_exception_unneeded) == set()

# The findings name the script and what is wrong with it.
test_rust_exception_script_missing_names_the_script if {
	some f in rust_findings(data.fixtures.rust_exception_script_missing)
	contains(f.msg, "scripts/other_lanes.sh")
	contains(f.msg, "does not exist")
}

test_rust_exception_script_without_clippy_says_why if {
	some f in rust_findings(data.fixtures.rust_exception_script_without_clippy)
	contains(f.msg, "does not run clippy as a cargo subcommand")
}

test_rust_exception_script_not_reached_says_why if {
	some f in rust_findings(data.fixtures.rust_exception_script_not_reached)
	contains(f.msg, "not named by any recipe")
}

test_rust_exception_names_no_script_says_so if {
	some f in rust_findings(data.fixtures.rust_exception_names_no_script)
	contains(f.msg, "names no script")
}
