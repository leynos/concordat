# NC-009, NC-010: a test's nested build is not paid for inside its timed body.
#
# The Python envelope has read every command in test scope that builds, with
# where it builds (`inherited`, `shared`, `dedicated`, `private` or `unknown`),
# and every test source that compiles in a timed test, with the preparation the
# repository declares for it (`setup-script`, `preparation-test`, `gate-call`).
# This policy turns a private target into NC-009 and an undeclared preparation
# into NC-010. Anything the reader could not establish is indeterminate, never a
# pass.
#
# Every finding except an envelope defect carries the configured severity, so
# the package ships as a survey: a warning is reported and does not block.
package canon.lint_rules.nested_cargo_cold_build

import rego.v1

ENVELOPE_KIND := "policy-input/nested-cargo-cold-build"

finding(rule_id, severity, verdict, path, line, message) := {
	"rule_id": rule_id,
	"severity": severity,
	"verdict": verdict,
	"path": path,
	"line": line,
	"msg": message,
}

default severity := "warning"

severity := data.parameters.severity

default exemptions := []

exemptions := data.parameters.exemptions

repository_name := input.repository.name

envelope_ok if {
	input.schema_version == 1
	input.kind == ENVELOPE_KIND
	is_object(input.repository)
	is_array(input.spawns)
	is_array(input.builds)
	is_array(input.problems)
}

# An exemption is a reviewed record: it names the repository, the exact file,
# why the file may build cold, and what event sends it back for review. A blank
# rationale or review trigger asks nobody to revisit it, so it is not a record
# and suppresses nothing.
is_exempt(path) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.path == path
	is_string(exemption.rationale)
	trim_space(exemption.rationale) != ""
	is_string(exemption.review_trigger)
	trim_space(exemption.review_trigger) != ""
}

# -- envelope defects -----------------------------------------------------------

deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/nested-cargo-cold-build envelope at schema version 1"
}

# -- NC-009: a nested build whose target directory is private -------------------

# A command that compiles nothing (`cargo metadata`, `cargo fetch`) can set any
# target it likes without paying for a build, so only a command that compiles,
# or may, is judged.
_builds(spawn) if spawn.compiles != "no"

deny contains finding("NC-009", severity, "noncompliant", spawn.path, spawn.line, msg) if {
	envelope_ok
	some spawn in input.spawns
	spawn.target_state == "private"
	_builds(spawn)
	_well_formed_spawn(spawn)
	not is_exempt(spawn.path)
	msg := sprintf(
		"%s:%d starts a nested build whose target directory is private: %s. Nothing a later run builds can reuse what this one compiled, so every run pays for the whole dependency graph inside the test's deadline and leaves another tree on disk. Build into the workspace's own target directory (read CARGO_TARGET_DIR from the parent environment, or ask `cargo metadata` for `target_directory`), share one directory across the whole test binary, or name the file in this rule's exemptions with the reason it must build fresh",
		[spawn.path, spawn.line, object.get(spawn, "target_source", "an unreadable source")],
	)
}

deny contains finding("EN-001", severity, "indeterminate", spawn.path, spawn.line, msg) if {
	envelope_ok
	some spawn in input.spawns
	spawn.target_state == "unknown"
	_builds(spawn)
	_well_formed_spawn(spawn)
	msg := sprintf(
		"%s:%d starts a nested build and sets its target directory from a value the reader cannot follow (%s), so whether the directory is private is unknown",
		[spawn.path, spawn.line, object.get(spawn, "target_source", "no source recorded")],
	)
}

# -- NC-010: a compile inside a timed test, with no declared preparation ---------

# One finding per file, at the first compile in it, naming every kind of build
# the file starts. A file that both spawns cargo and drives trybuild is one
# problem with one fix.
_unprepared(build) if {
	count(object.get(build, "prepared_by", [])) == 0
}

#
# A `cargo-unknown` build is a cargo command whose subcommand the reader could
# not see, in a crate that shows no compile elsewhere. It may be `metadata` or
# it may be `build`, so it is never a noncompliant finding on its own; it is
# reported as indeterminate unless the same file also has a definite build. An
# exemption does not reach it: it suppresses NC-009 and NC-010 only, so an exempt
# file cannot hide the rule's own uncertainty.
_definite(build) if build.kind != "cargo-unknown"

_cold_paths := {build.path |
	some build in input.builds
	_well_formed_build(build)
	_unprepared(build)
	_definite(build)
	not is_exempt(build.path)
}

_maybe_paths := {build.path |
	some build in input.builds
	_well_formed_build(build)
	_unprepared(build)
	not _definite(build)
	not build.path in _cold_paths
}

_cold_kinds(path) := kinds if {
	kinds := {build.kind |
		some build in input.builds
		build.path == path
		_well_formed_build(build)
		_unprepared(build)
		_definite(build)
	}
}

_cold_line(path) := min({build.line |
	some build in input.builds
	build.path == path
	_well_formed_build(build)
	_unprepared(build)
	_definite(build)
})

_maybe_line(path) := min({build.line |
	some build in input.builds
	build.path == path
	_well_formed_build(build)
	_unprepared(build)
})

deny contains finding("EN-001", severity, "indeterminate", path, line, msg) if {
	envelope_ok
	some path in _maybe_paths
	line := _maybe_line(path)
	msg := sprintf(
		"%s:%d starts cargo with a subcommand the reader cannot see, and the repository declares no preparation for it, so whether it compiles inside a timed test is unknown. If it does, prepare the build outside the timed test as NC-010 describes",
		[path, line],
	)
}

deny contains finding("NC-010", severity, "noncompliant", path, line, msg) if {
	envelope_ok
	some path in _cold_paths
	line := _cold_line(path)
	msg := sprintf(
		"%s:%d compiles inside a timed test (%s) and the repository declares no preparation outside it. If nothing has filled the build's target directory first, the test body pays for a cold build and its timeout fails an assertion that is sound. Prepare the build before the timed test runs: in a nextest setup script (a `[scripts.setup.*]` table selected by a `[[profile.*.scripts]]` rule whose filter covers this test), or in a named preparation test in the same crate and serial test group that stamps its artefacts, with the timed test refusing to run when the stamp is missing, as https://github.com/leynos/rstest-bdd/pull/916 does for its trybuild, macrotest and fixture builds. Doing the shared work once, outside the tests, is the same move as https://github.com/leynos/whitaker/issues/518. Name your own gate in this rule's `preparation_gates` parameter, or name the file in its exemptions with the reason it must build cold",
		[path, line, concat(", ", sort(_cold_kinds(path)))],
	)
}

# -- evidence the reader could not establish -----------------------------------

deny contains finding("EN-001", severity, "indeterminate", "", 0, msg) if {
	envelope_ok
	some problem in input.problems
	msg := sprintf("a nested-cargo fact could not be established: %s", [problem])
}

# A fact the reader wrote in a shape this policy does not know is not a pass.
# `object.get` supplies a sentinel rather than reading the key directly,
# because `not spawn.target_state in {...}` on a fact with no such key is
# undefined: the rule body would fail and the fact would escape unjudged.
deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	envelope_ok
	some spawn in input.spawns
	msg := _malformed_spawn(spawn)
}

deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	envelope_ok
	some build in input.builds
	msg := _malformed_build(build)
}

_malformed_spawn(spawn) := msg if {
	not is_object(spawn)
	msg := sprintf("a spawn fact is not an object: %v", [spawn])
}

_malformed_spawn(spawn) := msg if {
	is_object(spawn)
	state := object.get(spawn, "target_state", "<missing>")
	not state in {"inherited", "shared", "dedicated", "private", "unknown"}
	msg := sprintf("a spawn fact carries the unknown target state %v", [state])
}

_malformed_spawn(spawn) := msg if {
	is_object(spawn)
	object.get(spawn, "target_state", "<missing>") in {"inherited", "shared", "dedicated", "private", "unknown"}
	missing := _missing_fields(spawn, [["path", "string"], ["line", "number"], ["compiles", "string"]])
	count(missing) > 0
	msg := sprintf("a spawn fact is missing required %v", [missing])
}

_malformed_build(build) := msg if {
	not is_object(build)
	msg := sprintf("a build fact is not an object: %v", [build])
}

_malformed_build(build) := msg if {
	is_object(build)
	missing := _missing_fields(build, [["path", "string"], ["line", "number"], ["kind", "string"]])
	count(missing) > 0
	msg := sprintf("a build fact is missing required %v", [missing])
}

_malformed_build(build) := msg if {
	is_object(build)
	not is_array(object.get(build, "prepared_by", null))
	msg := "a build fact carries no prepared_by list"
}

# The guards the ordinary findings use to stay out of a malformed fact's way, so
# a fact with a string `line` is reported once, by the check above, rather than
# twice with a garbled location.
_well_formed_spawn(spawn) if {
	count(_missing_fields(spawn, [["path", "string"], ["line", "number"], ["compiles", "string"]])) == 0
}

_well_formed_build(build) if {
	is_object(build)
	count(_missing_fields(build, [["path", "string"], ["line", "number"], ["kind", "string"]])) == 0
	is_array(object.get(build, "prepared_by", null))
}

_missing_fields(fact, fields) := missing if {
	missing := [name |
		some field in fields
		name := field[0]
		type := field[1]
		not _has_type(fact, name, type)
	]
}

_has_type(fact, name, "string") if is_string(object.get(fact, name, null))

_has_type(fact, name, "number") if is_number(object.get(fact, name, null))
