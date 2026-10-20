package canon.lint_rules.rust_dev_build_footprint

import rego.v1

finding(id, verdict, path, msg) := {
	"rule_id": id, "severity": "error", "verdict": verdict,
	"path": path, "line": 0, "msg": msg,
}

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/rust-dev-build-footprint"
	is_array(input.surfaces)
}

deny contains finding("DF-000", "indeterminate", "", "invalid development-build policy input") if {
	not envelope_ok
}

surface_ok(surface) if {
	is_object(surface)
	is_string(surface.path)
	is_array(surface.errors)
	is_array(surface.profiles)
	is_array(surface.flags)
	surface.metadata_support in {"supported", "unsupported", "unknown"}
	base := [profile | some profile in surface.profiles; profile.source == "profile.dev"]
	count(base) == 1
	base[0].incremental != "unset"
	base[0].debug != "unset"
	every profile in surface.profiles {
		is_string(profile.source)
		profile.incremental in {"disabled", "enabled", "unset", "invalid"}
		profile.debug in {"disabled", "enabled", "unset", "invalid"}
	}
	count(surface.flags) > 0
	every source in surface.flags {
		is_string(source.source)
		source.incremental in {"disabled", "enabled", "unset", "invalid"}
		source.debug in {"disabled", "enabled", "unset", "invalid"}
		source.metadata in {"disabled", "enabled", "unset", "invalid"}
		is_boolean(object.get(source, "metadata_required", true))
	}
}

deny contains finding("DF-000", "indeterminate", "", "invalid development-build surface facts") if {
	envelope_ok
	some surface in input.surfaces
	not surface_ok(surface)
}

deny contains finding("DF-000", "indeterminate", surface.path, msg) if {
	envelope_ok
	some surface in input.surfaces
	surface_ok(surface)
	count(surface.errors) > 0
	msg := sprintf("cannot measure development defaults: %v", [surface.errors])
}

measured contains surface if {
	envelope_ok
	some surface in input.surfaces
	surface_ok(surface)
	count(surface.errors) == 0
}

setting_rule := {"incremental": "DF-001", "debug": "DF-002"}

state_verdict(state) := "indeterminate" if state == "invalid"

state_verdict(state) := "noncompliant" if state != "invalid"

# Defaults belong to the workspace. Package and build-script overrides must
# also stay small; a dependency wildcard cannot hide a named package override.
deny contains finding(id, state_verdict(state), surface.path, msg) if {
	some surface in measured
	some profile in surface.profiles
	some setting, id in setting_rule
	state := profile[setting]
	state in {"enabled", "invalid"}
	msg := sprintf("%s.%s must be disabled for default dev builds (observed %s)", [profile.source, setting, state])
}

# A compiler option overrides the profile Cargo passes first. Each replacing
# source is measured independently, preserving Cargo's target-source boundary.
deny contains finding(id, state_verdict(state), surface.path, msg) if {
	some surface in measured
	some source in surface.flags
	some setting, id in setting_rule
	state := source[setting]
	state in {"enabled", "invalid"}
	msg := sprintf("rustflags source %s re-enables or invalidates dev %s", [source.source, setting])
}

deny contains finding("DF-003", "indeterminate", surface.path, msg) if {
	some surface in measured
	surface.metadata_support == "unknown"
	msg := "embed-metadata support is unproven: use a dated nightly from 2025-06-26 onward or a stable/beta pin"
}

metadata_violation(source) if {
	object.get(source, "metadata_required", true)
	source.metadata != "disabled"
}

metadata_violation(source) if source.metadata in {"enabled", "invalid"}

deny contains finding("DF-003", state_verdict(source.metadata), surface.path, msg) if {
	some surface in measured
	surface.metadata_support == "supported"
	some source in surface.flags
	metadata_violation(source)
	msg := sprintf("rustflags source %s must retain -Zembed-metadata=no on this supported nightly", [source.source])
}

deny contains finding("DF-003", state_verdict(source.metadata), surface.path, msg) if {
	some surface in measured
	surface.metadata_support == "unsupported"
	some source in surface.flags
	source.metadata != "unset"
	msg := sprintf("rustflags source %s passes unstable embed-metadata to a stable/beta toolchain", [source.source])
}
