package canon.lint_rules.rust_dev_build_footprint

import rego.v1

finding(id, verdict, path, msg) := {
	"rule_id": id, "severity": "error", "verdict": verdict,
	"path": path, "line": 0, "msg": msg,
}

envelope_ok if {
	input.schema_version == 2
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
	is_object(surface.cargo_metadata)
	is_string(surface.cargo_metadata.setting)
	surface.cargo_metadata.state in {"disabled", "enabled", "unset", "invalid"}
	metadata_contract_ok(surface)
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
	}
}

metadata_contract_ok(surface) if {
	surface.metadata_support == "supported"
	surface.cargo_metadata.setting in {"no-embed-metadata", "embed-metadata"}
}

metadata_contract_ok(surface) if {
	surface.metadata_support != "supported"
	surface.cargo_metadata.setting == ""
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

# Cargo owns full-rmeta emission; target rustflags need no duplicated setting.
deny contains finding("DF-003", "indeterminate", surface.path, msg) if {
	some surface in measured
	surface.metadata_support == "unknown"
	msg := "native Cargo metadata capability is unproven for this pin, including the 2026-07-19..30 transition"
}

deny contains finding("DF-003", state_verdict(state), surface.path, msg) if {
	some surface in measured
	surface.metadata_support == "supported"
	state := surface.cargo_metadata.state
	state != "disabled"
	msg := sprintf("native Cargo [unstable].%s must disable embedding (observed %s)", [surface.cargo_metadata.setting, state])
}

# A raw disabling flag can create metadata-stub dependencies. A raw enabling
# flag contradicts the Cargo control. Preserve ordering within each source.
deny contains finding("DF-003", state_verdict(source.metadata), surface.path, msg) if {
	some surface in measured
	some source in surface.flags
	source.metadata != "unset"
	msg := sprintf("rustflags source %s must not set raw -Zembed-metadata; use Cargo's native coordination", [source.source])
}
