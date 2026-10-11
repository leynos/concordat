# documentation-presence: DG-001 and DG-002.
#
# Input is a policy-input/documentation-presence envelope. The Makefile's
# variables are expanded, and `lint` is followed through its prerequisites, by
# the envelope builder: each `interrogate` invocation a recipe `lint` reaches
# arrives as a structured run (paths, threshold, verbose level, relaxing flags,
# whether its recipe guards against MISSED rows). The policy reasons only over
# those facts, and anything it cannot prove is `indeterminate` (fail closed).
#
# DG-001: a run reachable from `make lint` covers every Python file the
# repository owns, at a threshold of at least 100, with no relaxing ignore flag
# and no exclusion beyond test data and vendored trees.
# DG-002: that run is exact. interrogate rounds its percentage to one decimal,
# so `--fail-under 100` passes a lone gap in a repository with thousands of
# definitions; the run must print every definition (verbose level 2) and its
# recipe must fail on any MISSED row.
package canon.lint_rules.documentation_presence

import rego.v1

# -- parameters --------------------------------------------------------------

default required_threshold := 100

required_threshold := data.parameters.required_threshold

default exempt_pattern := `(^|/)(fixtures|vendor|node_modules|\.venv|\.uv-cache|\.uv-tools)(/|$)`

exempt_pattern := data.parameters.exempt_pattern

default required_verbosity := 2

required_verbosity := data.parameters.required_verbosity

default makefile_path := "Makefile"

makefile_path := input.makefile_path

finding(rule_id, verdict, line, msg) := {
	"rule_id": rule_id,
	"severity": "error",
	"verdict": verdict,
	"path": makefile_path,
	"line": line,
	"msg": msg,
}

# -- envelope ------------------------------------------------------------------

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/documentation-presence"
}

deny contains f if {
	not envelope_ok
	f := finding(
		"EN-001", "indeterminate", 0,
		"policy input is not a policy-input/documentation-presence envelope at schema version 1",
	)
}

# -- scope -----------------------------------------------------------------------

is_exempt(path) if regex.match(exempt_pattern, path)

required_files := [path |
	some path in input.python_files
	not is_exempt(path)
]

# A repository that owns no Python outside exempt trees has nothing to document.
in_scope if {
	envelope_ok
	count(required_files) > 0
}

# -- configuration of one run ---------------------------------------------------------

config_name(run) := run.config if run.config != null

config_name(run) := "pyproject.toml" if run.config == null

config_fact(run) := fact if {
	some fact in input.configs
	fact.path == config_name(run)
}

# The `[tool.interrogate]` table a run reads: empty when the default
# pyproject.toml is absent (interrogate then uses its own defaults).
config_table(run) := fact.table if {
	fact := config_fact(run)
	fact.error == null
}

config_table(run) := {} if {
	run.config == null
	not config_fact(run)
}

config_unreadable(run) if {
	fact := config_fact(run)
	fact.error != null
}

config_unreadable(run) if {
	run.config != null
	not config_fact(run)
}

number_pattern := `^[0-9]+(\.[0-9]+)?$`

# The effective threshold: the command line wins over the configuration file.
threshold_text(run) := run.fail_under if run.fail_under != null

threshold_text(run) := sprintf("%v", [value]) if {
	run.fail_under == null
	value := object.get(config_table(run), "fail-under", null)
	value != null
}

threshold_value(run) := to_number(threshold_text(run)) if {
	regex.match(number_pattern, threshold_text(run))
}

verbosity(run) := max([run.verbose, configured]) if {
	configured := object.get(config_table(run), "verbose", 0)
	is_number(configured)
}

# Relaxing options from the configuration file: any `ignore-*` switch set true
# or a non-empty `ignore-regex`. Ignoring overloaded stubs is not a relaxation
# (Ruff forbids a docstring on them), so it is not reported.
config_ignores(run) := {key |
	some key, value in config_table(run)
	startswith(key, "ignore-")
	key != "ignore-overloaded-functions"
	value == true
}

config_ignores(run) := {"ignore-regex"} if {
	count(object.get(config_table(run), "ignore-regex", [])) > 0
}

all_ignores(run) := {flag | some flag in run.ignores} | config_ignores(run)

all_excludes(run) := array.concat(
	run.excludes,
	[entry | some entry in object.get(config_table(run), "exclude", []); is_string(entry)],
)

disallowed_excludes(run) := sort([entry |
	some entry in all_excludes(run)
	not regex.match(exempt_pattern, entry)
])

# -- coverage ------------------------------------------------------------------------

under(path, root) if root == "."

under(path, root) if path == root

under(path, root) if startswith(path, concat("", [trim_suffix(root, "/"), "/"]))

excluded_by(run, path) if {
	some entry in all_excludes(run)
	under(path, trim_suffix(entry, "/"))
}

covered_by(run, path) if {
	some root in run.paths
	under(path, root)
	not excluded_by(run, path)
}

covered(path) if {
	some run in resolved_runs
	covered_by(run, path)
}

# A file under a disallowed exclusion is reported by that exclusion's finding,
# not a second time as uncovered.
excluded_somewhere(path) if {
	some run in resolved_runs
	excluded_by(run, path)
}

resolved_runs := [run | some run in input.runs; run.resolved]

unresolved_runs := [run | some run in input.runs; not run.resolved]

uncovered := [path |
	some path in required_files
	not covered(path)
	not excluded_somewhere(path)
]

top_level(path) := parts[0] if {
	parts := split(path, "/")
	count(parts) > 1
}

top_level(path) := path if count(split(path, "/")) == 1

first_line := input.runs[0].line if count(input.runs) > 0

default first_line := 0

# -- DG-001 --------------------------------------------------------------------------

deny contains f if {
	in_scope
	not input.applicability.root_makefile
	f := finding(
		"DG-001", "noncompliant", 0,
		"the repository owns Python but has no root Makefile, so `make lint` cannot run interrogate",
	)
}

deny contains f if {
	in_scope
	input.applicability.root_makefile
	not input.lint_target
	f := finding(
		"DG-001", "noncompliant", 0,
		"the root Makefile has no `lint` target, so no interrogate run is reachable from `make lint`",
	)
}

deny contains f if {
	in_scope
	input.lint_target
	count(input.runs) == 0
	not input.reachability_resolved
	f := finding(
		"DG-001", "indeterminate", 0,
		"`make lint` names a prerequisite or sub-make this policy cannot resolve, so it cannot prove that no interrogate run is reachable",
	)
}

deny contains f if {
	in_scope
	input.lint_target
	count(input.runs) == 0
	input.reachability_resolved
	f := finding("DG-001", "noncompliant", 0, no_run_message)
}

no_run_message := sprintf(
	"`make lint` reaches no interrogate run; target %s runs it outside `lint`",
	[concat(", ", input.interrogate_elsewhere)],
) if {
	count(input.interrogate_elsewhere) > 0
}

default no_run_message := "`make lint` reaches no interrogate run; Python docstring coverage is not enforced"

deny contains f if {
	in_scope
	some run in unresolved_runs
	f := finding(
		"DG-001", "indeterminate", run.line,
		sprintf("target %s runs interrogate through text this policy cannot resolve (`%s`)", [run.target, run.command]),
	)
}

deny contains f if {
	in_scope
	some run in resolved_runs
	config_unreadable(run)
	f := finding(
		"DG-001", "indeterminate", run.line,
		sprintf("target %s reads interrogate configuration from `%s`, which is missing or not TOML", [run.target, config_name(run)]),
	)
}

readable(run) if not config_unreadable(run)

deny contains f if {
	in_scope
	some run in resolved_runs
	readable(run)
	not threshold_text(run)
	f := finding(
		"DG-001", "noncompliant", run.line,
		sprintf("target %s runs interrogate with no `--fail-under` or `fail-under` setting; its default threshold is 80", [run.target]),
	)
}

deny contains f if {
	in_scope
	some run in resolved_runs
	readable(run)
	threshold_text(run)
	not threshold_value(run)
	f := finding(
		"DG-001", "indeterminate", run.line,
		sprintf("target %s sets the interrogate threshold to `%s`, which is not a number", [run.target, threshold_text(run)]),
	)
}

deny contains f if {
	in_scope
	some run in resolved_runs
	readable(run)
	threshold_value(run) < required_threshold
	f := finding(
		"DG-001", "noncompliant", run.line,
		sprintf("target %s runs interrogate at a threshold of %v; it must be at least %v", [run.target, threshold_value(run), required_threshold]),
	)
}

deny contains f if {
	in_scope
	some run in resolved_runs
	readable(run)
	flags := all_ignores(run)
	count(flags) > 0
	f := finding(
		"DG-001", "noncompliant", run.line,
		sprintf("target %s relaxes interrogate with %s; document the definitions instead", [run.target, concat(", ", sort(flags))]),
	)
}

deny contains f if {
	in_scope
	some run in resolved_runs
	readable(run)
	excludes := disallowed_excludes(run)
	count(excludes) > 0
	f := finding(
		"DG-001", "noncompliant", run.line,
		sprintf("target %s excludes %s from interrogate; only test data and vendored trees may be excluded", [run.target, concat(", ", excludes)]),
	)
}

deny contains f if {
	in_scope
	count(unresolved_runs) == 0
	count(resolved_runs) > 0
	some group in {top_level(path) | some path in uncovered}
	members := [path | some path in uncovered; top_level(path) == group]
	f := finding(
		"DG-001", "noncompliant", first_line,
		sprintf("%d Python files under `%s` are outside every interrogate run (for example %s)", [count(members), group, members[0]]),
	)
}

# -- DG-002 --------------------------------------------------------------------------

deny contains f if {
	in_scope
	some run in resolved_runs
	readable(run)
	verbosity(run) < required_verbosity
	f := finding(
		"DG-002", "noncompliant", run.line,
		sprintf("target %s runs interrogate at verbose level %d; level %d prints a MISSED row per undocumented definition", [run.target, verbosity(run), required_verbosity]),
	)
}

deny contains f if {
	in_scope
	some run in resolved_runs
	not run.guarded
	f := finding(
		"DG-002", "noncompliant", run.line,
		sprintf("target %s does not fail on a MISSED row; interrogate rounds to one decimal, so `--fail-under 100` passes a lone gap in a large repository", [run.target]),
	)
}
