# whitaker-exclusions: WX-001 to WX-003, and EN-001 for what could not be read.
#
# A Whitaker exclusion (`excluded_crates`, `excluded_paths`, or another
# `excluded_*` key in a `dylint.toml`) switches a lint off for part of a
# repository. It should say why, and a whole-crate exclusion of a crate the
# repository governs should be a decision someone revisits, not a leftover.
# femtologging excluded `femtologging_rs`, its whole library, from
# `no_std_fs_operations` on the strength of a Whitaker bug that was never
# rechecked (femtologging#421).
#
# The Python envelope found every `excluded_*` key, whether a comment above the
# key (or above its table header) states a reason, and what each crate name in
# `excluded_crates` is: a crate the repository governs, a fixture or vendored
# crate, or nothing. This policy decides what the facts mean:
#
#   WX-001  an exclusion carries no reason;
#   WX-002  a whole-crate exclusion names a governed crate that the rule's
#           `crate_exclusions` parameter does not name;
#   WX-003  a whole-crate exclusion names no crate at all, so it is stale.
#
# A named exemption is a reviewed record (repository, crate, reason, review
# trigger); a blank reason or trigger makes it not a record. Fixture crates that
# break their own lints on purpose are not governed, so excluding them needs no
# entry. Findings carry the configured severity, `warning` by default so the
# package ships as a survey. An envelope defect is always an error.
package canon.lint_rules.whitaker_exclusions

import rego.v1

finding(id, severity, verdict, path, line, msg) := {
	"rule_id": id,
	"severity": severity,
	"verdict": verdict,
	"path": path,
	"line": line,
	"msg": msg,
}

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/whitaker-exclusions"
	is_array(input.exclusions)
	is_array(input.unreadable)
}

default severity := "warning"

severity := data.parameters.severity

default crate_exclusions := []

crate_exclusions := data.parameters.crate_exclusions

repository_name := input.repository.name

nonblank(value) if {
	is_string(value)
	trim_space(value) != ""
}

rustc_name(name) := replace(name, "-", "_")

is_named(entry) if {
	some named in crate_exclusions
	named.repository == repository_name
	rustc_name(named.crate) == rustc_name(entry.name)
	nonblank(named.reason)
	nonblank(named.review_trigger)
}

located(exclusion) if exclusion.line != null

where(exclusion) := sprintf("[%s] %s", [exclusion.table, exclusion.key]) if exclusion.table != ""

where(exclusion) := exclusion.key if exclusion.table == ""

# -- envelope and unreadable inputs ---------------------------------------------------

deny contains finding("EN-001", "error", "indeterminate", "", 0, msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/whitaker-exclusions envelope at schema version 1"
}

deny contains finding("EN-001", severity, "indeterminate", "", 0, msg) if {
	envelope_ok
	some entry in input.unreadable
	msg := sprintf("a configuration or manifest file could not be read, so the audit cannot judge the exclusions it holds or resolve the crates they name: %s", [entry])
}

deny contains finding("EN-001", severity, "indeterminate", exclusion.file, 0, msg) if {
	envelope_ok
	some exclusion in input.exclusions
	not located(exclusion)
	msg := sprintf("%s: the audit could not find %s in the file text (a dotted key or an inline table), so it cannot tell whether a comment gives a reason", [exclusion.file, where(exclusion)])
}

# -- WX-001: an exclusion without a reason ---------------------------------------------

deny contains finding("WX-001", severity, "noncompliant", exclusion.file, exclusion.line, msg) if {
	envelope_ok
	some exclusion in input.exclusions
	located(exclusion)
	not exclusion.has_reason
	msg := sprintf(
		"%s in %s excludes %d entr%s from a Whitaker lint but gives no reason. Put a comment block directly above the key (or above its table header) saying what the excluded code does that the lint forbids and what would let the exclusion go; a blank line ends the block",
		[where(exclusion), exclusion.file, count(exclusion.entries), plural(count(exclusion.entries))],
	)
}

plural(1) := "y"

plural(n) := "ies" if n != 1

# -- WX-002: a governed crate excluded as a whole -----------------------------------------

# One finding per exclusion key, naming every crate it leaves unaccounted for:
# a list of thirty test crates is one decision to review, not thirty findings.
unnamed_governed(exclusion) := sort([entry.name |
	some entry in exclusion.entries
	entry.resolved == "governed"
	not is_named(entry)
])

deny contains finding("WX-002", severity, "noncompliant", exclusion.file, line_of(exclusion), msg) if {
	envelope_ok
	some exclusion in input.exclusions
	exclusion.kind == "crates"
	names := unnamed_governed(exclusion)
	count(names) > 0
	msg := sprintf(
		"%s in %s excludes %d whole crate%s that this repository governs from a Whitaker lint: %s. Prefer `excluded_paths` for the module that needs it, or fix the code. If a whole crate must be excluded, name it in the rule's crate_exclusions parameter with a reason and a review trigger",
		[where(exclusion), exclusion.file, count(names), plural_s(count(names)), concat(", ", names)],
	)
}

plural_s(1) := ""

plural_s(n) := "s" if n != 1

line_of(exclusion) := exclusion.line if exclusion.line != null

line_of(exclusion) := 0 if exclusion.line == null

# -- WX-003: an exclusion that names nothing ----------------------------------------------

stale_names(exclusion) := sort([entry.name |
	some entry in exclusion.entries
	entry.resolved == "unknown"
])

deny contains finding("WX-003", severity, "noncompliant", exclusion.file, line_of(exclusion), msg) if {
	envelope_ok
	count(input.unreadable) == 0
	some exclusion in input.exclusions
	exclusion.kind == "crates"
	names := stale_names(exclusion)
	count(names) > 0
	msg := sprintf(
		"%s in %s names %s, which %s not a package, library, binary, test, bench, example or path-dependency alias in this repository, so the exclusion does nothing. Remove it, or correct the name (Whitaker matches the rustc crate name: hyphens are underscores, and a test target is named after its file)",
		[where(exclusion), exclusion.file, concat(", ", names), is_or_are(count(names))],
	)
}

is_or_are(1) := "is"

is_or_are(n) := "are" if n != 1
