# rust-build-defaults: the estate's Rust build standard as a checkable property.
#
# Input is a policy-input/rust-build-defaults envelope. Its facts come from TOML
# parsers and a Markdown heading walk, never from a textual search of either
# file: `-Zthreads=8` appearing in a comment is not a configured flag, and the
# word `channel` appearing in a comment is not a pinned channel. The policy
# reasons only over those facts, and anything it cannot decide produces an
# `indeterminate` finding rather than a silent pass.
package canon.lint_rules.rust_build_defaults

import rego.v1

default threads_flag := "-Zthreads=8"

threads_flag := data.parameters.threads_flag

default linker_flag := "-Clink-arg=-fuse-ld=mold"

linker_flag := data.parameters.linker_flag

default linker_platforms := ["linux"]

linker_platforms := data.parameters.linker_platforms

default codegen_backend := "cranelift"

codegen_backend := data.parameters.codegen_backend

default config_path := ".cargo/config.toml"

config_path := input.cargo_config.path

finding(rule_id, verdict, path, msg) := {
	"rule_id": rule_id,
	"severity": "error",
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": msg,
}

# -- envelope and applicability --------------------------------------------

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/rust-build-defaults"
}

cargo_input := object.get(input, "cargo", {})

cargo_input_is_valid if is_object(cargo_input)

invalid_cargo_surfaces if not cargo_input_is_valid

invalid_cargo_surfaces if {
	cargo_input_is_valid
	not is_array(object.get(cargo_input, "surfaces", []))
}

cargo_surfaces := object.get(cargo_input, "surfaces", []) if not invalid_cargo_surfaces

cargo_surfaces := [] if invalid_cargo_surfaces

applicable if {
	envelope_ok
	not invalid_cargo_surfaces
	count(cargo_surfaces) > 0
}

deny contains f if {
	not envelope_ok
	f := finding(
		"EN-001", "indeterminate", "",
		"policy input is not a rust-build-defaults envelope of schema version 1",
	)
}

deny contains f if {
	envelope_ok
	invalid_cargo_surfaces
	f := finding(
		"EN-001", "indeterminate", "",
		"policy input cargo must be an object and cargo.surfaces an array when present",
	)
}

deny contains f if {
	envelope_ok
	not invalid_cargo_surfaces
	input.applicability.rust_surfaces_declared != true
	count(cargo_surfaces) == 0
	f := finding(
		"AP-001", "indeterminate", "",
		"no governed Cargo.toml surface is declared and root Cargo.toml is absent",
	)
}

# -- the Cargo configuration -----------------------------------------------

has_config if {
	applicable
	input.cargo_config != null
}

config_readable if {
	has_config
	input.cargo_config.parse_error == null
}

deny contains f if {
	has_config
	input.cargo_config.parse_error != null
	f := finding(
		"CF-001", "indeterminate", config_path,
		sprintf(
			"the Cargo configuration could not be parsed: %s",
			[input.cargo_config.parse_error],
		),
	)
}

rustflags_sources := input.cargo_config.sources if config_readable

rustflags_sources := [] if not config_readable

# A target table replaces `[build] rustflags` outright on the platform it
# matches, so a flag that has to survive is repeated in every source. The
# linker flag is the deliberate exception and is governed by BD-002.
shared_flags := {flag |
	some source in rustflags_sources
	some flag in source.flags
	flag != linker_flag
}

# -- BD-001: the parallel frontend is a default ----------------------------

# `-Zthreads` is a nightly flag. Demanding it of a stable pin would break the
# build, so the clause is inapplicable rather than noncompliant there.
nightly_pinned if {
	input.toolchain != null
	input.toolchain.channel_kind in {"nightly", "nightly-dated"}
}

toolchain_unreadable if {
	input.toolchain != null
	input.toolchain.parse_error != null
}

toolchain_unreadable if {
	input.toolchain != null
	input.toolchain.parse_error == null
	input.toolchain.channel != null
	input.toolchain.channel_kind == "unknown"
}

deny contains f if {
	applicable
	toolchain_unreadable
	f := finding(
		"TC-001", "indeterminate", input.toolchain.path,
		"the pinned toolchain channel could not be classified, so the nightly-only clauses cannot be decided",
	)
}

threads_clause_applies if {
	applicable
	nightly_pinned
	not toolchain_unreadable
}

deny contains f if {
	threads_clause_applies
	not has_config
	f := finding(
		"BD-001", "noncompliant", config_path,
		sprintf(
			"no auto-discovered Cargo configuration carries %q, so the parallel frontend is not a default",
			[threads_flag],
		),
	)
}

deny contains f if {
	threads_clause_applies
	config_readable
	count(rustflags_sources) == 0
	f := finding(
		"BD-001", "noncompliant", config_path,
		sprintf("the Cargo configuration names no rustflags source carrying %q", [threads_flag]),
	)
}

deny contains f if {
	threads_clause_applies
	config_readable
	some source in rustflags_sources
	not threads_flag in source.flags
	f := finding(
		"BD-001", "noncompliant", config_path,
		sprintf(
			"rustflags source %q does not carry %q, so builds it applies to lose the parallel frontend",
			[source.name, threads_flag],
		),
	)
}

# -- BD-002: the linker is configured, and only where it ships -------------

linker_clause_applies if {
	applicable
	count(linker_platforms) > 0
}

linux_sources := [source |
	some source in rustflags_sources
	source.kind == "target"
	source.linux == true
	source.classified == true
]

deny contains f if {
	linker_clause_applies
	not has_config
	f := finding(
		"BD-002", "noncompliant", config_path,
		sprintf(
			"no auto-discovered Cargo configuration carries %q, so the linker is not a default",
			[linker_flag],
		),
	)
}

# Only provable when every source was placed. An unclassified source may be
# the Linux table, so reporting that the linker is configured nowhere while one
# source could not be read is a verdict the facts do not support; the
# indeterminate finding below is the whole answer in that case.
every_source_classified if {
	every source in rustflags_sources {
		source.classified == true
	}
}

deny contains f if {
	linker_clause_applies
	config_readable
	every_source_classified
	count(linux_sources) == 0
	f := finding(
		"BD-002", "noncompliant", config_path,
		sprintf(
			"no target table applies on Linux, so %q is configured nowhere",
			[linker_flag],
		),
	)
}

deny contains f if {
	linker_clause_applies
	config_readable
	some source in linux_sources
	not linker_flag in source.flags
	f := finding(
		"BD-002", "noncompliant", config_path,
		sprintf("Linux rustflags source %q does not carry %q", [source.name, linker_flag]),
	)
}

# The linker ships for Linux alone, so naming it unconditionally or under
# another platform's table breaks the build there rather than speeding it up.
deny contains f if {
	linker_clause_applies
	config_readable
	some source in rustflags_sources
	source.linux_only == false
	source.classified == true
	linker_flag in source.flags
	f := finding(
		"BD-002", "noncompliant", config_path,
		sprintf(
			"rustflags source %q names %q, which ships for Linux only, and applies beyond Linux",
			[source.name, linker_flag],
		),
	)
}

deny contains f if {
	linker_clause_applies
	config_readable
	some source in rustflags_sources
	source.classified == false
	f := finding(
		"BD-002", "indeterminate", config_path,
		sprintf(
			"target key %q could not be placed on or off Linux, so the linker clause cannot be decided",
			[source.key],
		),
	)
}

# -- BD-003: the sources are repeated, not merged --------------------------

deny contains f if {
	applicable
	config_readable
	count(rustflags_sources) > 1
	some source in rustflags_sources
	some flag in shared_flags
	not flag in source.flags
	f := finding(
		"BD-003", "noncompliant", config_path,
		sprintf(
			"%q is named by another rustflags source but not by %q, which replaces it where it applies",
			[flag, source.name],
		),
	)
}

# -- BD-004 to BD-006: the codegen backend ---------------------------------

backends := input.cargo_config.backends if config_readable

backends := [] if not config_readable

# A `-Zcodegen-backend=` token in rustflags carries no profile, and applies to
# every build the source reaches, so it selects the development profile too.
# A package override does not: cargo applies it to the named package alone,
# leaving every other development build on whatever backend it had.
dev_backends := [backend |
	some backend in backends
	backend.scope == "profile"
	backend.profile == "dev"
]

dev_backends_from_flags := [backend |
	some backend in backends
	backend.scope == "rustflags"
]

backend_configured if {
	some backend in dev_backends
	backend.backend == codegen_backend
}

backend_configured if {
	some backend in dev_backends_from_flags
	backend.backend == codegen_backend
}

exception_sections := [section |
	some scan in object.get(input, "exceptions", [])
	some section in scan.sections
]

exception_recorded if count(exception_sections) > 0

deny contains f if {
	applicable
	some scan in object.get(input, "exceptions", [])
	scan.read_error != null
	f := finding(
		"BD-004", "indeterminate", scan.path,
		sprintf("the exception document could not be read: %s", [scan.read_error]),
	)
}

# A checkout with no configuration at all selects no backend either, so the
# clause is decided there as well as over a configuration that parsed.
backend_clause_applies if {
	applicable
	not has_config
}

backend_clause_applies if config_readable

exception_read_error if {
	some scan in object.get(input, "exceptions", [])
	scan.read_error != null
}

deny contains f if {
	backend_clause_applies
	not backend_configured
	not exception_recorded
	not exception_read_error
	f := finding(
		"BD-004", "noncompliant", config_path,
		sprintf(
			"the %q backend is neither the development-profile default nor refused by a recorded exception",
			[codegen_backend],
		),
	)
}

# A backend the estate has not adopted is a deviation in its own right: the
# clause accepts two states, and an unmeasured third backend is neither.
deny contains f if {
	applicable
	config_readable
	some backend in backends
	backend.backend != codegen_backend
	f := finding(
		"BD-005", "noncompliant", config_path,
		sprintf(
			"%s selects the %q backend; the estate backend is %q",
			[backend.source, backend.backend, codegen_backend],
		),
	)
}

# Cargo refuses a profile `codegen-backend` key unless the unstable feature is
# enabled, so the key without the gate is a configuration that does not load.
deny contains f if {
	applicable
	config_readable
	some backend in backends
	backend.scope in {"profile", "package-override"}
	input.cargo_config.unstable_codegen_backend != true
	f := finding(
		"BD-005", "noncompliant", config_path,
		sprintf(
			"%s names a codegen backend without `[unstable] codegen-backend = true`, which cargo refuses",
			[backend.source],
		),
	)
}

# An `[unstable]` key is rejected outright by a stable cargo, so the file stops
# working for every consumer not on the pinned nightly.
deny contains f if {
	applicable
	config_readable
	count(backends) > 0
	not nightly_pinned
	not toolchain_unreadable
	f := finding(
		"BD-005", "noncompliant", config_path,
		"a codegen backend is selected without a nightly pin, and a stable cargo rejects the unstable key it needs",
	)
}

# The exception is current only while it names the toolchain it was measured
# on. The finding states that and no more: whether a bump obliges a fresh
# measurement, or only a line saying none was taken, depends on decisions the
# repository has recorded elsewhere and this policy cannot read.
exception_names_pin if {
	input.toolchain != null
	input.toolchain.channel != null
	some section in exception_sections
	input.toolchain.channel in section.channels_named
}

deny contains f if {
	backend_clause_applies
	not backend_configured
	exception_recorded
	input.toolchain != null
	input.toolchain.channel != null
	not exception_names_pin
	f := finding(
		"BD-006", "noncompliant", exception_sections[0].path,
		sprintf(
			"the recorded exception names no measurement on %q, so the recorded state does not cover the pinned toolchain",
			[input.toolchain.channel],
		),
	)
}

deny contains f if {
	backend_clause_applies
	not backend_configured
	exception_recorded
	not pinned_channel_known
	f := finding(
		"BD-006", "indeterminate", exception_sections[0].path,
		"no toolchain channel is pinned, so the recorded exception cannot be shown to be current",
	)
}

pinned_channel_known if {
	input.toolchain != null
	input.toolchain.channel != null
}
