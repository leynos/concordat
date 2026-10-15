# Private BD-003 consistency checks for build-wide versus target-local backends.
package canon.lint_rules.rust_build_defaults

import rego.v1

# Backend rustflags from build apply globally, unlike target-local selections.
# Keep this asymmetric: disjoint targets need not share a backend.
build_backend_flags := {flag |
	some source in rustflags_sources
	source.kind == "build"
	some flag in source.flags
	startswith(flag, "-Zcodegen-backend=")
}

deny contains f if {
	applicable
	config_readable
	some source in rustflags_sources
	source.kind == "target"
	some flag in build_backend_flags
	not flag in source.flags
	f := finding(
		"BD-003", "noncompliant", config_path,
		sprintf(
			"%q is named by build rustflags but not by %q, which replaces it where it applies",
			[flag, source.name],
		),
	)
}

