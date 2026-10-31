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
