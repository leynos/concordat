# rust-lint-tables: LT-001 to LT-004, and EN-001 for what could not be read.
#
# Every Cargo package a repository owns compiles under some lint levels: its
# own `[lints.*]` tables, or the `[workspace.lints.*]` of the workspace that
# contains it when it sets `[lints] workspace = true`. The estate's baseline is
# a set of Clippy, rustc and rustdoc levels (`data/baseline.toml`, derived into
# `data.baseline`). A package that sets none of them, a member a workspace table
# never reaches, a standalone crate with four entries and a manifest weaker than
# the baseline all read green to every gate today, which is how
# femtologging's extension kept two Clippy and two rustc lints beside netsuke's
# forty-odd.
#
# The Python envelope walked every Cargo.toml outside the fixture and vendored
# trees, resolved each package's effective levels the way Cargo does, and read
# the facts the checks need from the gate. This policy decides what they mean:
#
#   LT-001  the package has no lint tables that apply to it;
#   LT-002  its effective tables miss or weaken a baseline entry;
#   LT-003  `unsafe_code` is not `forbid`, and the package is not a named
#           boundary crate;
#   LT-004  a named boundary crate is not set up as the allow-list requires
#           (`deny`, a Miri command the gate reaches, an ADR).
#
# A divergence is allowed only as a reviewed record: the canon's
# `pyo3_divergences` (data), or the rule's `exemptions` parameter naming the
# repository, package and lint with a reason and a review trigger. Findings
# carry the configured severity, `warning` by default so the rule ships as a
# survey. An envelope defect is always an error.
package canon.lint_rules.rust_lint_tables

import rego.v1

baseline_file := "platform-standards/canon/lint-rules/rust-lint-tables/data/baseline.toml"

finding(id, severity, verdict, path, msg) := {
	"rule_id": id,
	"severity": severity,
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": msg,
}

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/rust-lint-tables"
	is_array(input.packages)
	is_object(input.miri)
	is_array(input.whitaker_runs)
	is_array(input.unreadable)
	is_array(input.gate_unreadable)
}

audited if {
	envelope_ok
	count(input.packages) > 0
}

default severity := "warning"

severity := data.parameters.severity

default exemptions := []

exemptions := data.parameters.exemptions

default boundary_crates := []

boundary_crates := data.parameters.boundary_crates

baseline := data.baseline

repository_name := input.repository.name

nonblank(value) if {
	is_string(value)
	trim_space(value) != ""
}

# -- levels -------------------------------------------------------------------------

rank := {"allow": 0, "warn": 1, "deny": 2, "forbid": 3}

level_rank(level) := rank[level]

level_rank(level) := -1 if not rank[level]

# The rank of the level a package's effective tables set for a lint; -1 when the
# lint is not set, or is set to something that is not a level.
actual_rank(pkg, table, lint) := level_rank(pkg.effective[table][lint].level)

actual_rank(pkg, table, lint) := -1 if not pkg.effective[table][lint]

actual_text(pkg, table, lint) := "not set" if not pkg.effective[table][lint]

actual_text(pkg, table, lint) := sprintf("%q", [pkg.effective[table][lint].level]) if {
	pkg.effective[table][lint]
	is_string(pkg.effective[table][lint].level)
}

actual_text(pkg, table, lint) := "not a lint level" if {
	pkg.effective[table][lint]
	not is_string(pkg.effective[table][lint].level)
}

# -- reviewed divergences -----------------------------------------------------------

# An exemption is a reviewed record: repository, package, lint, why, and what
# event sends it back for review. A blank reason or trigger asks nobody to
# revisit it, so it is not a record and exempts nothing. `level`, when given,
# is the weakest level the package may then set; absent, the lint is not
# required of the package at all.
valid_exemption(exemption, pkg) if {
	exemption.repository == repository_name
	exemption.crate == pkg.name
	nonblank(exemption.reason)
	nonblank(exemption.review_trigger)
}

exemption_rank(exemption) := level_rank(exemption.level) if exemption.level

exemption_rank(exemption) := 0 if not exemption.level

# The weakest levels the baseline's PyO3 divergences and the rule's exemptions
# let a package set for one lint.
limit_ranks(pkg, key) := {r |
	some divergence in baseline.pyo3_divergences
	pkg.depends_on_pyo3 == true
	divergence.lint == key
	r := level_rank(divergence.level)
} | {r |
	some exemption in exemptions
	valid_exemption(exemption, pkg)
	exemption.lint == key
	r := exemption_rank(exemption)
}

required_rank(pkg, table, lint) := min({level_rank(baseline[table][lint].level)} | limit_ranks(pkg, sprintf("%s::%s", [table, lint])))

# -- cognitive complexity: two ways to meet one requirement ---------------------------

cognitive := baseline.cognitive

# Whitaker's macro-aware lint replaces Clippy's only where it is a real gate:
# opted in through dylint.toml with a valid ceiling, Clippy's threshold removed
# so one gate owns the budget, and Whitaker run over the whole workspace. The
# last condition is a close reading of gate-workspace-coverage for Whitaker: a
# resolved command that selects the workspace without excluding a member, and
# no Whitaker command the reader could not place.
macro_aware_known_fields := {"max_complexity"}

macro_aware_opted_in(pkg) if {
	experimental := pkg.config.dylint.experimental
	is_array(experimental)
	every entry in experimental {
		is_string(entry)
	}
	cognitive.macro_aware_lint in experimental
}

macro_aware_ceiling_valid(pkg) if {
	pkg.config.dylint.max_complexity_is_integer == true
	max_complexity := pkg.config.dylint.table.max_complexity
	max_complexity >= 1
	max_complexity <= cognitive.macro_aware_ceiling
}

macro_aware_table_is_closed(pkg) if {
	count({field | some field, _ in pkg.config.dylint.table; not field in macro_aware_known_fields}) == 0
}

clippy_threshold_removed(pkg) if pkg.config.clippy.cognitive_threshold == null

in_root_workspace(pkg) if pkg.workspace == "Cargo.toml"

in_root_workspace(pkg) if {
	pkg.workspace == null
	pkg.manifest == "Cargo.toml"
}

whitaker_covers_root_workspace if {
	some run in input.whitaker_runs
	run.is_resolved == true
	run.is_workspace == true
	count(run.excludes) == 0
	run.changes_directory == false
	count([r | some r in input.whitaker_runs; r.is_resolved == false]) == 0
}

macro_aware_configured(pkg) if {
	macro_aware_opted_in(pkg)
	macro_aware_ceiling_valid(pkg)
	macro_aware_table_is_closed(pkg)
	clippy_threshold_removed(pkg)
	in_root_workspace(pkg)
}

macro_aware_credit(pkg) if {
	macro_aware_configured(pkg)
	whitaker_covers_root_workspace
}

# Configured as the macro-aware gate, but the gate has lines the reader could not
# place and none it could prove covers the workspace: whether Whitaker gates it
# is unknown, so the lint is neither credited nor reported missing (EN-001).
macro_aware_unknown(pkg) if {
	macro_aware_configured(pkg)
	not whitaker_covers_root_workspace
	count(input.gate_unreadable) > 0
}

# -- the requirement, entry by entry --------------------------------------------------

# `unsafe_code` has its own codes; every other entry is LT-002's.
required_entries contains [table, lint] if {
	some table in ["clippy", "rust", "rustdoc"]
	some lint, _ in baseline[table]
	not [table, lint] == ["rust", "unsafe_code"]
}

waived(pkg, table, lint) if required_rank(pkg, table, lint) <= 0

waived(pkg, table, lint) if {
	table == "clippy"
	lint == cognitive.lint
	macro_aware_credit(pkg)
}

waived(pkg, table, lint) if {
	table == "clippy"
	lint == cognitive.lint
	macro_aware_unknown(pkg)
}

short_level(pkg, table, lint) if {
	not waived(pkg, table, lint)
	actual_rank(pkg, table, lint) < required_rank(pkg, table, lint)
}

short_priority(pkg, table, lint) if {
	not waived(pkg, table, lint)
	wanted := baseline[table][lint].priority
	pkg.effective[table][lint].priority != wanted
}

wanted_level(pkg, table, lint) := level if {
	some level, level_value in rank
	level_value == required_rank(pkg, table, lint)
}

shortfalls(pkg) := sort({line |
	some [table, lint] in required_entries
	short_level(pkg, table, lint)
	line := sprintf("%s::%s should be %q (it is %s)", [table, lint, wanted_level(pkg, table, lint), actual_text(pkg, table, lint)])
} | {line |
	some [table, lint] in required_entries
	short_priority(pkg, table, lint)
	not short_level(pkg, table, lint)
	line := sprintf("%s::%s must keep priority %d", [table, lint, baseline[table][lint].priority])
})

has_tables(pkg) if pkg.state in {"inherited", "own"}

where_to_fix(pkg) := sprintf("the [workspace.lints.*] tables in %s", [pkg.workspace]) if pkg.state == "inherited"

where_to_fix(pkg) := sprintf("the [lints.*] tables in %s", [pkg.manifest]) if pkg.state == "own"

# -- envelope and unreadable inputs ---------------------------------------------------

deny contains finding("EN-001", "error", "indeterminate", "", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/rust-lint-tables envelope at schema version 1"
}

deny contains finding("EN-001", severity, "indeterminate", "", msg) if {
	envelope_ok
	some entry in input.unreadable
	msg := sprintf("a manifest or configuration file could not be read, so the audit cannot judge what it governs: %s", [entry])
}

deny contains finding("EN-001", severity, "indeterminate", pkg.manifest, msg) if {
	audited
	some pkg in input.packages
	has_tables(pkg)
	macro_aware_unknown(pkg)
	msg := sprintf(
		"package %s opts in to Whitaker's macro-aware complexity gate, but the audit cannot tell whether Whitaker runs over the whole workspace: it found no such command, and some gate lines could not be read (%s). Clippy's cognitive_complexity is neither required nor credited until it can",
		[pkg.name, concat("; ", input.gate_unreadable)],
	)
}

# -- LT-001: no lint tables apply to the package ----------------------------------------

deny contains finding("LT-001", severity, "noncompliant", pkg.manifest, msg) if {
	audited
	some pkg in input.packages
	pkg.state == "none"
	msg := sprintf(
		"package %s has no lint tables. Add the estate baseline: for a workspace, put the [workspace.lints.*] tables from %s in the workspace root and add `[lints]` with `workspace = true` to %s; for a package outside any workspace, add the same tables as [lints.*] to %s",
		[pkg.name, baseline_file, pkg.manifest, pkg.manifest],
	)
}

deny contains finding("LT-001", severity, "noncompliant", pkg.manifest, msg) if {
	audited
	some pkg in input.packages
	pkg.state == "dangling"
	msg := sprintf(
		"package %s sets `[lints] workspace = true`, but no workspace above it defines [workspace.lints], so it inherits nothing. Add the [workspace.lints.*] tables from %s to the workspace root %s",
		[pkg.name, baseline_file, dangling_target(pkg)],
	)
}

dangling_target(pkg) := pkg.workspace if pkg.workspace != null

dangling_target(pkg) := "(create one: a [workspace] table in the root Cargo.toml)" if pkg.workspace == null

deny contains finding("LT-001", severity, "noncompliant", pkg.manifest, msg) if {
	audited
	some pkg in input.packages
	pkg.state == "not_opted_in"
	msg := sprintf(
		"package %s is in workspace %s, which defines [workspace.lints], but %s has no `[lints]` table with `workspace = true`, so none of them apply to it. Add `[lints]` with `workspace = true` to %s (the root package of a workspace needs it as much as a member does)",
		[pkg.name, pkg.workspace, pkg.manifest, pkg.manifest],
	)
}

# -- LT-002: tables weaker than the baseline --------------------------------------------

deny contains finding("LT-002", severity, "noncompliant", pkg.manifest, msg) if {
	audited
	some pkg in input.packages
	has_tables(pkg)
	lines := shortfalls(pkg)
	count(lines) > 0
	msg := sprintf(
		"package %s compiles under lint tables that miss or weaken %d baseline entr%s. Set them in %s (copy them from %s), or record a reviewed divergence in the rule's exemptions parameter: %s",
		[pkg.name, count(lines), plural(count(lines)), where_to_fix(pkg), baseline_file, concat("; ", lines)],
	)
}

plural(1) := "y"

plural(n) := "ies" if n != 1

# -- LT-003: unsafe_code ---------------------------------------------------------------

boundary_names contains boundary.crate if {
	some boundary in boundary_crates
	boundary.repository == repository_name
}

deny contains finding("LT-003", severity, "noncompliant", pkg.manifest, msg) if {
	audited
	some pkg in input.packages
	has_tables(pkg)
	not pkg.name in boundary_names
	actual_rank(pkg, "rust", "unsafe_code") < level_rank("forbid")
	msg := sprintf(
		"package %s sets unsafe_code to %s; the baseline forbids it. Set `unsafe_code = \"forbid\"` in %s. A crate that genuinely needs `unsafe` is a boundary crate instead: set \"deny\", add a Miri command to the gate, and name it in the rule's boundary_crates parameter with its ADR (axinite ADR 0015 and wireframe ADR 0016 are the models)",
		[pkg.name, actual_text(pkg, "rust", "unsafe_code"), where_to_fix(pkg)],
	)
}

# -- LT-004: a boundary crate that is not set up as required ----------------------------

boundary_in_repository(boundary) if boundary.repository == repository_name

package_named(name) := [p | some p in input.packages; p.name == name][0]

deny contains finding("LT-004", severity, "noncompliant", "", msg) if {
	audited
	some boundary in boundary_crates
	boundary_in_repository(boundary)
	count([p | some p in input.packages; p.name == boundary.crate]) == 0
	msg := sprintf("boundary_crates names package %s, but this repository has no such package. Remove the entry, or correct the package name", [boundary.crate])
}

deny contains finding("LT-004", severity, "noncompliant", pkg.manifest, msg) if {
	audited
	some boundary in boundary_crates
	boundary_in_repository(boundary)
	pkg := package_named(boundary.crate)
	has_tables(pkg)
	actual_rank(pkg, "rust", "unsafe_code") != level_rank("deny")
	msg := sprintf(
		"boundary crate %s sets unsafe_code to %s; a boundary crate sets \"deny\" so each use of `unsafe` needs its own reviewed `#[expect(unsafe_code, reason = ...)]`. Set it in %s, or remove the crate from boundary_crates if it needs no `unsafe`",
		[pkg.name, actual_text(pkg, "rust", "unsafe_code"), where_to_fix(pkg)],
	)
}

deny contains finding("LT-004", severity, "noncompliant", pkg.manifest, msg) if {
	audited
	some boundary in boundary_crates
	boundary_in_repository(boundary)
	pkg := package_named(boundary.crate)
	not nonblank(object.get(boundary, "adr", ""))
	msg := sprintf("boundary crate %s names no ADR. Record why it needs `unsafe` in an architecture decision record and put its identifier in boundary_crates", [pkg.name])
}

deny contains finding("LT-004", severity, "noncompliant", pkg.manifest, msg) if {
	audited
	some boundary in boundary_crates
	boundary_in_repository(boundary)
	pkg := package_named(boundary.crate)
	input.miri.is_reachable == false
	input.miri.is_resolved == true
	msg := sprintf("boundary crate %s has no Miri command the gate reaches. Run `cargo miri test` from a target `make lint` or `make test` reaches, or from a CI workflow step", [pkg.name])
}

deny contains finding("EN-001", severity, "indeterminate", pkg.manifest, msg) if {
	audited
	some boundary in boundary_crates
	boundary_in_repository(boundary)
	pkg := package_named(boundary.crate)
	input.miri.is_reachable == false
	input.miri.is_resolved == false
	msg := sprintf("boundary crate %s: the audit found no Miri command, but part of the gate could not be read (an unresolved Makefile walk, a workflow it could not decode, or a matrix built by an expression), so it cannot say one is missing", [pkg.name])
}
