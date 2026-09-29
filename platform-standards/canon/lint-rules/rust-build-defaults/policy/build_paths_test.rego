# Fixture tests for BD-007 to BD-009 of the rust-build-defaults rule package.
#
# Each test pins every finding a fixture produces, with its line and message,
# so a change to any clause's semantics or wording fails here.
package canon.lint_rules.rust_build_defaults_build_paths_test

import rego.v1

import data.canon.lint_rules.rust_build_defaults as policy

profile(findings) := {[f.rule_id, f.verdict, f.line, f.msg] | some f in findings}

# -- BD-007, Makefile: every coverage recipe selects LLVM ----------------------

# The estate's common shape: the recipe inherits Cranelift, which cannot instrument coverage.
test_coverage_make_unselected if {
	findings := policy.deny with input as data.fixtures.coverage_make_unselected
	profile(findings) == {
		["BD-007", "noncompliant", 4, "the coverage recipe runs cargo llvm-cov without selecting LLVM, but the development profile's default backend is Cranelift, which cannot instrument coverage"],
	}
}

# statelet's shape: the profile override as an environment prefix on a continuation line.
test_coverage_make_prefix if {
	findings := policy.deny with input as data.fixtures.coverage_make_prefix
	count(findings) == 0
}

# thysalion's shape: the override's value through a single-valued variable.
test_coverage_make_variable if {
	findings := policy.deny with input as data.fixtures.coverage_make_variable
	count(findings) == 0
}

# A Makefile-wide exported override reaches every recipe.
test_coverage_make_export if {
	findings := policy.deny with input as data.fixtures.coverage_make_export
	count(findings) == 0
}

# A target-specific exported override reaches that target's recipe.
test_coverage_make_target_variable if {
	findings := policy.deny with input as data.fixtures.coverage_make_target_variable
	count(findings) == 0
}

# `-Zcodegen-backend=llvm` in RUSTFLAGS follows the profile's flag, and rustc takes the last.
test_coverage_make_rustflags if {
	findings := policy.deny with input as data.fixtures.coverage_make_rustflags
	count(findings) == 0
}

# A dedicated profile selecting LLVM is accepted; naming the Cranelift profile is not.
test_coverage_make_profile if {
	findings := policy.deny with input as data.fixtures.coverage_make_profile
	profile(findings) == {
		["BD-007", "noncompliant", 5, "the wrong-profile recipe runs cargo llvm-cov without selecting LLVM, but the development profile's default backend is Cranelift, which cannot instrument coverage"],
	}
}

# The release profile does not select Cranelift, so a release-profile coverage run is LLVM.
test_coverage_make_release if {
	findings := policy.deny with input as data.fixtures.coverage_make_release
	count(findings) == 0
}

# When the test profile selects Cranelift too, the development override alone leaves it.
test_coverage_make_test_profile if {
	findings := policy.deny with input as data.fixtures.coverage_make_test_profile
	profile(findings) == {
		["BD-007", "noncompliant", 2, "the coverage-dev-only recipe runs cargo llvm-cov without selecting LLVM, but the development profile's default backend is Cranelift, which cannot instrument coverage"],
	}
}

# An override behind a conditionally assigned variable cannot be proven either way.
test_coverage_make_unresolved if {
	findings := policy.deny with input as data.fixtures.coverage_make_unresolved
	profile(findings) == {
		["BD-007", "indeterminate", 8, "the coverage recipe runs cargo llvm-cov without selecting LLVM, but the development profile's default backend is Cranelift, which cannot instrument coverage"],
	}
}

# A line that only prints `cargo llvm-cov` is not a coverage path.
test_coverage_make_mention if {
	findings := policy.deny with input as data.fixtures.coverage_make_mention
	count(findings) == 0
}

# A repository whose development profile is not Cranelift is out of BD-007's scope, even where its Makefile cannot be proven.
test_coverage_make_exception if {
	findings := policy.deny with input as data.fixtures.coverage_make_exception
	profile(findings) == {
		["BD-008", "indeterminate", 0, "the Makefile includes other files, so its variables cannot be proven"],
	}
}

# Where rustflags select Cranelift, an assigned RUSTFLAGS replaces them; inheriting keeps them.
test_coverage_make_rustflags_route if {
	findings := policy.deny with input as data.fixtures.coverage_make_rustflags_route
	profile(findings) == {
		["BD-007", "noncompliant", 5, "the coverage-inherited recipe runs cargo llvm-cov without selecting LLVM, but the development profile's default backend is Cranelift, which cannot instrument coverage"],
	}
}

# LLVM on the development profile itself is not the coverage profile: BD-005 still refuses it.
test_llvm_dev_profile if {
	findings := policy.deny with input as data.fixtures.llvm_dev_profile
	profile(findings) == {
		["BD-004", "noncompliant", 0, "the \"cranelift\" backend is neither the development-profile default nor refused by a recorded exception"],
		["BD-005", "noncompliant", 0, "profile.dev selects the \"llvm\" backend; the estate backend is \"cranelift\""],
	}
}

# A Makefile makeutil refused is indeterminate, and the other clauses still run.
test_makefile_refused if {
	findings := policy.deny with input as data.fixtures.makefile_refused
	profile(findings) == {
		["BD-007", "indeterminate", 0, "makeutil could not parse the Makefile, so its coverage recipes cannot be read: makeutil exited with status 2 for Makefile"],
		["BD-008", "indeterminate", 0, "makeutil could not parse the Makefile, so its RUSTFLAGS assignments cannot be read: makeutil exited with status 2 for Makefile"],
	}
}

# A recovered parse may have lost rules, so neither clause is proven.
test_makefile_recovered if {
	findings := policy.deny with input as data.fixtures.makefile_recovered
	profile(findings) == {
		["BD-007", "indeterminate", 0, "the Makefile parse was recovered from syntax errors, so its facts may be incomplete"],
		["BD-008", "indeterminate", 0, "the Makefile parse was recovered from syntax errors, so its facts may be incomplete"],
	}
}

# An included file may define anything, so neither clause is proven.
test_makefile_include if {
	findings := policy.deny with input as data.fixtures.makefile_include
	profile(findings) == {
		["BD-007", "indeterminate", 0, "the Makefile includes other files, so its variables cannot be proven"],
		["BD-008", "indeterminate", 0, "the Makefile includes other files, so its variables cannot be proven"],
	}
}

# -- BD-007, workflows: coverage steps and dev-profile release builds ----------

# shared-actions generate-coverage selects LLVM itself, whatever the step's environment.
test_coverage_workflow_action if {
	findings := policy.deny with input as data.fixtures.coverage_workflow_action
	count(findings) == 0
}

# A direct `cargo llvm-cov` needs the override from the step, job, workflow, or line.
test_coverage_workflow_run if {
	findings := policy.deny with input as data.fixtures.coverage_workflow_run
	profile(findings) == {
		["BD-007", "indeterminate", 0, "job \"expression\" (Coverage) runs cargo llvm-cov without selecting LLVM, but the development profile's default backend is Cranelift"],
		["BD-007", "noncompliant", 0, "job \"unselected\" (Coverage) runs cargo llvm-cov without selecting LLVM, but the development profile's default backend is Cranelift"],
	}
}

# A release- or tag-triggered build without --release, -r or --profile builds through the development profile.
test_release_workflow if {
	findings := policy.deny with input as data.fixtures.release_workflow
	profile(findings) == {
		["BD-007", "noncompliant", 0, "job \"build\" (Dev-profile build) builds a release through the development profile without selecting LLVM, but the development profile's default backend is Cranelift"],
	}
}

# -- BD-008: a gate recipe that assigns RUSTFLAGS restates the fast flags ------

# netsuke's shape: the mold flag behind `$(if $(filter Linux,...))`, the assignment inside a variable.
test_gate_netsuke if {
	findings := policy.deny with input as data.fixtures.gate_netsuke
	count(findings) == 0
}

# The estate template: `RUST_FLAGS` assigned twice and never carrying the fast flags.
test_gate_template if {
	findings := policy.deny with input as data.fixtures.gate_template
	profile(findings) == {
		["BD-008", "noncompliant", 6, "the test recipe sets RUSTFLAGS without \"-Clink-arg=-fuse-ld=mold\"; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it"],
		["BD-008", "noncompliant", 6, "the test recipe sets RUSTFLAGS without \"-Zthreads=8\"; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it"],
	}
}

# An inline `$(shell uname -s)` Linux condition and split `-Z threads=8` spelling.
test_gate_inline_linux if {
	findings := policy.deny with input as data.fixtures.gate_inline_linux
	count(findings) == 0
}

# A target-specific RUSTFLAGS is an assignment like any other.
test_gate_target_variable if {
	findings := policy.deny with input as data.fixtures.gate_target_variable
	profile(findings) == {
		["BD-008", "noncompliant", 1, "the test target-specific assignment sets RUSTFLAGS without \"-Clink-arg=-fuse-ld=mold\"; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it"],
	}
}

# A Makefile-wide RUSTFLAGS reaches every gate recipe, its appends read together.
test_gate_global if {
	findings := policy.deny with input as data.fixtures.gate_global
	profile(findings) == {
		["BD-008", "noncompliant", 1, "the Makefile-wide assignment sets RUSTFLAGS without \"-Clink-arg=-fuse-ld=mold\"; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it"],
	}
}

# Coverage and release-profile recipes are BD-007's, and targets outside the gates are not judged.
test_gate_exempt if {
	findings := policy.deny with input as data.fixtures.gate_exempt
	count(findings) == 0
}

# A literal `$(MAKE) target` delegation is followed.
test_gate_delegated if {
	findings := policy.deny with input as data.fixtures.gate_delegated
	profile(findings) == {
		["BD-008", "noncompliant", 5, "the clippy recipe sets RUSTFLAGS without \"-Clink-arg=-fuse-ld=mold\"; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it"],
		["BD-008", "noncompliant", 5, "the clippy recipe sets RUSTFLAGS without \"-Zthreads=8\"; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it"],
	}
}

# Long options and options with attached values precede the delegated target.
test_gate_delegated_options if {
	findings := policy.deny with input as data.fixtures.gate_delegated_options
	profile(findings) == {
		["BD-008", "noncompliant", 5, "the clippy recipe sets RUSTFLAGS without \"-Clink-arg=-fuse-ld=mold\"; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it"],
		["BD-008", "noncompliant", 5, "the clippy recipe sets RUSTFLAGS without \"-Zthreads=8\"; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it"],
	}
}

# `-f` and `--file` name another makefile, so the delegation cannot be followed.
test_gate_dynamic_file if {
	findings := policy.deny with input as data.fixtures.gate_dynamic_file
	profile(findings) == {
		["BD-008", "indeterminate", 0, "the \"test\" target reaches a dynamic recursive Make invocation, so its recipes cannot be proven"],
	}
}

# A computed `$(MAKE) $(VAR)` delegation cannot be followed.
test_gate_dynamic if {
	findings := policy.deny with input as data.fixtures.gate_dynamic
	profile(findings) == {
		["BD-008", "indeterminate", 0, "the \"test\" target reaches a dynamic recursive Make invocation, so its recipes cannot be proven"],
	}
}

# A stable pin rejects `-Zthreads`, so only the linker flag is asked for.
test_gate_stable if {
	findings := policy.deny with input as data.fixtures.gate_stable
	profile(findings) == {
		["BD-008", "noncompliant", 2, "the test recipe sets RUSTFLAGS without \"-Clink-arg=-fuse-ld=mold\"; an assigned RUSTFLAGS replaces every rustflags source in the Cargo configuration, so the gate build loses it"],
	}
}

# -- BD-009: a direct cargo gate step keeps the fast flags ---------------------

# setup-rust exports `-D warnings` by default; an empty or fast input keeps the flags, and make is BD-008's.
test_workflow_setup_rust if {
	findings := policy.deny with input as data.fixtures.workflow_setup_rust
	profile(findings) == {
		["BD-009", "noncompliant", 0, "job \"default-input\" (Test) runs cargo with the RUSTFLAGS the toolchain action exports (\"-D warnings\"), which lacks \"-Clink-arg=-fuse-ld=mold\" and replaces every rustflags source in the Cargo configuration"],
		["BD-009", "noncompliant", 0, "job \"default-input\" (Test) runs cargo with the RUSTFLAGS the toolchain action exports (\"-D warnings\"), which lacks \"-Zthreads=8\" and replaces every rustflags source in the Cargo configuration"],
	}
}

# The linker is asked for on a literal Linux runner only, and threads not of a stable toolchain.
test_workflow_rustflags_env if {
	findings := policy.deny with input as data.fixtures.workflow_rustflags_env
	profile(findings) == {
		["BD-009", "indeterminate", 0, "job \"expression\" (Test) runs cargo with the step's RUSTFLAGS set from an expression, so its flags cannot be read"],
		["BD-009", "noncompliant", 0, "job \"macos\" (Test) runs cargo with the step's RUSTFLAGS (\"-D warnings\"), which lacks \"-Zthreads=8\" and replaces every rustflags source in the Cargo configuration"],
		["BD-009", "noncompliant", 0, "job \"stable\" (Test) runs cargo with the RUSTFLAGS the toolchain action exports (\"-D warnings\"), which lacks \"-Clink-arg=-fuse-ld=mold\" and replaces every rustflags source in the Cargo configuration"],
	}
}
