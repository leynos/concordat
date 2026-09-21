# Policy tests for the rust-build-defaults rule package.
#
# Fixture envelopes are supplied via `conftest verify --data fixtures/data.json`
# and appear under `data.fixtures`. Each test pins the exact finding set a
# fixture must produce, so any drift in policy semantics fails loudly.
#
# The suite is written in both directions. A clause proved only against
# violations refuses repositories that are legitimately different — a stable
# pin has no parallel frontend to configure, and the estate's one repository
# already carrying the linker flag spells it in two tokens rather than one.
package canon.lint_rules.rust_build_defaults_test

import rego.v1

import data.canon.lint_rules.rust_build_defaults as policy

# -- helpers ---------------------------------------------------------------

profile(findings) := {[f.rule_id, f.verdict] | some f in findings}

# -- the two compliant states the ruling admits ----------------------------

test_cranelift_as_the_development_default_is_compliant if {
	findings := policy.deny with input as data.fixtures.compliant_cranelift
	count(findings) == 0
}

test_a_recorded_exception_naming_the_pin_is_compliant if {
	findings := policy.deny with input as data.fixtures.compliant_exception
	count(findings) == 0
}

# The backend selected through `rustflags` carries no profile, so it reaches
# the development profile like every other build the source applies to.
test_a_backend_selected_through_rustflags_is_compliant if {
	findings := policy.deny with input as data.fixtures.compliant_rustflags_backend
	count(findings) == 0
}

# -- narrowness: cases that must produce no finding ------------------------

# axinite keys on the explicit triple and splits `-C` from its value. A
# checker written against one spelling would fail the estate's one repository
# that already carries the linker flag.
test_the_split_spelling_and_explicit_triple_are_compliant if {
	findings := policy.deny with input as data.fixtures.compliant_split_spelling
	count(findings) == 0
}

# `-Zthreads` is a nightly flag. Demanding it of a stable pin would break the
# build, so the clause is inapplicable rather than noncompliant.
test_a_stable_pin_is_not_asked_for_the_parallel_frontend if {
	findings := policy.deny with input as data.fixtures.compliant_stable_pin
	count(findings) == 0
}

# -- BD-001: the parallel frontend -----------------------------------------

test_no_configuration_at_all_fails_the_defaults if {
	findings := policy.deny with input as data.fixtures.no_config
	profile(findings) == {
		["BD-001", "noncompliant"],
		["BD-002", "noncompliant"],
		["BD-004", "noncompliant"],
	}
}

test_the_flag_dropped_from_the_linux_table_is_found if {
	findings := policy.deny with input as data.fixtures.threads_missing_from_linux
	profile(findings) == {["BD-001", "noncompliant"], ["BD-003", "noncompliant"]}
	some f in findings
	f.rule_id == "BD-001"
	contains(f.msg, "cfg(target_os")
}

# A checker that reads only `[build] rustflags` passes this and must not.
test_the_flag_dropped_from_the_build_table_is_found if {
	findings := policy.deny with input as data.fixtures.threads_missing_from_build
	profile(findings) == {["BD-001", "noncompliant"], ["BD-003", "noncompliant"]}
	some f in findings
	f.rule_id == "BD-001"
	contains(f.msg, "source \"build\"")
}

# A `grep` for the flag passes this fixture, which is why the facts come from
# a TOML parser instead.
test_a_flag_named_only_in_a_comment_is_not_configured if {
	findings := policy.deny with input as data.fixtures.commented_flags
	profile(findings) == {["BD-001", "noncompliant"], ["BD-002", "noncompliant"]}
	count([f | some f in findings; f.rule_id == "BD-001"]) == 2
}

# -- BD-002: the linker ----------------------------------------------------

test_the_linker_named_unconditionally_is_found if {
	findings := policy.deny with input as data.fixtures.linker_unconditional
	profile(findings) == {["BD-002", "noncompliant"]}
	count(findings) == 2
	some f in findings
	contains(f.msg, "ships for Linux only")
}

test_no_linux_table_configures_the_linker_nowhere if {
	findings := policy.deny with input as data.fixtures.no_linux_table
	profile(findings) == {["BD-002", "noncompliant"]}
	some f in findings
	contains(f.msg, "no target table applies on Linux")
}

# A `target_env` predicate is neither Linux nor not-Linux to this reader, and
# a guess in either direction would be a verdict the facts do not support.
test_an_unplaceable_target_key_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.unclassified_target
	profile(findings) == {["BD-002", "indeterminate"]}
}

# -- BD-003: the sources are repeated, not merged --------------------------

test_the_linker_flag_is_not_counted_as_drift if {
	findings := policy.deny with input as data.fixtures.compliant_exception
	count([f | some f in findings; f.rule_id == "BD-003"]) == 0
}

# -- BD-004 to BD-006: the codegen backend ---------------------------------

test_neither_backend_nor_exception_is_noncompliant if {
	findings := policy.deny with input as data.fixtures.no_backend_no_exception
	profile(findings) == {["BD-004", "noncompliant"]}
}

test_a_profile_key_without_the_unstable_gate_is_refused if {
	findings := policy.deny with input as data.fixtures.backend_without_gate
	profile(findings) == {["BD-005", "noncompliant"]}
	some f in findings
	contains(f.msg, "[unstable] codegen-backend = true")
}

test_an_unadopted_backend_is_refused if {
	findings := policy.deny with input as data.fixtures.other_backend
	profile(findings) == {
		["BD-004", "noncompliant"],
		["BD-005", "noncompliant"],
	}
}

test_an_unstable_key_under_a_stable_pin_is_refused if {
	findings := policy.deny with input as data.fixtures.backend_on_stable
	profile(findings) == {["BD-005", "noncompliant"]}
	some f in findings
	contains(f.msg, "stable cargo")
}

# The exception is current only while it names the pinned toolchain; a bump
# past that channel is what obliges the repository to measure again.
test_an_exception_measured_on_an_older_toolchain_is_stale if {
	findings := policy.deny with input as data.fixtures.stale_exception
	profile(findings) == {["BD-006", "noncompliant"]}
	some f in findings
	contains(f.msg, "nightly-2026-08-23")
}

test_an_exception_with_no_pin_to_measure_against_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.exception_without_pin
	profile(findings) == {["BD-006", "indeterminate"]}
}

# -- guards ----------------------------------------------------------------

test_an_unparsable_configuration_decides_nothing_else if {
	findings := policy.deny with input as data.fixtures.unreadable_config
	profile(findings) == {["CF-001", "indeterminate"]}
	count(findings) == 1
}

test_a_checkout_with_no_cargo_surface_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.not_rust
	profile(findings) == {["AP-001", "indeterminate"]}
}

test_an_unknown_schema_version_is_refused if {
	findings := policy.deny with input as data.fixtures.unknown_schema
	profile(findings) == {["EN-001", "indeterminate"]}
}

test_an_invalid_cargo_payload_is_refused if {
	findings := policy.deny with input as data.fixtures.invalid_cargo
	profile(findings) == {["EN-001", "indeterminate"]}
}

# -- unit-level readings ---------------------------------------------------

# The linker flag is held out of the drift comparison on purpose: it is the
# one flag that legitimately appears in a single source.
test_shared_flags_exclude_the_linker_flag if {
	flags := policy.shared_flags with input as data.fixtures.compliant_exception
	flags == {"-Zthreads=8"}
}

test_a_rustflags_backend_reaches_the_development_profile if {
	selected := policy.backend_configured with input as data.fixtures.compliant_rustflags_backend
	selected
}

# -- target classification, in both directions ------------------------------

# A negated expression contains the Linux predicate and applies everywhere but
# Linux. A substring test reads it as a Linux source and would then accept a
# Linux-only linker flag in a table macOS and Windows builds take.
test_a_negated_linux_cfg_is_not_a_linux_source if {
	findings := policy.deny with input as data.fixtures.negated_linux_cfg
	profile(findings) == {["BD-002", "indeterminate"]}
}

# `cfg(unix)` is a source Linux builds take, and so do macOS builds. The linker
# ships for Linux alone, so naming it here breaks the platform it reaches.
test_the_linker_in_a_source_reaching_beyond_linux_is_found if {
	findings := policy.deny with input as data.fixtures.linker_beyond_linux
	profile(findings) == {["BD-002", "noncompliant"]}
	some f in findings
	contains(f.msg, "applies beyond Linux")
}

# With no source placed, "the linker is configured nowhere" is not provable:
# the unplaceable source may be the Linux table. The indeterminate finding is
# the whole answer, and the noncompliant one must not accompany it.
test_an_unplaceable_sole_target_does_not_prove_the_linker_absent if {
	findings := policy.deny with input as data.fixtures.unclassified_only
	profile(findings) == {["BD-002", "indeterminate"]}
	count(findings) == 1
}

# -- the backend, in both directions ----------------------------------------

# Cargo applies a package override to the named package alone, so every other
# development build keeps the backend it had. That is not the profile default.
test_a_package_override_is_not_the_profile_default if {
	findings := policy.deny with input as data.fixtures.package_override_backend
	profile(findings) == {["BD-004", "noncompliant"]}
}

# An unreadable document may hold the exception, so neither state is proved.
test_an_unreadable_exception_document_proves_neither_state if {
	findings := policy.deny with input as data.fixtures.exception_unreadable
	profile(findings) == {["BD-004", "indeterminate"]}
	count(findings) == 1
}

# -- a configuration cargo refuses ------------------------------------------

# Cargo exits on `rustflags = ["-Zthreads=8", 42]`. Dropping the offending
# member and reading the rest would report a repository that cannot build as
# one carrying the standard.
test_a_configuration_cargo_refuses_decides_nothing if {
	findings := policy.deny with input as data.fixtures.non_string_rustflags
	profile(findings) == {["CF-001", "indeterminate"]}
	count(findings) == 1
}

# -- the parameters are read, not merely declared ---------------------------
#
# Every test above runs on the manifest defaults. An implementation that
# ignored `data.parameters` entirely would pass all of them, so each parameter
# is exercised here in both directions: a value that makes a compliant fixture
# fail, and a value that makes a failing fixture pass.

# The fixture is compliant on the default flag, so every finding here is one
# the parameter produced. Both sources are named, because each carries the
# default spelling and neither carries the configured one.
test_the_threads_flag_parameter_is_read if {
	findings := policy.deny with input as data.fixtures.compliant_exception
		with data.parameters as {"threads_flag": "-Zthreads=16"}
	profile(findings) == {["BD-001", "noncompliant"]}
	count(findings) == 2
}

test_the_linker_flag_parameter_is_read if {
	findings := policy.deny with input as data.fixtures.compliant_exception
		with data.parameters as {"linker_flag": "-Clink-arg=-fuse-ld=lld"}
	# The configured flag is absent from the Linux table, and the flag the
	# repository does name is no longer held out of the drift comparison.
	profile(findings) == {["BD-002", "noncompliant"], ["BD-003", "noncompliant"]}
}

# An empty platform list makes the linker clause inapplicable, which is the
# escape hatch for a repository that builds for no platform the linker ships
# on. Proved on the fixture that otherwise reports the linker configured
# nowhere.
test_an_empty_linker_platform_list_makes_the_clause_inapplicable if {
	findings := policy.deny with input as data.fixtures.no_linux_table
		with data.parameters as {"linker_platforms": []}
	count(findings) == 0
}

test_a_populated_linker_platform_list_keeps_the_clause if {
	findings := policy.deny with input as data.fixtures.no_linux_table
		with data.parameters as {"linker_platforms": ["linux"]}
	profile(findings) == {["BD-002", "noncompliant"]}
}

# The fixture selects Cranelift. Naming another backend as the estate's makes
# the same configuration a deviation, and the reverse holds on the fixture
# whose backend is not the default.
test_the_backend_parameter_is_read if {
	findings := policy.deny with input as data.fixtures.compliant_cranelift
		with data.parameters as {"codegen_backend": "experimental"}
	profile(findings) == {["BD-004", "noncompliant"], ["BD-005", "noncompliant"]}
}

test_the_backend_parameter_admits_the_repository_that_matches_it if {
	findings := policy.deny with input as data.fixtures.other_backend
		with data.parameters as {"codegen_backend": "experimental"}
	count(findings) == 0
}
