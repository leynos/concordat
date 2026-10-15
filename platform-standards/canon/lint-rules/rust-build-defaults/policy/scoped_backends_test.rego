# Policy regressions for target-scoped backend defaults.
package canon.lint_rules.rust_build_defaults_scoped_test

import rego.v1

import data.canon.lint_rules.rust_build_defaults as policy

profile(findings) := {[f.rule_id, f.verdict] | some f in findings}

removed_flag(source, name, value, flag) if {
	source.name == name
	value == flag
}

build_or_linux_source(source) if source.kind == "build"

build_or_linux_source(source) if source.linux_only == true

source_with_added_flag(source, name, flag) := object.union(source, {
	"flags": array.concat(source.flags, [flag]),
}) if source.name == name

source_with_added_flag(source, name, _) := source if source.name != name

scoped_fixture := data.fixtures.compliant_scoped_rustflags_backend

# Mutations change the parsed facts of the real fixture, retaining its shape.
without_source_flag(envelope, source_name, flag) := object.union(envelope, {
	"cargo_config": object.union(envelope.cargo_config, {"sources": [
		object.union(source, {"flags": [value |
			some value in source.flags
			not removed_flag(source, source_name, value, flag)
		]}) |
		some source in envelope.cargo_config.sources
	]}),
})

test_target_scoped_backends_leave_windows_and_macos_on_their_defaults if {
	findings := policy.deny with input as scoped_fixture
	count(findings) == 0
}

test_build_and_two_linux_backend_sources_are_compliant if {
	config := object.union(scoped_fixture.cargo_config, {"sources": [source |
		some source in scoped_fixture.cargo_config.sources
		build_or_linux_source(source)
	]})
	fixture := object.union(scoped_fixture, {"cargo_config": config})
	findings := policy.deny with input as fixture
	count(findings) == 0
}

test_scoped_backend_does_not_hide_a_missing_frontend_flag if {
	fixture := without_source_flag(scoped_fixture, "target.aarch64-unknown-linux-gnu", "-Zthreads=8")
	findings := policy.deny with input as fixture
	profile(findings) == {["BD-001", "noncompliant"], ["BD-003", "noncompliant"]}
}

test_removing_all_scoped_backends_still_requires_an_exception if {
	config := object.union(scoped_fixture.cargo_config, {
		"sources": [object.union(source, {"flags": [flag |
			some flag in source.flags
			flag != "-Zcodegen-backend=cranelift"
		]}) | some source in scoped_fixture.cargo_config.sources],
		"backends": [],
	})
	findings := policy.deny with input as object.union(scoped_fixture, {"cargo_config": config})
	profile(findings) == {["BD-004", "noncompliant"]}
}

test_scoped_backend_does_not_allow_the_linker_on_macos if {
	config := object.union(scoped_fixture.cargo_config, {"sources": [
		source_with_added_flag(source, "target.aarch64-apple-darwin", "-Clink-arg=-fuse-ld=mold") |
		some source in scoped_fixture.cargo_config.sources
	]})
	findings := policy.deny with input as object.union(scoped_fixture, {"cargo_config": config})
	profile(findings) == {["BD-002", "noncompliant"]}
}

# A backend introduced by build is global: a replacing target must retain it.
test_a_linux_replacement_cannot_drop_the_build_backend if {
	fixture := without_source_flag(
		data.fixtures.compliant_rustflags_backend,
		"target.cfg(target_os = \"linux\")",
		"-Zcodegen-backend=cranelift",
	)
	findings := policy.deny with input as fixture
	profile(findings) == {["BD-003", "noncompliant"]}
}

test_shared_flags_contain_the_frontend_but_not_the_target_backend if {
	flags := policy.shared_flags with input as scoped_fixture
	flags == {"-Zthreads=8"}
}

# Backend validity remains a separate clause; LLVM does not hide lost defaults.
test_a_linux_replacement_cannot_replace_the_build_backend_with_llvm if {
	fixture := without_source_flag(
		data.fixtures.compliant_rustflags_backend,
		"target.cfg(target_os = \"linux\")",
		"-Zcodegen-backend=cranelift",
	)
	config := object.union(fixture.cargo_config, {
		"sources": [source_with_added_flag(
			source, "target.cfg(target_os = \"linux\")", "-Zcodegen-backend=llvm",
		) | some source in fixture.cargo_config.sources],
		"backends": array.concat(
		[backend |
			some backend in fixture.cargo_config.backends
			backend.source != "target.cfg(target_os = \"linux\")"
		],
		[{
			"source": "target.cfg(target_os = \"linux\")",
			"scope": "rustflags", "profile": null, "backend": "llvm",
		}],
	),
	})
	findings := policy.deny with input as object.union(fixture, {"cargo_config": config})
	profile(findings) == {["BD-003", "noncompliant"], ["BD-005", "noncompliant"]}
}
