# gate-lint-coverage: LC-001 to LC-004, and EN-001 for what could not be read.
#
# `gate-workspace-coverage` asks which packages a gate's commands select. This
# rule asks the other half of "does the gate look at all of it": which targets,
# which features, with warnings denied or not, and whether `cargo doc` is a gate.
# femtologging's gate read green with no `cargo doc` anywhere, whole-crate
# Whitaker exclusions nobody rechecked, and lane lists in a script (#421).
#
# The Python envelope found each Cargo surface (a workspace root, or a package
# outside any workspace such as a PyO3 extension in `rust_extension/`), and every
# Clippy, Whitaker and `cargo doc` command the Makefile's `lint` target and every
# workflow reach, with what each one checks (`--all-targets`, features, `-D
# warnings`) and which surface it targets. This policy decides what the facts
# mean, per surface and per tool:
#
#   LC-001  Clippy or Whitaker does not check all targets;
#   LC-002  the features checked do not cover the default set and every declared
#           feature, across the tool's commands on the surface;
#   LC-003  a command does not deny warnings;
#   LC-004  `make lint` does not reach `cargo doc --no-deps` with `RUSTDOCFLAGS`
#           denying warnings over a surface that holds a library.
#
# A tool the gate does not run on a surface is not required (DG-004 asks that
# `make lint` reaches Clippy at all). A command the audit cannot place (a script
# that builds the lanes, an unresolved word among the arguments, a command after
# a `cd` it could not follow, a step that runs for some matrix entries only)
# makes the tool on that surface indeterminate (EN-001): nothing is guessed.
package canon.lint_rules.gate_lint_coverage

import rego.v1

judged_tools := ["clippy", "whitaker"]

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
	input.kind == "policy-input/gate-lint-coverage"
	is_array(input.surfaces)
	is_array(input.runs)
	is_object(input.make_environment)
	is_object(input.cargo_config)
	is_array(input.unreadable)
	is_boolean(input.makefile_resolved)
}

audited if {
	envelope_ok
	count(input.surfaces) > 0
}

default severity := "warning"

severity := data.parameters.severity

default exemptions := []

exemptions := data.parameters.exemptions

repository_name := input.repository.name

nonblank(value) if {
	is_string(value)
	trim_space(value) != ""
}

# An exemption is a reviewed record: repository, surface manifest, tool, why, and
# what event sends it back for review. A blank reason or trigger exempts nothing.
# It stands for the rule's `LC-001` need only: a Clippy lane that must leave out
# the fixture crates (`--lib --bins --tests --benches`, as the runbook allows).
is_exempt(surface, tool) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.surface == surface.manifest
	exemption.tool == tool
	nonblank(exemption.reason)
	nonblank(exemption.review_trigger)
}

# -- the runs of one tool on one surface ----------------------------------------------

runs_for(surface, tool) := [r |
	some r in input.runs
	r.tool == tool
	r.surface == surface.manifest
]

unplaced_runs(tool) := [r |
	some r in input.runs
	r.tool == tool
	r.surface == null
]

# A command the reader could not place or settle: it may check more or less than
# it says, so the tool is not judged on its surface.
unresolved_runs(surface, tool) := [r |
	some r in runs_for(surface, tool)
	r.is_resolved == false
]

# Nothing else the audit could not read, or an unplaced command of the tool
# anywhere, might hold the lane that covers the rest.
nothing_unreadable if count(input.unreadable) == 0

tool_known(surface, tool) if {
	count(unresolved_runs(surface, tool)) == 0
	count(unplaced_runs(tool)) == 0
	input.makefile_resolved == true
	nothing_unreadable
}

list_sources(runs) := concat("; ", sort([r.source | some r in runs]))

# -- envelope and unreadable inputs ---------------------------------------------------

deny contains finding("EN-001", "error", "indeterminate", "", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/gate-lint-coverage envelope at schema version 1"
}

deny contains finding("EN-001", severity, "indeterminate", "", msg) if {
	envelope_ok
	some entry in input.unreadable
	msg := sprintf("a manifest, gate file or line could not be read, so the audit cannot say what the gate checks: %s", [entry])
}

deny contains finding("EN-001", severity, "indeterminate", "Makefile", msg) if {
	envelope_ok
	input.makefile_resolved == false
	msg := "the Makefile walk from lint skipped a target it could not resolve (a `make -C`, or a variable that names a target), so the audit cannot say which lint commands the gate runs"
}

deny contains finding("EN-001", severity, "indeterminate", surface.manifest, msg) if {
	audited
	some surface in input.surfaces
	some tool in array.concat(judged_tools, ["doc"])
	runs := unresolved_runs(surface, tool)
	count(runs) > 0
	msg := sprintf(
		"%s: the audit cannot place %d %s command(s) on this surface (%s), so it does not judge what %s checks there. Commands: %s",
		[surface.manifest, count(runs), tool, why_unresolved(runs), tool, list_sources(runs)],
	)
}

deny contains finding("EN-001", severity, "indeterminate", "", msg) if {
	audited
	some tool in array.concat(judged_tools, ["doc"])
	runs := unplaced_runs(tool)
	count(runs) > 0
	msg := sprintf(
		"the audit cannot say which Cargo surface %d %s command(s) target (%s), so it does not judge %s on any surface. Commands: %s%s",
		[count(runs), tool, why_unresolved(runs), tool, list_sources(runs), lanes_note(runs)],
	)
}

why_unresolved(runs) := concat("; ", sort({r.why | some r in runs; r.why != ""}))

# The lane inputs a script was given are carried so the finding shows what could
# not be read (and a person or the survey can measure it another way).
lane_inputs(runs) := sort({lane | some r in runs; some lane in r.lanes})

lanes_note(runs) := "" if count(lane_inputs(runs)) == 0

lanes_note(runs) := sprintf(". Lane inputs it was given: %s", [concat("; ", lane_inputs(runs))]) if count(lane_inputs(runs)) > 0

# -- LC-001: not every target -------------------------------------------------------------

deny contains finding("LC-001", severity, "noncompliant", surface.manifest, msg) if {
	audited
	some surface in input.surfaces
	some tool in judged_tools
	runs := runs_for(surface, tool)
	count(runs) > 0
	tool_known(surface, tool)
	count([r | some r in runs; r.all_targets == true]) == 0
	not is_exempt(surface, tool)
	msg := sprintf(
		"%s: no %s command checks all targets, so tests, benches and examples are not linted. Add `--all-targets` (or `--lib --bins --tests --benches --examples`) to: %s. A lane that must leave fixture crates out can be exempted by surface and tool with a reason and a review trigger in the rule's parameters",
		[surface.manifest, tool, list_sources(runs)],
	)
}

# -- LC-002: not every feature --------------------------------------------------------------

declared(surface) := {f | some f in surface.features}

# A declared feature is covered by a run that selects its package and either
# enables every feature or names it: as written (`a/fast`), or by its bare name,
# which enables it in every selected package that has it. A `-p a` run therefore
# covers `a`'s features only.
covered(surface, tool) := {d |
	some d in declared(surface)
	some r in runs_for(surface, tool)
	run_covers(surface, r, d)
}

run_covers(_, r, d) if {
	r.all_features == true
	selects_package(r, d)
}

run_covers(_, r, d) if {
	some f in r.features
	d == f
	selects_package(r, d)
}

# A bare name enables the feature in every selected package that has it, except
# under resolver 1, where it applies to the one package the command runs in.
run_covers(surface, r, d) if {
	some f in r.features
	not contains(f, "/")
	endswith(d, concat("", ["/", f]))
	selects_package(r, d)
}

# Resolver 1 applies `--features` and `--no-default-features` to the package the
# command runs in, whatever it selects, so a surface of several packages under it
# (or under a resolver the audit cannot tell) is not judged: it is indeterminate.
feature_scoping_known(surface) if count(surface.packages) == 1

feature_scoping_known(surface) if surface.resolver in {"2", "3"}

deny contains finding("EN-001", severity, "indeterminate", surface.manifest, msg) if {
	audited
	some surface in input.surfaces
	some tool in judged_tools
	runs := runs_for(surface, tool)
	count(runs) > 0
	not feature_scoping_known(surface)
	count(surface.features) + count(surface.default_packages) > 0
	msg := sprintf(
		"%s: the workspace uses feature resolver %s, which applies command-line features to the package a command runs in, so the audit does not judge which features the %s commands check. Set `resolver = \"2\"` (or a root package of edition 2021 or later) to have them judged. Commands: %s",
		[surface.manifest, surface.resolver, tool, list_sources(runs)],
	)
}

# The envelope says which packages a run selects, as Cargo does (`-p`,
# `--workspace` less `--exclude`, a member's manifest, or the default members). A
# feature of a one-package surface is unqualified and always selected.
selects_package(_, d) if not contains(d, "/")

selects_package(r, d) if split(d, "/")[0] in r.selected

# A package's default feature set is checked by a command that selects the
# package and does not switch the defaults off.
default_checked(surface, tool, name) if {
	some r in runs_for(surface, tool)
	r.no_default_features == false
	name in r.selected
}

default_checked(surface, tool, name) if {
	some r in runs_for(surface, tool)
	r.all_features == true
	name in r.selected
}

# `--no-default-features --features default` asks for the defaults by name.
default_checked(surface, tool, name) if {
	some r in runs_for(surface, tool)
	some f in r.features
	f in {"default", concat("", [name, "/default"])}
	name in r.selected
}

uncovered_features(surface, tool) := sort(declared(surface) - covered(surface, tool))

deny contains finding("LC-002", severity, "noncompliant", surface.manifest, msg) if {
	audited
	some surface in input.surfaces
	some tool in judged_tools
	runs := runs_for(surface, tool)
	count(runs) > 0
	tool_known(surface, tool)
	feature_scoping_known(surface)
	missing := uncovered_features(surface, tool)
	default_missing := not_default_checked(surface, tool)
	count(missing) + count(default_missing) > 0
	msg := sprintf(
		"%s: the %s commands do not check %s. Add an `--all-features` run, or a lane for each: %s",
		[surface.manifest, tool, describe_gaps(missing, default_missing), list_sources(runs)],
	)
}

# The default set counts only for a package that declares a `default` feature,
# and is judged for each such package.
not_default_checked(surface, tool) := sort([sprintf("the default feature set of %s", [name]) |
	some name in surface.default_packages
	not default_checked(surface, tool, name)
])

describe_gaps(missing, default_missing) := concat(", ", array.concat(
	[sprintf("feature %s", [f]) | some f in missing],
	default_missing,
))

# -- LC-003: warnings not denied ------------------------------------------------------------

# What a run's command line says, falling back to what its environment says. A
# workflow's own `env` is not read, so a workflow run that says nothing is
# unknown.
warnings_state(run) := "denied" if run.deny_warnings == true

warnings_state(run) := "not_denied" if run.deny_warnings == false

warnings_state(run) := state if {
	run.deny_warnings == null
	state := environment_state(run)
}

exported_flags(run) := input.make_environment.rustdocflags if run.tool == "doc"

exported_flags(run) := input.make_environment.rustflags if run.tool != "doc"

is_make_run(run) if startswith(run.source, "Makefile:")

# The last level flag for `warnings` decides, so `-D warnings -W warnings` does
# not deny.
warnings_levels(text) := regex.find_all_string_submatch_n(`(?:^|\s)(-[DWAF]|--(?:deny|warn|allow|forbid))(?:\s|=)*warnings\b`, text, -1)

denying_flags := {"-D", "-F", "--deny", "--forbid"}

denies_text(text) := true if denies_last(text)

denies_text(text) := false if not denies_last(text)

denies_last(text) if {
	levels := warnings_levels(text)
	count(levels) > 0
	levels[count(levels) - 1][1] in denying_flags
}

# What a Makefile exports reaches the commands its recipes run, not a workflow
# step that runs `cargo` directly: that step's own `env` is not read.
# A workflow step's own `RUSTFLAGS` (not read) overrides `.cargo/config.toml`, so
# even a configuration that denies warnings does not settle it.
environment_state(run) := "unknown" if not is_make_run(run)

environment_state(run) := "unknown" if {
	is_make_run(run)
	input.make_environment.is_resolved == false
}

environment_state(run) := state if {
	is_make_run(run)
	input.make_environment.is_resolved == true
	exported := exported_flags(run)
	exported != null
	state := {true: "denied", false: "not_denied"}[denies_text(exported)]
}

# Nothing exported: the configuration decides a Clippy or Whitaker run; a
# `cargo doc` run has no configuration reading, and a workflow run may have an `env`.
environment_state(run) := state if {
	is_make_run(run)
	input.make_environment.is_resolved == true
	exported_flags(run) == null
	state := unexported_state(run)
}

unexported_state(run) := "unknown" if {
	run.tool != "doc"
	input.cargo_config.is_readable == false
}

unexported_state(run) := "denied" if {
	run.tool != "doc"
	input.cargo_config.is_readable == true
	input.cargo_config.denies_warnings == true
}

unexported_state(run) := "not_denied" if {
	input.cargo_config.is_readable == true
	not config_denies(run)
	is_make_run(run)
}

unexported_state(run) := "unknown" if {
	input.cargo_config.is_readable == true
	not config_denies(run)
	not is_make_run(run)
}

config_denies(run) if {
	run.tool != "doc"
	input.cargo_config.denies_warnings == true
}

deny contains finding("LC-003", severity, "noncompliant", run.surface, msg) if {
	audited
	some run in input.runs
	run.is_resolved == true
	run.surface != null
	warnings_state(run) == "not_denied"
	msg := sprintf(
		"%s: this %s command does not deny warnings, so a warning passes the gate. %s",
		[run.source, run.tool, fix_for(run)],
	)
}

# A Clippy or Whitaker command whose warning state the audit cannot tell: its
# workflow `env` is not read, or the Makefile's export could not be expanded.
deny contains finding("EN-001", severity, "indeterminate", surface.manifest, msg) if {
	audited
	some surface in input.surfaces
	some tool in judged_tools
	unknown := [r |
		some r in runs_for(surface, tool)
		r.is_resolved == true
		warnings_state(r) == "unknown"
	]
	count(unknown) > 0
	msg := sprintf(
		"%s: the audit cannot tell whether %d %s command(s) deny warnings (a workflow `env` is not read, or an exported value could not be expanded), so it does not judge LC-003 for them. Commands: %s",
		[surface.manifest, count(unknown), tool, list_sources(unknown)],
	)
}

fix_for(run) := "Set `RUSTDOCFLAGS=\"-D warnings\"` on the command or export it in the Makefile." if run.tool == "doc"

fix_for(run) := "Set `RUSTFLAGS=\"-D warnings\"` on the command (or export it, or set `build.rustflags` in .cargo/config.toml)." if run.tool == "whitaker"

fix_for(run) := "Set `RUSTFLAGS=\"-D warnings\"` on the command, or pass `-- -D warnings` (or set `build.rustflags` in .cargo/config.toml)." if run.tool == "clippy"

# -- LC-004: the rustdoc gate ---------------------------------------------------------------

doc_runs(surface) := [r |
	some r in runs_for(surface, "doc")
	is_make_run(r)
]

# A `cargo doc` command selects the whole surface when it spells out the
# workspace, or when the surface holds one package.
selects_all(surface, run) if run.workspace == true

selects_all(surface, run) if count(surface.packages) == 1

selects_all(surface, run) if {
	count({p | some p in surface.packages} - {p | some p in run.packages}) == 0
}

is_gate_doc(surface, run) if {
	run.is_resolved == true
	run.no_deps == true
	run.narrows == false
	selects_all(surface, run)
	warnings_state(run) == "denied"
}

# A `cargo doc` that would be a gate if its warnings are denied, but whether they
# are is unknown (an exported value the audit could not expand, say): the answer
# is open, not "no gate".
doc_state(run) := warnings_state(run)

doc_state(run) := "unknown" if not warnings_state(run)

doc_gate_unknown(surface) if {
	some r in doc_runs(surface)
	r.is_resolved == true
	r.no_deps == true
	selects_all(surface, r)
	doc_state(r) == "unknown"
}

deny contains finding("LC-004", severity, "noncompliant", surface.manifest, msg) if {
	audited
	some surface in input.surfaces
	surface.has_library == true
	count(unresolved_runs(surface, "doc")) == 0
	count(unplaced_runs("doc")) == 0
	input.makefile_resolved == true
	nothing_unreadable
	count([r | some r in doc_runs(surface); is_gate_doc(surface, r)]) == 0
	not doc_gate_unknown(surface)
	msg := doc_message(surface)
}

deny contains finding("EN-001", severity, "indeterminate", surface.manifest, msg) if {
	audited
	some surface in input.surfaces
	surface.has_library == true
	count([r | some r in doc_runs(surface); is_gate_doc(surface, r)]) == 0
	doc_gate_unknown(surface)
	msg := sprintf(
		"%s: `make lint` runs `cargo doc --no-deps` over the whole surface, but the audit cannot tell whether warnings are denied (RUSTDOCFLAGS is exported with a value it could not expand, or not visible), so it does not judge the rustdoc gate. Commands: %s",
		[surface.manifest, list_sources(doc_runs(surface))],
	)
}

doc_message(surface) := sprintf(
	"%s holds a library, but `make lint` reaches no `cargo doc`. Add `RUSTDOCFLAGS=\"-D warnings\" cargo doc --no-deps --workspace` (for a standalone crate, `--manifest-path %s`) to a target `lint` reaches, so a broken intra-doc link or bare URL fails the gate",
	[surface.manifest, surface.manifest],
) if count(doc_runs(surface)) == 0

doc_message(surface) := sprintf(
	"%s holds a library, and `make lint` reaches `cargo doc`, but not as a gate over the whole surface: it needs `--no-deps`, a selection of every package (`--workspace`) and `RUSTDOCFLAGS` denying warnings. Commands: %s",
	[surface.manifest, list_sources(doc_runs(surface))],
) if count(doc_runs(surface)) > 0
