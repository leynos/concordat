# Fixture tests for RT-022 to RT-027, the Rust toolchain policy.
package canon.lint_rules.rust_toolchain_policy_test

import rego.v1

import data.canon.lint_rules.rust_toolchain_policy as policy

params := {
  "window_days": 56,
  "dependabot_intervals": ["daily", "weekly"],
}

# The findings as [rule, severity, verdict, path, message] tuples.
audit(fixture) := {
  [finding.rule_id, finding.severity, finding.verdict, finding.path, finding.msg] |
  some finding in policy.deny with input as fixture with data.parameters as params
}

rules(fixture) := {finding[0] | some finding in audit(fixture)}

# A fresh dated nightly, moved by Dependabot, with an MSRV that CI exercises.
test_compliant if {
  count(audit(data.fixtures.compliant)) == 0
}

test_wrong_kind_is_refused if {
  rules(data.fixtures.wrong_kind) == {"RT-022"}
  some finding in audit(data.fixtures.wrong_kind)
  finding[2] == "indeterminate"
}

test_a_repository_without_rust_is_outside_the_rule if {
  count(audit(data.fixtures.not_applicable)) == 0
}

# -- RT-022 ---------------------------------------------------------------------

test_no_pin if {
  audit(data.fixtures.no_pin) == {[
    "RT-022", "error", "noncompliant", "rust-toolchain.toml",
    "no rust-toolchain.toml: the toolchain is whatever the machine has, so the build and the Dependabot bump have nothing to agree on",
  ]}
}

test_unreadable_pin_is_indeterminate if {
  audit(data.fixtures.unreadable_pin) == {[
    "RT-022", "error", "indeterminate", "rust-toolchain.toml",
    "rust-toolchain.toml could not be read: boom",
  ]}
}

test_a_pin_with_no_channel if {
  audit(data.fixtures.no_channel) == {[
    "RT-022", "error", "noncompliant", "rust-toolchain.toml",
    "rust-toolchain.toml names no channel",
  ]}
}

test_floating_stable_is_not_exact if {
  rules(data.fixtures.floating_stable) == {"RT-022"}
}

test_a_numeric_release_is_exact if {
  count(audit(data.fixtures.numeric_pin)) == 0
}

# -- RT-023 ---------------------------------------------------------------------

test_an_old_nightly if {
  some finding in audit(data.fixtures.old_nightly)
  finding[0] == "RT-023"
  finding[4] == "nightly-2026-03-26 is 199 days old, beyond the 56-day window"
}

test_the_window_edge_is_inclusive if {
  count({f | some f in audit(data.fixtures.edge_nightly); f[0] == "RT-023"}) == 0
}

test_one_day_past_the_window if {
  some finding in audit(data.fixtures.just_over_nightly)
  finding[0] == "RT-023"
  finding[4] == "nightly-2026-08-15 is 57 days old, beyond the 56-day window"
}

test_the_window_is_a_parameter if {
  findings := policy.deny with input as data.fixtures.old_nightly
    with data.parameters as object.union(params, {"window_days": 365})
  count({f | some f in findings; f.rule_id == "RT-023"}) == 0
}

# -- Exceptions (RT-023, RT-024) --------------------------------------------------

test_a_current_exception_exempts_age_and_movability if {
  count(audit(data.fixtures.excepted_old_nightly)) == 0
}

# A nightly coupled to compiler internals has no stable MSRV to declare.
test_a_current_exception_exempts_the_msrv_of_a_library if {
  count(audit(data.fixtures.excepted_library_without_msrv)) == 0
}

test_a_stale_exception_exempts_nothing if {
  rules(data.fixtures.stale_exception) == {"RT-023", "RT-024"}
}

# -- RT-024 ---------------------------------------------------------------------

test_no_dependabot_entry if {
  rules(data.fixtures.no_dependabot_entry) == {"RT-024"}
}

test_no_dependabot_file if {
  rules(data.fixtures.no_dependabot_file) == {"RT-024"}
}

test_unreadable_dependabot_is_indeterminate if {
  audit(data.fixtures.dependabot_unreadable) == {[
    "RT-024", "error", "indeterminate", ".github/dependabot.yml",
    "Dependabot configuration could not be read: invalid YAML: boom",
  ]}
}

test_a_monthly_entry_does_not_keep_the_pin_moving if {
  rules(data.fixtures.dependabot_monthly) == {"RT-024"}
}

test_a_weekly_entry_does_keep_the_pin_moving if {
  count(audit(data.fixtures.dependabot_weekly)) == 0
}

test_an_entry_for_another_directory_does_not_move_the_root_pin if {
  rules(data.fixtures.dependabot_wrong_directory) == {"RT-024"}
}

test_the_accepted_intervals_are_a_parameter if {
  findings := policy.deny with input as data.fixtures.dependabot_monthly
    with data.parameters as object.union(params, {"dependabot_intervals": ["monthly"]})
  count({f | some f in findings; f.rule_id == "RT-024"}) == 0
}

test_the_accepted_intervals_can_exclude_daily if {
  findings := policy.deny with input as data.fixtures.compliant
    with data.parameters as object.union(params, {"dependabot_intervals": ["weekly"]})
  some finding in findings
  finding.rule_id == "RT-024"
}

# -- RT-025 ---------------------------------------------------------------------

test_a_publishable_library_needs_an_msrv if {
  audit(data.fixtures.lib_without_msrv) == {[
    "RT-025", "warning", "noncompliant", "Cargo.toml",
    "publishable library crate demo declares no rust-version, so its users cannot tell which compiler it supports",
  ]}
}

test_an_application_needs_no_msrv if {
  count(audit(data.fixtures.app_without_msrv)) == 0
}

test_an_unpublished_library_needs_no_msrv if {
  count(audit(data.fixtures.unpublished_lib_without_msrv)) == 0
}

test_a_workspace_msrv_is_inherited if {
  count(audit(data.fixtures.inherited_msrv)) == 0
}

test_inheriting_a_missing_workspace_msrv_is_missing if {
  rules(data.fixtures.inherits_nothing) == {"RT-025"}
}

# -- RT-026 ---------------------------------------------------------------------

test_an_msrv_ci_never_runs if {
  audit(data.fixtures.msrv_not_in_ci) == {[
    "RT-026", "error", "noncompliant", "Cargo.toml",
    "rust-version 1.85 is declared but no workflow installs that toolchain, runs cargo-msrv or has an msrv job",
  ]}
}

test_a_patch_spelling_matches_its_minor if {
  count(audit(data.fixtures.msrv_patch_spelling)) == 0
}

test_the_pin_can_be_the_msrv if {
  count(audit(data.fixtures.msrv_is_the_pin)) == 0
}

test_an_msrv_job_exercises_it if {
  count(audit(data.fixtures.msrv_job)) == 0
}

test_reading_the_manifest_msrv_exercises_it if {
  count(audit(data.fixtures.msrv_read_from_manifest)) == 0
}

test_a_workspace_msrv_is_judged_too if {
  some finding in audit(data.fixtures.workspace_msrv_not_in_ci)
  finding[0] == "RT-026"
}

test_an_unreadable_workflow_makes_rt026_indeterminate if {
  some finding in audit(data.fixtures.workflow_unreadable)
  finding[0] == "RT-026"
  finding[2] == "indeterminate"
  not "noncompliant" in {f[2] | some f in audit(data.fixtures.workflow_unreadable); f[0] == "RT-026"}
}

# -- RT-027 ---------------------------------------------------------------------

test_a_competing_nightly if {
  audit(data.fixtures.ci_competing_nightly) == {[
    "RT-027", "error", "noncompliant", ".github/workflows",
    "a workflow names nightly-2026-03-26 but rust-toolchain.toml pins nightly-2026-09-20: CI and the file must not disagree about the toolchain",
  ]}
}
