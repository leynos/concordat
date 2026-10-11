# documentation-presence: DG-003 and DG-004 (Rust).
#
# Input is the same policy-input/documentation-presence envelope the Python
# rules read; its `rust` section lists every library crate the checkout owns
# with the places that can set `missing_docs` (crate-root inner attributes, the
# crate's `[lints.rust]` table, the workspace table it opted in to) and its
# `clippy_runs` lists the clippy invocations `make lint` reaches. The envelope
# builder reads the files; the policy only decides what the facts mean, and
# anything it cannot prove is `indeterminate` (fail closed).
#
# DG-003: every library crate denies (or forbids) `missing_docs`. A crate-root
# attribute beats the manifest, as it does in rustc, so the last
# `missing_docs` attribute at the root decides when there is one. A crate-root
# `#![expect(missing_docs, reason = "...")]` is the one accepted exception, for
# items a derive macro generates and a crate cannot document.
# Module-level and item-level `allow`/`expect(missing_docs)` need a `reason` too.
# DG-004: `make lint` reaches clippy, because the lint only fails the build
# where a gate compiles the crate: `-D warnings` then makes a regression block.
package canon.lint_rules.documentation_presence

import rego.v1

accepted_levels := {"deny", "forbid"}

# `cfg_attr(not(test), deny(missing_docs))` is the idiom for a lint that must not
# fail the test build; any other condition leaves a configuration unchecked.
accepted_conditions := {null, "not(test)"}

rust_crates := object.get(object.get(input, "rust", {}), "crates", [])

rust_errors := object.get(object.get(input, "rust", {}), "errors", [])

rust_in_scope if {
	envelope_ok
	count(rust_crates) > 0
}

rust_in_scope if {
	envelope_ok
	count(rust_errors) > 0
}

crate_finding(rule_id, verdict, path, msg) := {
	"rule_id": rule_id,
	"severity": "error",
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": msg,
}

crate_label(crate) := sprintf("crate `%s`", [crate.name]) if crate.name != null

crate_label(crate) := sprintf("the crate at `%s`", [crate.manifest]) if crate.name == null

# -- the level a crate ends with ------------------------------------------------------

has_attribute(crate) if count(crate.attributes) > 0

last_attribute(crate) := crate.attributes[count(crate.attributes) - 1]

# The manifest level: the crate's own table, else the workspace's it opted in to.
manifest_level(crate) := crate.manifest_level if crate.manifest_level != null

manifest_level(crate) := crate.workspace_level if {
	crate.manifest_level == null
	crate.workspace_level != null
}

default manifest_level(_) := "unset"

# A crate-root `expect` with a reason is accepted for generated items.
is_accepted_exception(crate) if {
	has_attribute(crate)
	attribute := last_attribute(crate)
	attribute.kind == "expect"
	attribute.has_reason
}

is_denied(crate) if {
	has_attribute(crate)
	last_attribute(crate).kind in accepted_levels
}

is_denied(crate) if {
	not has_attribute(crate)
	manifest_level(crate) in accepted_levels
}

is_denied(crate) if is_accepted_exception(crate)

is_readable(crate) if crate.root_error == null

has_unchecked_condition(crate) if {
	some attribute in crate.attributes
	not attribute.condition in accepted_conditions
}

# -- DG-003 --------------------------------------------------------------------------

deny contains f if {
	rust_in_scope
	some error in rust_errors
	f := crate_finding(
		"DG-003", "indeterminate", "Cargo.toml",
		sprintf("a Cargo manifest could not be read, so the crates it describes cannot be checked: %s", [error]),
	)
}

deny contains f if {
	rust_in_scope
	some crate in rust_crates
	not is_readable(crate)
	f := crate_finding(
		"DG-003", "indeterminate", crate.manifest,
		sprintf("%s has a library root that cannot be read (%s)", [crate_label(crate), crate.root_error]),
	)
}

deny contains f if {
	rust_in_scope
	some crate in rust_crates
	is_readable(crate)
	has_unchecked_condition(crate)
	f := crate_finding(
		"DG-003", "indeterminate", crate.root,
		sprintf("%s sets `missing_docs` under a `cfg_attr` condition other than `not(test)`, so some configurations are unchecked", [crate_label(crate)]),
	)
}

deny contains f if {
	rust_in_scope
	some crate in rust_crates
	is_readable(crate)
	not has_unchecked_condition(crate)
	has_attribute(crate)
	attribute := last_attribute(crate)
	attribute.kind == "expect"
	not attribute.has_reason
	f := crate_finding(
		"DG-003", "noncompliant", crate.root,
		sprintf("%s has `#![expect(missing_docs)]` without a `reason`; an exception for generated items must say why", [crate_label(crate)]),
	)
}

deny contains f if {
	rust_in_scope
	some crate in rust_crates
	is_readable(crate)
	not has_unchecked_condition(crate)
	has_attribute(crate)
	attribute := last_attribute(crate)
	attribute.kind in {"allow", "warn"}
	f := crate_finding(
		"DG-003", "noncompliant", crate.root,
		sprintf("%s sets `missing_docs` to `%s` at its crate root; it must be `deny` or `forbid`", [crate_label(crate), attribute.kind]),
	)
}

deny contains f if {
	rust_in_scope
	some crate in rust_crates
	is_readable(crate)
	not has_unchecked_condition(crate)
	not has_attribute(crate)
	not is_denied(crate)
	f := crate_finding(
		"DG-003", "noncompliant", crate.manifest,
		sprintf("%s does not deny `missing_docs` (level: %s); set `missing_docs = \"deny\"` in `[workspace.lints.rust]` and `[lints] workspace = true` in the crate, or in its own `[lints.rust]`", [crate_label(crate), manifest_level(crate)]),
	)
}

# Module-level and item-level suppressions hide undocumented items from a crate
# whose root denies the lint, so each needs a reason. The reason is what the
# rule can check; that the code is generated or macro-produced is for the
# reviewer, who reads the reason.
unreasoned(crate) := [s | some s in object.get(crate, "suppressions", []); not s.has_reason]

deny contains f if {
	rust_in_scope
	some crate in rust_crates
	suppressed := unreasoned(crate)
	count(suppressed) > 0
	first := suppressed[0]
	f := crate_finding(
		"DG-003", "noncompliant", first.file,
		sprintf("%s has %d `%s(missing_docs)` suppression(s) without a `reason` (first at line %d); each must give a reason and sit on generated or macro-produced code", [crate_label(crate), count(suppressed), first.kind, first.line]),
	)
}

deny contains f if {
	rust_in_scope
	some crate in rust_crates
	some file in object.get(crate, "source_errors", [])
	f := crate_finding(
		"DG-003", "indeterminate", file,
		sprintf("%s has a source file that is not UTF-8 text, so its suppressions cannot be read", [crate_label(crate)]),
	)
}

# -- DG-004 --------------------------------------------------------------------------

clippy_resolved := [run | some run in object.get(input, "clippy_runs", []); run.resolved]

clippy_unresolved := [run | some run in object.get(input, "clippy_runs", []); not run.resolved]

# A repository that runs clippy through a wrapper script records why in its
# developers' guide (a section whose heading contains "Clippy gate") and names the
# script in backticks. The record stands in for a `cargo clippy` recipe line only
# while a named script exists in the checkout, mentions clippy as a cargo
# subcommand, and is reached from a recipe `lint` reaches. The audit cannot see
# inside the script, so it checks no more than that.
exceptions := object.get(object.get(input, "clippy_exceptions", {}), "records", [])

exception_errors := object.get(object.get(input, "clippy_exceptions", {}), "errors", [])

valid_exception_scripts := [script |
	some exception in exceptions
	some script in exception.scripts
	script.exists
	script.runs_clippy
	script.reached
]

has_valid_exception if count(valid_exception_scripts) > 0

# A guide or script the audit cannot examine makes the exception unknowable: the
# verdict is indeterminate alone, not indeterminate beside an ordinary "no clippy".
script_errors := [script |
	some exception in exceptions
	some script in exception.scripts
	script.error != null
]

exception_unreadable if count(exception_errors) > 0

exception_unreadable if count(script_errors) > 0

deny contains f if {
	rust_in_scope
	count(rust_crates) > 0
	count(clippy_resolved) == 0
	not has_valid_exception
	some script in script_errors
	f := crate_finding(
		"DG-004", "indeterminate", "docs/developers-guide.md",
		sprintf("the clippy-gate exception names `%s`, which the audit cannot examine: %s", [script.path, script.error]),
	)
}

script_problem(script) := "does not exist inside the checkout" if {
	not script.exists
	script.error == null
}

script_problem(script) := "does not run clippy as a cargo subcommand" if {
	script.exists
	not script.runs_clippy
}

script_problem(script) := "is not run by any command in a recipe that `make lint` reaches" if {
	script.exists
	script.runs_clippy
	not script.reached
}

deny contains f if {
	rust_in_scope
	count(rust_crates) > 0
	count(clippy_resolved) == 0
	not has_valid_exception
	some error in exception_errors
	f := crate_finding(
		"DG-004", "indeterminate", "docs/developers-guide.md",
		sprintf("the document that would record a clippy-gate exception cannot be read, so the audit cannot tell whether one exists: %s", [error]),
	)
}

deny contains f if {
	rust_in_scope
	count(rust_crates) > 0
	count(clippy_resolved) == 0
	not has_valid_exception
	some exception in exceptions
	count(exception.scripts) == 0
	f := crate_finding(
		"DG-004", "noncompliant", exception.path,
		sprintf("the clippy-gate exception at line %d names no script in backticks, so the audit cannot check it", [exception.line]),
	)
}

deny contains f if {
	rust_in_scope
	count(rust_crates) > 0
	count(clippy_resolved) == 0
	not has_valid_exception
	some exception in exceptions
	some script in exception.scripts
	script.error == null
	f := crate_finding(
		"DG-004", "noncompliant", exception.path,
		sprintf("the clippy-gate exception at line %d names `%s`, which %s", [exception.line, script.path, script_problem(script)]),
	)
}

deny contains f if {
	rust_in_scope
	count(rust_crates) > 0
	not has_valid_exception
	not input.applicability.root_makefile
	f := finding(
		"DG-004", "noncompliant", 0,
		"the repository owns Rust libraries but has no root Makefile, so `make lint` cannot run clippy",
	)
}

deny contains f if {
	rust_in_scope
	count(rust_crates) > 0
	not has_valid_exception
	input.applicability.root_makefile
	not input.lint_target
	f := finding(
		"DG-004", "noncompliant", 0,
		"the root Makefile has no `lint` target, so no clippy run is reachable from `make lint`",
	)
}

deny contains f if {
	rust_in_scope
	count(rust_crates) > 0
	not has_valid_exception
	input.lint_target
	count(clippy_resolved) == 0
	count(clippy_unresolved) > 0
	f := finding(
		"DG-004", "indeterminate", clippy_unresolved[0].line,
		sprintf("target %s runs clippy through text this policy cannot resolve (`%s`)", [clippy_unresolved[0].target, clippy_unresolved[0].command]),
	)
}

deny contains f if {
	rust_in_scope
	count(rust_crates) > 0
	not has_valid_exception
	input.lint_target
	count(clippy_resolved) == 0
	count(clippy_unresolved) == 0
	not input.reachability_resolved
	f := finding(
		"DG-004", "indeterminate", 0,
		"`make lint` names a prerequisite or sub-make this policy cannot resolve, so it cannot prove that no clippy run is reachable",
	)
}

deny contains f if {
	rust_in_scope
	count(rust_crates) > 0
	not has_valid_exception
	not exception_unreadable
	input.lint_target
	count(clippy_resolved) == 0
	count(clippy_unresolved) == 0
	input.reachability_resolved
	f := finding(
		"DG-004", "noncompliant", 0,
		"`make lint` reaches no clippy run, and no recorded clippy-gate exception stands in for one, so a `missing_docs` lint at deny is not compiled by the gate",
	)
}
