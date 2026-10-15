# Root-manifest backend facts remain separate from Cargo configuration facts.
package canon.lint_rules.rust_build_defaults_manifest_backends_test

import rego.v1

import data.canon.lint_rules.rust_build_defaults as policy

# A minimal typed fact isolates profile semantics from serialized TOML keys.
selection(scope, backend) := {
	"source": "profile.release", "scope": scope,
	"profile": "release", "backend": backend,
}

envelope(selections) := {
	"cargo_config": {"backends": []},
	"cargo_manifest_backends": selections,
	"cargo": {"parsed": null},
}

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
