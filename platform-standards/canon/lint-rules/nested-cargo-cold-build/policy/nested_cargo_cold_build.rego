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
	is_array(object.get(input, "passes", []))
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

# Paths with a definite unprepared build, exempt or not. An exempt file's
# unseen-subcommand builds are the same compile its definite build already
# explains, so they stay out of `_maybe_paths` whether or not it is exempt.
_definite_paths := {build.path |
	some build in input.builds
	_well_formed_build(build)
	_unprepared(build)
	_definite(build)
}

_maybe_paths := {build.path |
	some build in input.builds
	_well_formed_build(build)
	_unprepared(build)
	not _definite(build)
	not build.path in _definite_paths
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

# -- NC-010 per pass: each test pass of `make test` prepared for itself ---------

# A repository whose Makefile (or a named in-suite test or gate) is what pays
# for its nested builds is judged pass by pass: preparing one pass's features
# warms nothing for a second pass that builds different ones. A repository whose
# every build a nextest setup script covers is left alone (the script runs
# before each nextest pass), and one with no declared preparation is already
# reported once per file above.
_pass_runner(pass) := object.get(pass, "runner", "nextest")

# A nextest setup script runs before a nextest pass and never before a plain
# `cargo test`, so a build only a script prepares is declared for a nextest pass
# and left to the script, but for a cargo pass it is not prepared at all.
_declared_for(pass, build) if {
	_well_formed_build(build)
	count(object.get(build, "prepared_by", [])) > 0
	not "setup-script" in build.prepared_by
}

_declared_for(pass, build) if {
	_well_formed_build(build)
	count(object.get(build, "prepared_by", [])) > 0
	_pass_runner(pass) == "cargo"
}

_relies_on_declared_preparation(pass) if {
	some build in input.builds
	_declared_for(pass, build)
}

# The families whose nested builds `cargo test --no-run` leaves cold: trybuild
# and macrotest each build in a directory of their own under the target.
_nested_family(build) if build.kind in {"trybuild", "macrotest"}

# A pass that selects packages (`-p NAME`) runs only the nested builds those
# packages own; a pass that selects the workspace, or names none, runs them all.
# A build whose package is unknown is taken as covered, never guessed away.
_pass_packages(pass) := object.get(pass, "packages", [])

_pass_selects_all(pass) if count(_pass_packages(pass)) == 0

_pass_selects_all(pass) if "--workspace" in _pass_packages(pass)

_pass_covers(pass, _) if _pass_selects_all(pass)

_pass_covers(pass, build) if build["package"] in _pass_packages(pass)

_pass_covers(_, build) if not is_string(object.get(build, "package", null))

_pass_has_nested_family(pass) if {
	some build in input.builds
	_declared_for(pass, build)
	_nested_family(build)
	_pass_covers(pass, build)
}

# A named in-suite preparation test or gate runs under the harness's own
# timeout, so it is advice rather than a blocker whatever the severity says. The
# pass must run a build that preparation covers.
_has_in_suite_preparation(pass) if {
	some build in input.builds
	_declared_for(pass, build)
	_pass_covers(pass, build)
	some how in build.prepared_by
	how in {"preparation-test", "gate-call"}
}

# The test target a nested build lives in: `tests/ui.rs` and `tests/ui/main.rs`
# are both target `ui`. A build outside `tests/` has no named target.
_test_target(build) := name if {
	matches := regex.find_all_string_submatch_n(`(?:^|/)tests/([^/]+?)(?:\.rs|/.*)$`, build.path, 1)
	name := matches[0][1]
}

# A warming run restricted to named test targets (`--test NAME`) warms only the
# nested builds those targets hold. A run that named none, or a build whose
# target is unknown, is never guessed away.
_pass_warms(pass, _) if count(object.get(pass, "warmed_tests", [])) == 0

_pass_warms(pass, build) if not _test_target(build)

_pass_warms(pass, build) if _test_target(build) in object.get(pass, "warmed_tests", [])

_passes := object.get(input, "passes", [])

_pass_is_judged(pass) if {
	envelope_ok
	_well_formed_pass(pass)
	_relies_on_declared_preparation(pass)
	not is_exempt(pass.path)
}

# A pass is only judged for the declared builds it runs: one that selects other
# packages (`-p core`) never runs another crate's nested build.
_pass_runs_a_declared_build(pass) if {
	some build in input.builds
	_declared_for(pass, build)
	_pass_covers(pass, build)
}

# Evidence the reader could not establish is reported whether or not any build
# declares a preparation, so an unreadable Makefile is never silently clean.
_pass_is_reportable(pass) if {
	envelope_ok
	_well_formed_pass(pass)
	not is_exempt(pass.path)
}

deny contains finding("NC-010", severity, "noncompliant", pass.path, pass.line, msg) if {
	some pass in _passes
	_pass_is_judged(pass)
	_pass_runs_a_declared_build(pass)
	pass.status == "unprepared"
	pass.visible > 0
	msg := sprintf(
		"%s:%d runs the tests as `%s`, and no preparation that runs before it has the same package selection, features, target, profile and RUSTFLAGS, so its nested builds are cold inside the timed run (rstest-bdd's `--no-default-features` pass timed out although `--all-features` had been prepared). Prepare each pass with that pass's own flags before it runs, as https://github.com/leynos/rstest-bdd/pull/979 does with one preparation target taking the flags, or https://github.com/leynos/whitaker/pull/669 with `make warm-test-builds`",
		[pass.path, pass.line, object.get(pass, "command", "the test runner")],
	)
}

deny contains finding("NC-010", "warning", "noncompliant", pass.path, pass.line, msg) if {
	some pass in _passes
	_pass_is_judged(pass)
	pass.status == "unprepared"
	pass.visible == 0
	_has_in_suite_preparation(pass)
	msg := sprintf(
		"%s:%d runs the tests as `%s`, and the only preparation is a named test or gate inside the suite, which runs under the harness's own timeout. A loaded host can exhaust that bound while it compiles. Run the preparation before the timed run with this pass's flags, outside any test deadline (rstest-bdd's `prepare-nested-fixtures` target)",
		[pass.path, pass.line, object.get(pass, "command", "the test runner")],
	)
}

# A plain `cargo test` pass never runs a nextest setup script, so a build only a
# script prepares is cold inside it.
deny contains finding("NC-010", severity, "noncompliant", pass.path, pass.line, msg) if {
	some pass in _passes
	_pass_is_judged(pass)
	_pass_runner(pass) == "cargo"
	pass.status == "unprepared"
	pass.visible == 0
	not _has_in_suite_preparation(pass)
	some build in input.builds
	_well_formed_build(build)
	"setup-script" in build.prepared_by
	_pass_covers(pass, build)
	msg := sprintf(
		"%s:%d runs the tests as `%s`, a plain `cargo test`, and the only preparation of its nested builds is a nextest setup script, which nextest runs before its own passes and `cargo test` never does. Prepare the build before this pass in the Makefile, with this pass's flags, outside any test deadline",
		[pass.path, pass.line, object.get(pass, "command", "the test runner")],
	)
}

# A warming run limited to named test targets (`--test NAME`) leaves the nested
# builds of every other test file cold.
deny contains finding("NC-010", severity, "noncompliant", pass.path, pass.line, msg) if {
	some pass in _passes
	_pass_is_judged(pass)
	pass.status == "warmed"
	some build in input.builds
	_declared_for(pass, build)
	_nested_family(build)
	_pass_covers(pass, build)
	not _pass_warms(pass, build)
	msg := sprintf(
		"%s:%d runs the tests as `%s`. The runs before it that warm trybuild or macrotest are limited to the test targets %v (`--test`), and the nested build at %s:%d lives in another test file, so it stays cold inside the timed run. Run that test target for real before the timed pass, with the same flags, as https://github.com/leynos/whitaker/pull/669 does",
		[pass.path, pass.line, object.get(pass, "command", "the test runner"), object.get(pass, "warmed_tests", []), build.path, build.line],
	)
}

deny contains finding("NC-010", severity, "noncompliant", pass.path, pass.line, msg) if {
	some pass in _passes
	_pass_is_judged(pass)
	pass.status == "compiled"
	_pass_has_nested_family(pass)
	msg := sprintf(
		"%s:%d runs the tests as `%s`. Only a compile of the same flags runs before it (`--no-run`, `build` or `archive`), and that does not warm trybuild or macrotest: each builds in a directory of its own under the target that the outer build never fills (a whitaker-common contract took 58 s after `--no-run` and 2 s on the next run). Run the nested-build tests for real before the timed pass, with the same flags, as https://github.com/leynos/whitaker/pull/669 does",
		[pass.path, pass.line, object.get(pass, "command", "the test runner")],
	)
}

deny contains finding("EN-001", severity, "indeterminate", pass.path, pass.line, msg) if {
	some pass in _passes
	_pass_is_reportable(pass)
	pass.status == "indeterminate"
	msg := sprintf(
		"%s:%d may run the tests as `%s`, and the reader cannot tell what prepares it: %s",
		[pass.path, pass.line, object.get(pass, "command", "the test runner"), object.get(pass, "reason", "no reason recorded")],
	)
}

deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	envelope_ok
	some pass in _passes
	not _well_formed_pass(pass)
	msg := sprintf("a pass fact is missing required fields: %v", [pass])
}

_well_formed_pass(pass) if {
	is_object(pass)
	count(_missing_fields(pass, [["path", "string"], ["line", "number"], ["status", "string"], ["visible", "number"]])) == 0
	pass.status in {"warmed", "compiled", "unprepared", "indeterminate"}
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
	some msg in _malformed_build(build)
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

# A build can be malformed in more than one way at once (`{}` is missing every
# field and carries no preparation list), so the messages are a set: a function
# with several outputs for one input is a conflict in Rego, not several findings.
_malformed_build(build) := {msg |
	not is_object(build)
	msg := sprintf("a build fact is not an object: %v", [build])
} | {msg |
	is_object(build)
	missing := _missing_fields(build, [["path", "string"], ["line", "number"], ["kind", "string"]])
	count(missing) > 0
	msg := sprintf("a build fact is missing required %v", [missing])
} | {msg |
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
