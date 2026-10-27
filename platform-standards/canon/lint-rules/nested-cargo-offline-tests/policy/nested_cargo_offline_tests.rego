# NC-007, NC-008: a test's own cargo run cannot reach the network, and the
# entry point that runs the tests warms the cache first.
#
# The Python envelope has read every cargo spawn in test scope and classified
# it `offline`, `online` or `unresolved`, and every test entry point and
# classified its prefetch `before`, `absent` or `uncertain`. This policy turns
# the first into a finding (NC-007), and the second into a finding (NC-008) only
# when a spawn or trybuild use gives the entry point something to protect.
# Anything the reader could not establish is indeterminate, never a pass.
#
# Every finding except an envelope defect carries the configured severity, so
# the package ships as a survey: a warning is reported and does not block.
package canon.lint_rules.nested_cargo_offline_tests

import rego.v1

ENVELOPE_KIND := "policy-input/nested-cargo-offline-tests"

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
	is_array(input.trybuild)
	is_array(input.entry_points)
	is_array(input.problems)
}

# An exemption is a reviewed record: it names the repository, the exact file,
# why the file must reach the network, and what event sends it back for
# review. A blank rationale or review trigger asks nobody to revisit it, so it
# is not a record and suppresses nothing.
is_exempt(path) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.path == path
	is_string(exemption.rationale)
	trim_space(exemption.rationale) != ""
	is_string(exemption.review_trigger)
	trim_space(exemption.review_trigger) != ""
}

# NC-008 protects a nested build the checkout visibly makes. A repository that
# spawns no cargo and uses no trybuild has nothing for a prefetch to warm.
has_nested_build if {
	count(input.spawns) > 0
}

has_nested_build if {
	count(input.trybuild) > 0
}

# -- envelope defects -----------------------------------------------------------

deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/nested-cargo-offline-tests envelope at schema version 1"
}

# -- NC-007: a test's cargo run cannot reach the network -----------------------

#
# The message prints the line with `%d`, so a fact whose `line` is not a number
# would be reported as `%!d(string=80)`. A malformed fact is reported once, by
# the required-field check below, rather than twice with a garbled location.
deny contains finding("NC-007", severity, "noncompliant", spawn.path, spawn.line, msg) if {
	envelope_ok
	some spawn in input.spawns
	spawn.state == "online"
	_well_formed_spawn(spawn)
	not is_exempt(spawn.path)
	msg := sprintf(
		"%s:%d starts cargo without --frozen, without both --offline and --locked, and without CARGO_NET_OFFLINE=\"true\" set on the command. The nested build resolves dependencies or updates the crates.io index while holding the shared package-cache lock, so every other gate on the host can wait behind it. Pass --frozen, or set CARGO_NET_OFFLINE=\"true\" on the command, or name the file in this rule's exemptions with the reason it must reach the network",
		[spawn.path, spawn.line],
	)
}

deny contains finding("EN-001", severity, "indeterminate", spawn.path, spawn.line, msg) if {
	envelope_ok
	some spawn in input.spawns
	spawn.state == "unresolved"
	_well_formed_spawn(spawn)
	msg := sprintf(
		"%s:%d starts cargo with arguments or environment the reader cannot see, so whether it can reach the network is unknown: %s",
		[spawn.path, spawn.line, _describe(spawn)],
	)
}

# -- NC-008: the entry point warms the cache before it runs the tests ----------

deny contains finding("NC-008", severity, "noncompliant", entry.path, entry.line, msg) if {
	envelope_ok
	has_nested_build
	some entry in input.entry_points
	entry.state == "absent"
	_well_formed_entry(entry)
	not is_exempt(entry.path)
	msg := sprintf(
		"%s:%d runs the tests (%s) with no success-gated cargo fetch --locked before them, so a nested build starts cold and resolves while holding the shared package-cache lock. Run cargo fetch --locked before the test command, joined by && so its failure stops the run, or name the file in this rule's exemptions",
		[entry.path, entry.line, entry.name],
	)
}

# An unreadable entry point only counts where a nested build exists for a
# prefetch to protect. Without one, the missing (or unreadable) prefetch warms
# nothing, and reporting it would fail a repository that has no problem this
# rule can fix. NC-008 carries the same gate for the same reason.
deny contains finding("EN-001", severity, "indeterminate", entry.path, entry.line, msg) if {
	envelope_ok
	has_nested_build
	some entry in input.entry_points
	entry.state == "uncertain"
	_well_formed_entry(entry)
	msg := sprintf(
		"%s:%d may run the tests (%s), but its ordering could not be read: %s",
		[entry.path, entry.line, entry.name, object.get(entry, "reason", "no reason recorded")],
	)
}

# -- evidence the reader could not establish -----------------------------------

deny contains finding("EN-001", severity, "indeterminate", "", 0, msg) if {
	envelope_ok
	some problem in input.problems
	msg := sprintf("a nested-cargo fact could not be established: %s", [problem])
}

# A spawn fact the reader wrote in a shape this policy does not know is not a
# pass: the three states are the whole vocabulary, and silently ignoring a
# fourth would report a repository clean on evidence nobody judged.
#
# `object.get` supplies a sentinel rather than reading the key directly,
# because `not spawn.state in {...}` on a fact with no `state` at all is
# undefined: Rego has no value to negate, the rule body fails, and the fact
# escapes unjudged. The sentinel makes the absence itself reportable, and the
# `is_object` guard keeps `object.get` from raising a type error on a fact
# that is not a mapping at all.
deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	envelope_ok
	some spawn in input.spawns
	msg := _unknown_spawn(spawn)
}

deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	envelope_ok
	some entry in input.entry_points
	msg := _unknown_entry(entry)
}

_unknown_spawn(spawn) := msg if {
	is_object(spawn)
	state := object.get(spawn, "state", "<missing>")
	not state in {"offline", "online", "unresolved"}
	msg := sprintf("a spawn fact carries the unknown state %v", [state])
}

_unknown_spawn(spawn) := msg if {
	not is_object(spawn)
	msg := sprintf("a spawn fact is not an object: %v", [spawn])
}

# A known state is not by itself a usable fact. NC-007 and the unresolved
# finding both report the file and line a spawn starts at, so a fact missing
# either one cannot produce a finding at all and the spawn would pass unjudged.
# The missing fields are named rather than silently tolerated.
_unknown_spawn(spawn) := msg if {
	is_object(spawn)
	object.get(spawn, "state", "<missing>") in {"offline", "online", "unresolved"}
	missing := _missing_fields(spawn, [["path", "string"], ["line", "number"]])
	count(missing) > 0
	msg := sprintf("a spawn fact is missing required %v", [missing])
}

_unknown_entry(entry) := msg if {
	is_object(entry)
	state := object.get(entry, "state", "<missing>")
	not state in {"before", "absent", "uncertain"}
	msg := sprintf("an entry-point fact carries the unknown state %v", [state])
}

_unknown_entry(entry) := msg if {
	not is_object(entry)
	msg := sprintf("an entry-point fact is not an object: %v", [entry])
}

# Both entry-point findings name the entry point by path, line and name, so a
# fact missing any of them produces no finding at all and the entry point would
# pass unjudged. `_well_formed_entry` therefore covers every known state, not
# only `absent`: an `uncertain` fact whose finding is silently dropped is the
# one most likely to hide a real problem.
_unknown_entry(entry) := msg if {
	is_object(entry)
	object.get(entry, "state", "<missing>") in {"before", "absent", "uncertain"}
	missing := _missing_entry_fields(entry)
	count(missing) > 0
	msg := sprintf("an entry-point fact is missing required %v", [missing])
}

_missing_entry_fields(entry) := _missing_fields(
	entry,
	[["path", "string"], ["line", "number"], ["name", "string"]],
)

# The guards the ordinary findings use to stay out of a malformed fact's way.
# Without them a fact with a string `line` would be reported twice: once by the
# required-field check, and once more by NC-007 printing `%!d(string=80)` where
# a line number belongs.
_well_formed_spawn(spawn) if {
	missing := _missing_fields(spawn, [["path", "string"], ["line", "number"]])
	count(missing) == 0
}

_well_formed_entry(entry) if {
	missing := _missing_entry_fields(entry)
	count(missing) == 0
}

# Return the `[name, type]` pairs whose field is absent or of the wrong type,
# in the order they were listed. A wrong type is as unusable as a missing
# field: Rego would print a boolean where the finding expects a line number.
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

# The description of an unresolved spawn is read from a fact that may be
# incomplete, so both fields are defaulted: reading them directly would leave
# this helper undefined and drop the EN-001 finding that refers to it.
_describe(spawn) := description if {
	environment := concat(",", object.get(spawn, "environment", []))
	description := sprintf("flags %v, environment %v", [object.get(spawn, "flags", []), environment])
}
