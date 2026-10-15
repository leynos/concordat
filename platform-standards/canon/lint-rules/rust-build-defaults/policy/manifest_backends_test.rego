# Root-manifest backend facts remain separate from Cargo configuration facts.
package canon.lint_rules.rust_build_defaults_manifest_backends_test

import rego.v1

import data.canon.lint_rules.rust_build_defaults as policy

# A minimal typed fact isolates profile semantics from serialized TOML keys.
selection(scope, backend) := {
	"source": "profile.release", "scope": scope,
	"profile": "release", "backend": backend,
}

# Preserve applicability and parse status so config facts reach the policy.
envelope(selections) := object.union(data.fixtures.coverage_make_release, {
	"cargo_manifest_backends": selections,
})

test_direct_manifest_llvm_is_a_profile_proof if {
	policy.profile_selects_llvm("release") with input as envelope([selection("profile", "llvm")])
}

test_package_llvm_is_not_a_profile_default_proof if {
	not policy.profile_selects_llvm("release") with input as envelope([selection("package-override", "llvm")])
}

test_direct_manifest_cranelift_is_not_the_release_llvm_default if {
	not policy.release_profile_is_llvm with input as envelope([selection("profile", "cranelift")])
}

test_absent_manifest_backend_retains_the_release_llvm_default if {
	policy.release_profile_is_llvm with input as envelope([])
}

test_package_cranelift_prevents_a_wholly_llvm_release_proof if {
	not policy.release_profile_is_llvm with input as envelope([selection("package-override", "cranelift")])
}

test_package_llvm_does_not_displace_the_release_llvm_default if {
	policy.release_profile_is_llvm with input as envelope([selection("package-override", "llvm")])
}

test_unsupported_manifest_backend_cannot_prove_the_release_default if {
	not policy.release_profile_is_llvm with input as envelope([selection("profile", "custom")])
}

test_malformed_manifest_backend_cannot_prove_the_release_default if {
	facts := object.union(envelope([]), {"cargo_manifest_backend_error": "profile.release is not a table"})
	not policy.release_profile_is_llvm with input as facts
}

# A different configured profile cannot erase a manifest release selection.
test_config_other_profile_preserves_manifest_release_cranelift if {
	config := object.union(selection("profile", "llvm"), {"source": "profile.coverage", "profile": "coverage"})
	facts := object.union(envelope([selection("profile", "cranelift")]), {"cargo_config": {"parse_error": null, "backends": [config]}})
	policy.applicable with input as facts
	policy.config_readable with input as facts
	policy.profile_selects_cranelift("release") with input as facts
	not policy.release_profile_is_llvm with input as facts
}

test_config_other_profile_preserves_manifest_release_llvm if {
	config := object.union(selection("profile", "cranelift"), {"source": "profile.coverage", "profile": "coverage"})
	facts := object.union(envelope([selection("profile", "llvm")]), {"cargo_config": {"parse_error": null, "backends": [config]}})
	policy.applicable with input as facts
	policy.config_readable with input as facts
	policy.profile_selects_llvm("release") with input as facts
}

test_unknown_config_backend_cannot_fall_back_to_manifest_llvm if {
	facts := object.union(envelope([selection("profile", "llvm")]), {"cargo_config": {"parse_error": null, "backends": [selection("profile", "custom")]}})
	policy.applicable with input as facts
	policy.config_readable with input as facts
	not policy.profile_selects_llvm("release") with input as facts
	not policy.release_profile_is_llvm with input as facts
}

test_config_llvm_overrides_inactive_unknown_manifest_profile if {
	facts := object.union(envelope([selection("profile", "custom")]), {"cargo_config": {"parse_error": null, "backends": [selection("profile", "llvm")]}})
	policy.applicable with input as facts
	policy.config_readable with input as facts
	not policy.profile_backend_unproved("release") with input as facts
	policy.release_profile_is_llvm with input as facts
}

test_config_other_profile_preserves_unknown_manifest_release if {
	config := object.union(selection("profile", "llvm"), {"source": "profile.coverage", "profile": "coverage"})
	facts := object.union(envelope([selection("profile", "custom")]), {"cargo_config": {"parse_error": null, "backends": [config]}})
	policy.applicable with input as facts
	policy.config_readable with input as facts
	policy.profile_backend_unproved("release") with input as facts
	not policy.release_profile_is_llvm with input as facts
}

test_config_llvm_does_not_erase_unknown_manifest_package_override if {
	facts := object.union(envelope([selection("package-override", "custom")]), {"cargo_config": {"parse_error": null, "backends": [selection("profile", "llvm")]}})
	policy.applicable with input as facts
	policy.config_readable with input as facts
	policy.profile_backend_unproved("release") with input as facts
	not policy.release_profile_is_llvm with input as facts
}

test_config_llvm_does_not_erase_malformed_manifest_facts if {
	facts := object.union(envelope([]), {"cargo_config": {"parse_error": null, "backends": [selection("profile", "llvm")]}, "cargo_manifest_backend_error": "profile.release is not a table"})
	policy.applicable with input as facts
	policy.config_readable with input as facts
	not policy.release_profile_is_llvm with input as facts
}
