# BD-008 preserves installer-managed Whitaker isolation without losing wrappers.
package canon.lint_rules.rust_build_defaults_isolated_whitaker_test

import rego.v1

import data.canon.lint_rules.rust_build_defaults as policy

fixture := data.fixtures.gate_isolated_whitaker

profile(findings) := {[f.rule_id, f.verdict] | some f in findings}

test_isolated_whitaker_and_development_cargo_routes_are_compliant if {
	findings := policy.deny with input as fixture
	count(findings) == 0
}

# Clearing the common Make variable spoils both direct Cargo and its wrapper.
test_cargo_and_python_test_runner_still_require_the_development_flags if {
	variables := [variable |
		some variable in fixture.makefile.variables
		variable.name != "STANDARD_RUSTFLAGS"
	]
	makefile := object.union(fixture.makefile, {"variables": variables})
	findings := policy.deny with input as object.union(fixture, {"makefile": makefile})
	profile(findings) == {["BD-008", "noncompliant"]}
	lines := {finding.line | some finding in findings}
	lines == {6, 10}
	count(findings) == 4
}

test_target_specific_warning_flags_on_whitaker_alone_are_exempt if {
	findings := policy.deny with input as fixture
		with data.parameters as {"gate_targets": ["isolated-only"]}
	count(findings) == 0
}

test_whitaker_as_a_cargo_argument_is_not_an_isolated_invocation if {
	not policy.runs_isolated_whitaker(`RUSTFLAGS="-D warnings" cargo test -p whitaker`)
	not policy.runs_isolated_whitaker(`RUSTFLAGS="-D warnings" python test_runner.py whitaker`)
}

test_installed_whitaker_command_shapes_are_recognized if {
	policy.runs_isolated_whitaker(`RUSTFLAGS="-D warnings" whitaker --all`)
	policy.runs_isolated_whitaker(`env RUSTFLAGS="-D warnings" /opt/bin/whitaker --all`)
}
