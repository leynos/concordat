# BD-008 reads an isolated Whitaker route that follows `cd`, `unset` and
# environment-assignment segments, and counts a `cd` out of the checkout as
# isolation from its Cargo configuration.
package canon.lint_rules.rust_build_defaults_isolated_routes_test

import rego.v1

import data.canon.lint_rules.rust_build_defaults as policy

profile(findings) := {[f.rule_id, f.verdict] | some f in findings}

# -- the `&&` split ----------------------------------------------------------

# The second ampersand of `&&` must not stay on the segment it introduces.
test_a_chained_segment_keeps_no_leading_ampersand if {
	segments := policy.command_segments(`cd .. && unset X && RUSTFLAGS="" whitaker --all`)
	segments == [`cd .. `, ` unset X `, ` RUSTFLAGS="" whitaker --all`]
}

test_a_lone_ampersand_inside_a_segment_is_kept if {
	segments := policy.command_segments(`make test 2>&1 | tee log`)
	segments == [`make test 2>&1 `, ` tee log`]
}

test_isolated_whitaker_is_recognized_after_an_unset_segment if {
	segments := policy.command_segments(`unset X && RUSTFLAGS="-D warnings" whitaker --all`)
	some segment in segments
	policy.runs_isolated_whitaker(segment)
}

# -- a cd out of the checkout ------------------------------------------------

# cabochon's shape: the route leaves the checkout, so the Cranelift profile in
# the checkout's own .cargo/config.toml is never discovered.
test_a_whitaker_route_after_a_cd_to_the_parent_is_isolated_from_a_config_backend if {
	findings := policy.deny with input as data.fixtures.whitaker_chained_outside
	count(findings) == 0
}

# Without the cd the same recipe still runs beside the Cranelift configuration,
# which a RUSTFLAGS assignment cannot displace.
test_the_same_route_without_the_cd_stays_unproved if {
	findings := policy.deny with input as data.fixtures.whitaker_chained_inside
	profile(findings) == {["BD-008", "indeterminate"]}
}

# A cd into a subdirectory stays inside the checkout, whose configuration Cargo
# still discovers from there.
test_a_cd_into_a_subdirectory_does_not_leave_the_checkout if {
	findings := policy.deny with input as data.fixtures.whitaker_chained_subdirectory
	profile(findings) == {["BD-008", "indeterminate"]}
}

# --manifest-path reads the root manifest wherever the route runs, so a
# manifest-selected Cranelift backend is not escaped by the cd.
test_a_manifest_backend_is_not_escaped_by_the_cd if {
	findings := policy.deny with input as data.fixtures.whitaker_chained_outside_manifest_backend
	profile(findings) == {["BD-008", "indeterminate"]}
}

# -- a gate build after a prefix is still a gate build ------------------------

test_a_gate_build_after_a_cd_or_unset_prefix_is_still_flagged if {
	findings := policy.deny with input as data.fixtures.gate_build_after_cd
	profile(findings) == {["BD-008", "noncompliant"]}
	count(findings) == 4
}

# -- the working directory is tracked in order ---------------------------------

# Cargo discovers configuration from the directory the tool runs in, so a `cd ..`
# that a later `cd` undoes is not an exit. The depth is relative to the checkout.
test_a_single_cd_to_the_parent_leaves_the_checkout if {
	policy.cwd_depth([`cd .. `, ` whitaker --all`], 1) == -1
}

test_a_cd_into_a_subdirectory_then_back_is_where_it_started if {
	policy.cwd_depth([`cd crates `, ` cd .. `, ` whitaker --all`], 2) == 0
}

test_two_cds_to_the_parent_stay_outside if {
	policy.cwd_depth([`cd .. `, ` cd .. `, ` whitaker --all`], 2) == -2
}

test_the_parent_named_absolutely_leaves_the_checkout if {
	policy.cwd_depth([`cd "$(dir $(CURDIR))" `, ` whitaker --all`], 1) == -1
}

test_a_cd_back_into_the_checkout_directory_is_inside_again if {
	policy.cwd_depth([`cd .. `, ` cd repo `, ` whitaker --all`], 2) == 0
}

test_an_absolute_parent_after_a_subdirectory_still_leaves if {
	policy.cwd_depth([`cd crates `, ` cd $(dir $(CURDIR)) `, ` whitaker --all`], 2) == -1
}

# A cd the policy does not understand never reads as leaving the checkout.
test_an_unknown_cd_resets_the_depth_to_the_checkout if {
	policy.cwd_depth([`cd .. `, ` cd /tmp `, ` whitaker --all`], 2) == 0
	policy.cwd_depth([`cd ../sibling `, ` whitaker --all`], 1) == 0
}

test_a_recipe_without_a_cd_runs_in_the_checkout if {
	policy.cwd_depth([` whitaker --all`], 0) == 0
}

# -- explicit LLVM overrides and env prefixes ----------------------------------

# statelet's shape: the configuration selects Cranelift for the dev profile and
# the route names LLVM for that profile in its own environment.
test_an_explicit_llvm_override_for_the_cranelift_profile_proves_isolation if {
	findings := policy.deny with input as data.fixtures.whitaker_profile_override
	count(findings) == 0
}

# Mutation: an override for some other profile leaves dev's Cranelift in force.
test_an_override_for_another_profile_stays_unproved if {
	findings := policy.deny with input as data.fixtures.whitaker_profile_override_wrong_profile
	profile(findings) == {["BD-008", "indeterminate"]}
}

# Mutation: no override at all is the unproved case above.
test_the_override_must_be_llvm if {
	findings := policy.deny with input as data.fixtures.whitaker_profile_override
	count(findings) == 0
	segment := ` RUSTFLAGS= CARGO_PROFILE_DEV_CODEGEN_BACKEND=cranelift whitaker --all `
	policy.suite_contamination(
		{"targets": ["lint"], "recipes": []},
		segment,
	)
}

test_a_whitaker_route_behind_env_unset_prefixes_is_recognized if {
	policy.runs_isolated_whitaker(`env -u RUSTFLAGS whitaker --all`)
	policy.runs_isolated_whitaker(`env -u CARGO_ENCODED_RUSTFLAGS -u RUSTFLAGS /opt/bin/whitaker --all`)
	policy.runs_isolated_whitaker(`env --unset=RUSTFLAGS --unset CARGO_ENCODED_RUSTFLAGS RUSTFLAGS= whitaker --all`)
	policy.runs_isolated_whitaker(`@env -u RUSTFLAGS DYLINT_RUSTFLAGS="-D warnings" whitaker --all`)
}

test_env_options_it_does_not_know_are_not_isolated_routes if {
	not policy.runs_isolated_whitaker(`env -i whitaker --all`)
	not policy.runs_isolated_whitaker(`env -u whitaker`)
	not policy.runs_isolated_whitaker(`env -u RUSTFLAGS cargo test -p whitaker`)
	not policy.runs_isolated_whitaker(`-u RUSTFLAGS whitaker --all`)
}

test_a_whitaker_route_behind_env_unset_is_compliant if {
	findings := policy.deny with input as data.fixtures.whitaker_env_unset
	count(findings) == 0
}

# Mutation: cargo behind the same prefix is a gate build, held to the flags.
test_cargo_behind_an_env_prefix_is_still_a_gate_build if {
	findings := policy.deny with input as data.fixtures.gate_env_prefixed_cargo
	profile(findings) == {["BD-008", "noncompliant"]}
}

# -- quoted text is not an environment operation --------------------------------

# Mutation: a quoted value that merely contains the override is not the override.
test_a_quoted_llvm_override_proves_nothing if {
	findings := policy.deny with input as data.fixtures.whitaker_quoted_override
	profile(findings) == {["BD-008", "indeterminate"]}
}

# Mutation: a quoted `-u RUSTFLAGS` is not a removal, so the exported
# development flags still reach the route.
test_a_quoted_unset_does_not_clear_exported_flags if {
	findings := policy.deny with input as data.fixtures.whitaker_quoted_unset
	profile(findings) == {["BD-008", "noncompliant"]}
	some f in findings
	contains(f.msg, "carries development")
}

test_only_the_routes_own_prefix_is_read if {
	policy.route_assigned_values(`CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm whitaker --all`, "CARGO_PROFILE_DEV_CODEGEN_BACKEND") == ["llvm"]
	policy.route_assigned_values(`NOTE=" CARGO_PROFILE_DEV_CODEGEN_BACKEND=llvm" whitaker --all`, "CARGO_PROFILE_DEV_CODEGEN_BACKEND") == []
	policy.route_assigned_values(`whitaker --all RUSTFLAGS=-Dwarnings`, "RUSTFLAGS") == []
	policy.route_assigned_values(`env -u RUSTFLAGS --unset=OTHER RUSTFLAGS= whitaker --all`, "RUSTFLAGS") == [""]
}

# Mutation: a Make variable that is not exported never reaches the route.
test_an_unexported_make_variable_proves_nothing if {
	findings := policy.deny with input as data.fixtures.whitaker_unexported_override
	profile(findings) == {["BD-008", "indeterminate"]}
}

# Mutation: an escaped space keeps the override inside another variable's value.
test_an_escaped_space_hides_no_override if {
	findings := policy.deny with input as data.fixtures.whitaker_escaped_override
	profile(findings) == {["BD-008", "indeterminate"]}
}

# Mutation: removing RUSTFLAGS is not isolation. The configuration's rustflags
# still apply, so a route that only unsets it stays unproved.
test_unsetting_rustflags_alone_does_not_prove_isolation if {
	findings := policy.deny with input as data.fixtures.whitaker_unset_only
	profile(findings) == {["BD-008", "indeterminate"]}
}

# Mutation: an unreadable prefix must not fall back to a safe Make-level value.
test_an_unreadable_prefix_does_not_fall_back_to_an_exported_value if {
	findings := policy.deny with input as data.fixtures.whitaker_escaped_with_export
	profile(findings) == {["BD-008", "indeterminate"]}
}
