# spelling-config-baseline: PD-007 to PD-013.
#
# Input is a policy-input/spelling-config-baseline envelope. Makefile facts come
# from the pinned `makeutil parse` report, workflows from a YAML decode, the
# overlay from a TOML decode, `.gitignore` as its lines, and vendored machinery
# as the paths that matched the package's globs. The policy reasons only over
# those facts, and anything it cannot prove is `indeterminate` (fail closed).
#
# The rule enforces typos-config-builder's 0.1.x design: one pinned `gate`
# command regenerates `typos.toml` from the live shared dictionary, runs the
# builder's pinned Typos, and enforces the shared phrase corrections. The
# regenerated file is never drift-checked, and no repository carries its own
# Typos pin, generator, or phrase checker.
package canon.lint_rules.spelling_config_baseline

import rego.v1

# -- parameters --------------------------------------------------------------

default spelling_target := "spelling"

spelling_target := data.parameters.spelling_target

default builder_floor := "v0.1.3"

builder_floor := data.parameters.builder_floor

default builder_repository := "github.com/leynos/typos-config-builder"

builder_repository := data.parameters.builder_repository

default legacy_variables := [
	"TYPOS_VERSION",
	"PATHSPEC_VERSION",
	"TYPOS_CONFIG_BUILDER_COMMIT",
	"TYPOS_CONFIG_BUILDER_REV",
	"TYPOS_CONFIG_BUILDER_REVISION",
	"TYPOS_CONFIG_BUILDER_SHA",
]

legacy_variables := data.parameters.legacy_variables

default legacy_targets := ["spelling-helper-test", "spelling-phrase-check", "spelling-config"]

legacy_targets := data.parameters.legacy_targets

default gitignore_entries := [".typos-oxendict-base.json", ".typos-oxendict-base.toml"]

gitignore_entries := data.parameters.gitignore_entries

default overlay_schema := 1

overlay_schema := data.parameters.overlay_schema

# The canonical AGENTS.md spelling block, keyed by the builder release that
# publishes it. The manifest carries the texts; the Rego default is empty, so a
# run without them is indeterminate rather than compared with nothing.
default agents_md_blocks := {}

agents_md_blocks := data.parameters.agents_md_blocks

default makefile_path := "Makefile"

makefile_path := input.makefile.source.path

finding(rule_id, verdict, path, line, msg) := {
	"rule_id": rule_id,
	"severity": "error",
	"verdict": verdict,
	"path": path,
	"line": line,
	"msg": msg,
}

# -- envelope and applicability ------------------------------------------------

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/spelling-config-baseline"
}

deny contains f if {
	not envelope_ok
	f := finding(
		"EN-001", "indeterminate", makefile_path, 0,
		"policy input is not a policy-input/spelling-config-baseline envelope at schema version 1",
	)
}

rules_defining(target) := [rule |
	some rule in input.makefile.rules
	target in rule.targets
]

spelling_target_defined if {
	input.makefile != null
	count(rules_defining(spelling_target)) > 0
}

workflow_mentions_typos if {
	some workflow in input.workflows
	workflow.error == null
	some _, value in walk(workflow.parsed)
	is_string(value)
	contains(lower(value), "typos")
}

# A repository gates spelling when it has any piece of a spelling setup: the
# Makefile target, the generated or overlay configuration, vendored machinery,
# or a workflow that runs typos. A repository with none of them is out of scope.
spelling_evidence if spelling_target_defined

spelling_evidence if input.applicability.typos_config == true

spelling_evidence if input.applicability.typos_local == true

spelling_evidence if count(input.vendored) > 0

spelling_evidence if workflow_mentions_typos

applicable if {
	envelope_ok
	spelling_evidence
}

# A workflow that cannot be decoded might run typos, so with no other
# evidence the repository's scope is unknown: indeterminate, never passed.
deny contains f if {
	envelope_ok
	not spelling_evidence
	some workflow in input.workflows
	workflow.error != null
	f := finding(
		"PD-010", "indeterminate", workflow.path, 0,
		sprintf("%s could not be decoded, so whether the repository gates spelling is unknown: %s", [workflow.path, workflow.error]),
	)
}

# -- Make variable expansion -----------------------------------------------------
#
# As in markdown-formatting-baseline: a variable with exactly one
# unconditional, non-`define` assignment has one value the policy can
# substitute, over three passes. A continuation line (`\` then a newline)
# joins its lines with a space, as Make does.

variable_name_pattern := `^[A-Za-z_][A-Za-z0-9_]*$`

assignments_of(name) := [variable |
	some variable in input.makefile.variables
	variable.name == name
]

simple_assignment(variable) if {
	regex.match(variable_name_pattern, variable.name)
	count(variable.conditions) == 0
	variable.define_block == false
}

single_valued(name) if {
	assignments := assignments_of(name)
	count(assignments) == 1
	simple_assignment(assignments[0])
}

joined(text) := regex.replace(text, `\\\n[[:space:]]*`, " ")

variable_value[name] := joined(variable.raw_value) if {
	some variable in input.makefile.variables
	name := variable.name
	single_valued(name)
}

env_assignment_prefix := `[A-Za-z_][A-Za-z0-9_]*=("[^"]*"|'[^']*'|[^[:space:]]*)[[:space:]]+`

# A variable assigned under conditions, every one of whose values is empty or
# only environment assignments (`LOCAL_TOOL_ENV = PATH="..."` on one platform,
# empty on another), can only ever prefix a command with its environment. It
# is erased for the command-word proof rather than left unresolved; any other
# multiply assigned variable stays unresolved and is not guessed.
prefix_only_value(value) if regex.match(
	sprintf(`^[[:space:]]*((%s)|[A-Za-z_][A-Za-z0-9_]*=("[^"]*"|'[^']*'|[^[:space:]]*)[[:space:]]*)*$`, [env_assignment_prefix]),
	joined(value),
)

prefix_only_variable(name) if {
	assignments := assignments_of(name)
	count(assignments) > 0
	not single_valued(name)
	every variable in assignments {
		variable.define_block == false
		prefix_only_value(variable.raw_value)
	}
}

prefix_only_names := {variable.name |
	some variable in input.makefile.variables
	prefix_only_variable(variable.name)
}

environment_variables := {"HOME", "PATH", "PWD", "SHELL", "TMPDIR", "USER"}

replacements := object.union_n([
	{reference: sprintf("$%s", [name]) |
		some name in environment_variables
		not single_valued(name)
		some reference in [sprintf("$(%s)", [name]), sprintf("${%s}", [name])]
	},
	{reference: "" |
		some name in prefix_only_names
		some reference in [sprintf("$(%s)", [name]), sprintf("${%s}", [name])]
	},
	{sprintf("$(%s)", [name]): value | some name, value in variable_value},
	{sprintf("${%s}", [name]): value | some name, value in variable_value},
])

expand(text) := strings.replace_n(
	replacements,
	strings.replace_n(replacements, strings.replace_n(replacements, joined(text))),
)

unresolved_references(text) := {name |
	some match in regex.find_all_string_submatch_n(
		`\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]`,
		expand(text), -1,
	)
	name := match[1]
	name != "MAKE"
}

# -- static closure from the spelling target -------------------------------------
#
# Prerequisites and complete, literal same-file `$(MAKE) target` chains are the
# only edges the fact model proves, as in markdown-formatting-baseline.

static_make_recipe_pattern := sprintf(
	`^[[:space:]]*[-@+]*[[:space:]]*(%s)*\$\(MAKE\)[[:space:]]+[A-Za-z0-9_.-]+([[:space:]]*&&[[:space:]]*(%s)*\$\(MAKE\)[[:space:]]+[A-Za-z0-9_.-]+)*[[:space:]]*$`,
	[env_assignment_prefix, env_assignment_prefix],
)

static_make_recipe_is_binding(recipe) if regex.match(static_make_recipe_pattern, recipe.text)

static_make_segment_target(segment) := target if {
	matches := regex.find_all_string_submatch_n(
		sprintf(
			`^[[:space:]]*[-@+]*[[:space:]]*(%s)*\$\(MAKE\)[[:space:]]+([A-Za-z0-9_.-]+)[[:space:]]*$`,
			[env_assignment_prefix],
		),
		segment, 1,
	)
	count(matches) == 1
	target := matches[0][3]
}

static_make_targets(recipe) := {target |
	static_make_recipe_is_binding(recipe)
	some segment in split(recipe.text, "&&")
	target := static_make_segment_target(segment)
}

recipe_mentions_make(recipe) if contains(recipe.text, "$(MAKE)")

recipe_mentions_make(recipe) if contains(recipe.text, "${MAKE}")

known_targets := {target |
	some rule in input.makefile.rules
	some target in rule.targets
}

target_edges[target] contains next if {
	some rule in input.makefile.rules
	some target in rule.targets
	some next in rule.prerequisites
}

target_edges[target] contains next if {
	some rule in input.makefile.rules
	some target in rule.targets
	some recipe in rule.recipes
	some next in static_make_targets(recipe)
	next in known_targets
}

target_graph := {target: object.get(target_edges, target, []) | some target in known_targets}

reachable_from(root) := {root} | graph.reachable(target_graph, {root})

path_rule(root, rule) if {
	some target in reachable_from(root)
	target in rule.targets
}

path_recipes(root) := {recipe |
	some rule in input.makefile.rules
	path_rule(root, rule)
	some recipe in rule.recipes
}

conditional_path(root) if {
	some rule in input.makefile.rules
	path_rule(root, rule)
	count(rule.conditions) > 0
}

dynamic_make_delegation(root) if {
	some recipe in path_recipes(root)
	regex.match(`(\$\(MAKE\)|\$\{MAKE\})[[:space:]]+(\$\(|-C)`, recipe.text)
}

unproven_make_delegation(root) if {
	some recipe in path_recipes(root)
	recipe_mentions_make(recipe)
	not static_make_recipe_is_binding(recipe)
}

root_definitions_ambiguous(root) if count(rules_defining(root)) > 1

root_definitions_ambiguous(root) if {
	some rule in rules_defining(root)
	rule.double_colon == true
}

includes_present if count(input.makefile.includes) > 0

parse_recovered if input.makefile.parse.status != "complete"

closure_provable if {
	spelling_target_defined
	not includes_present
	not parse_recovered
	not root_definitions_ambiguous(spelling_target)
	not conditional_path(spelling_target)
	not dynamic_make_delegation(spelling_target)
	not unproven_make_delegation(spelling_target)
}

closure_indeterminate_reason := "Makefile includes other files; the spelling recipes cannot be proven" if {
	includes_present
} else := "Makefile parse was recovered from syntax errors; facts may be incomplete" if {
	parse_recovered
} else := sprintf("the %q target has multiple or double-colon definitions", [spelling_target]) if {
	root_definitions_ambiguous(spelling_target)
} else := sprintf("the %q target reaches a conditional Make rule; its recipes cannot be proven", [spelling_target]) if {
	conditional_path(spelling_target)
} else := sprintf("the %q target reaches dynamic recursive Make; its recipes cannot be proven", [spelling_target]) if {
	dynamic_make_delegation(spelling_target)
} else := sprintf("the %q target reaches an unproven recursive Make invocation; its recipes cannot be proven", [spelling_target]) if {
	unproven_make_delegation(spelling_target)
}

spelling_recipes := {recipe |
	closure_provable
	some recipe in path_recipes(spelling_target)
	not regex.match(`^[[:space:]]*[-@+]*[[:space:]]*#`, recipe.text)
}

# -- builder invocations ---------------------------------------------------------
#
# The builder runs through a uv runner that names its release (`uvx --from
# "git+https://github.com/leynos/typos-config-builder.git@v0.1.3"
# typos-config-builder gate`, or `uv tool run --from ...`), or directly as the
# command word, unpinned. The runner's options are captured so the `--from`
# spec can be read; the builder's own arguments are captured separately.

segment_start := `(^|[;&(]|[^|]\|)[[:space:]]*[-@+]*[[:space:]]*`

runner := `(?:uvx|uv[[:space:]]+tool[[:space:]]+run)`

builder_core := sprintf(
	`%s(?:%s)*(?:(%s)((?:[[:space:]]+[^[:space:];|&]+)*?)[[:space:]]+)?(?:[^[:space:];|&()"']*/)?typos-config-builder((?:[[:space:]][^;|&]*)?)`,
	[segment_start, env_assignment_prefix, runner],
)

builder_pattern := builder_core

builder_binding_pattern := sprintf(`%s(?:&&[^;|&]*)*$`, [builder_core])

# Each builder invocation as [runner, runner options, builder arguments].
builder_invocations(recipe) := {[m[n - 3], m[n - 2], m[n - 1]] |
	some m in regex.find_all_string_submatch_n(builder_pattern, expand(recipe.text), -1)
	n := count(m)
}

binding_invocations(recipe) := {[m[n - 3], m[n - 2], m[n - 1]] |
	recipe.ignore_errors == false
	some m in regex.find_all_string_submatch_n(builder_binding_pattern, expand(recipe.text), -1)
	n := count(m)
}

tokens(text) := [token |
	some token in split(trim_space(text), " ")
	token != ""
]

subcommand(invocation) := tokens(invocation[2])[0]

runs_gate(invocation) if subcommand(invocation) == "gate"

checks_drift(invocation) if "--check" in tokens(invocation[2])

# The `--from` spec with its quotes removed, or none when the runner has none.
from_spec(invocation) := spec if {
	matches := regex.find_all_string_submatch_n(
		`--from(?:=|[[:space:]]+)["']?([^"'[:space:]]+)["']?`, invocation[1], 1,
	)
	count(matches) == 1
	spec := matches[0][1]
}

git_ref(spec) := parts[3] if {
	parts := regex.find_all_string_submatch_n(
		`^git\+https://(github\.com/[^/@]+/[^/@]+?)(\.git)?@(.+)$`, spec, 1,
	)[0]
	parts[1] == builder_repository
}

release_version(ref) := [to_number(p[1]), to_number(p[2]), to_number(p[3])] if {
	p := regex.find_all_string_submatch_n(`^v?([0-9]+)\.([0-9]+)\.([0-9]+)$`, ref, 1)[0]
}

version_at_least(version, floor) if version[0] > floor[0]

version_at_least(version, floor) if {
	version[0] == floor[0]
	version[1] > floor[1]
}

version_at_least(version, floor) if {
	version[0] == floor[0]
	version[1] == floor[1]
	version[2] >= floor[2]
}

floor_version := release_version(builder_floor)

# Why a pin is not an acceptable release: the finding names the pin it saw.
pin_problem(invocation) := "runs typos-config-builder without pinning a release; run it with uvx --from the tagged repository" if {
	not from_spec(invocation)
} else := sprintf("pins typos-config-builder with unresolved Make variables (%s); the release cannot be proven", [spec]) if {
	spec := from_spec(invocation)
	contains(spec, "$(")
} else := sprintf("pins %s, not the %s repository", [spec, builder_repository]) if {
	spec := from_spec(invocation)
	not git_ref(spec)
} else := sprintf("pins typos-config-builder to commit %s; pin a release tag at or above %s", [ref, builder_floor]) if {
	ref := git_ref(from_spec(invocation))
	regex.match(`^[0-9a-f]{7,40}$`, ref)
} else := sprintf("pins typos-config-builder to %q, which is not a release tag; pin one at or above %s", [ref, builder_floor]) if {
	ref := git_ref(from_spec(invocation))
	not release_version(ref)
} else := sprintf("pins typos-config-builder %s, below the floor %s", [ref, builder_floor]) if {
	ref := git_ref(from_spec(invocation))
	not version_at_least(release_version(ref), floor_version)
}

# `typos` itself as a word: a command word, an argument to `xargs` or `env`, or
# a tool a uv runner names (`uv tool run typos@1.48.0`). The builder pins and
# runs its own Typos, so a repository never runs it directly. `typos.toml` and
# `typos-config-builder` are other words; a line that only prints (`echo`,
# `printf`) is a mention, not a run.
direct_typos_word := `(^|[[:space:];&|(])(?:[^[:space:];|&()"'=]*/)?typos(?:@[^[:space:];|&]*)?([[:space:]]|$)`

runs_typos_directly(line) if {
	regex.match(direct_typos_word, line)
	not regex.match(`^[[:space:]]*[-@+]*[[:space:]]*(echo|printf)([[:space:]]|$)`, line)
	not regex.match(`^[[:space:]]*#`, line)
}

# -- PD-007: the spelling target runs the pinned gate ------------------------------

deny contains f if {
	applicable
	input.makefile == null
	f := finding(
		"PD-007", "noncompliant", makefile_path, 0,
		sprintf("root Makefile is missing; a %q target must run typos-config-builder gate", [spelling_target]),
	)
}

deny contains f if {
	applicable
	input.makefile != null
	not spelling_target_defined
	f := finding(
		"PD-007", "noncompliant", makefile_path, 0,
		sprintf("the %q target is absent; it must run typos-config-builder gate", [spelling_target]),
	)
}

deny contains f if {
	applicable
	spelling_target_defined
	not closure_provable
	f := finding("PD-007", "indeterminate", makefile_path, 0, closure_indeterminate_reason)
}

# Every builder invocation on the spelling path is judged on its own.
deny contains f if {
	applicable
	some recipe in spelling_recipes
	some invocation in builder_invocations(recipe)
	runs_gate(invocation)
	msg := pin_problem(invocation)
	f := finding("PD-007", "noncompliant", makefile_path, recipe.location.start_line, sprintf("%q-path recipe %s", [spelling_target, msg]))
}

deny contains f if {
	applicable
	some recipe in spelling_recipes
	some invocation in builder_invocations(recipe)
	not runs_gate(invocation)
	not checks_drift(invocation)
	f := finding(
		"PD-007", "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs typos-config-builder without gate; gate regenerates typos.toml, runs Typos, and checks phrases in one step", [spelling_target]),
	)
}

deny contains f if {
	applicable
	some recipe in spelling_recipes
	some invocation in builder_invocations(recipe)
	checks_drift(invocation)
	f := finding(
		"PD-007", "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe drift-checks typos.toml with --check; gate regenerates it from the live dictionary instead", [spelling_target]),
	)
}

deny contains f if {
	applicable
	some recipe in spelling_recipes
	some invocation in builder_invocations(recipe)
	runs_gate(invocation)
	not invocation in binding_invocations(recipe)
	f := finding(
		"PD-007", "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe soft-skips typos-config-builder gate; its exit status cannot fail the target", [spelling_target]),
	)
}

deny contains f if {
	applicable
	some recipe in spelling_recipes
	runs_typos_directly(expand(recipe.text))
	f := finding(
		"PD-007", "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs typos directly; gate runs the builder's pinned Typos", [spelling_target]),
	)
}

gate_on_path if {
	some recipe in spelling_recipes
	some invocation in builder_invocations(recipe)
	runs_gate(invocation)
}

unresolved_on_path := {name |
	some recipe in spelling_recipes
	some name in unresolved_references(recipe.text)
}

deny contains f if {
	applicable
	closure_provable
	not gate_on_path
	count(unresolved_on_path) > 0
	f := finding(
		"PD-007", "indeterminate", makefile_path, 0,
		sprintf("no recipe reachable from %q provably runs typos-config-builder gate; unresolved Make variables: %s", [spelling_target, concat(", ", sort(unresolved_on_path))]),
	)
}

deny contains f if {
	applicable
	closure_provable
	not gate_on_path
	count(unresolved_on_path) == 0
	f := finding(
		"PD-007", "noncompliant", makefile_path, 0,
		sprintf("no recipe reachable from %q runs typos-config-builder gate", [spelling_target]),
	)
}

# -- PD-008: no legacy pins or helper targets --------------------------------------

deny contains f if {
	applicable
	some variable in input.makefile.variables
	variable.name in legacy_variables
	f := finding(
		"PD-008", "noncompliant", makefile_path, variable.location.start_line,
		sprintf("Makefile assigns %s; the builder pins its own Typos and dependencies", [variable.name]),
	)
}

deny contains f if {
	applicable
	some rule in input.makefile.rules
	some target in rule.targets
	target in legacy_targets
	f := finding(
		"PD-008", "noncompliant", makefile_path, rule.location.start_line,
		sprintf("Makefile defines the legacy %q helper target; gate replaces it", [target]),
	)
}

# -- PD-009: no vendored spelling machinery -----------------------------------------

deny contains f if {
	applicable
	some path in input.vendored
	f := finding(
		"PD-009", "noncompliant", path, 0,
		sprintf("%s is vendored spelling machinery; typos-config-builder owns generation and phrase checks", [path]),
	)
}

# -- PD-010: CI neither runs typos directly nor drift-checks typos.toml -------------

deny contains f if {
	applicable
	some workflow in input.workflows
	workflow.error != null
	f := finding("PD-010", "indeterminate", workflow.path, 0, sprintf("%s could not be decoded: %s", [workflow.path, workflow.error]))
}

workflow_steps(workflow) := {[job_id, index, step] |
	workflow.error == null
	jobs := object.get(workflow.parsed, "jobs", {})
	is_object(jobs)
	some job_id, job in jobs
	is_object(job)
	steps := object.get(job, "steps", [])
	is_array(steps)
	some index, step in steps
	is_object(step)
}

step_label(step, index) := step.name if is_string(step.name) else := sprintf("step %d", [index + 1])

# What a `run:` script does that the gate must own instead, one reason per kind.
run_problem(script) := "runs typos directly" if {
	some line in split(script, "\n")
	runs_typos_directly(line)
} else := "drift-checks typos.toml with typos-config-builder --check" if {
	some line in split(script, "\n")
	contains(line, "typos-config-builder")
	regex.match(`(^|[[:space:]])--check([[:space:]]|$)`, line)
} else := "drift-checks typos.toml with git diff" if {
	some line in split(script, "\n")
	regex.match(`\bgit\b[^\n]*\bdiff\b[^\n]*typos\.toml`, line)
} else := "runs vendored spelling machinery" if {
	regex.match(`generate_typos_config|typos_rollout`, script)
} else := "runs a legacy spelling helper target" if {
	some target in legacy_targets
	regex.match(sprintf(`\bmake\b[^\n]*[[:space:]]%s([[:space:]]|$)`, [target]), script)
}

deny contains f if {
	applicable
	some workflow in input.workflows
	some [job_id, index, step] in workflow_steps(workflow)
	is_string(step.run)
	problem := run_problem(step.run)
	f := finding(
		"PD-010", "noncompliant", workflow.path, 0,
		sprintf("job %q %s (%s); run make %s, whose gate owns Typos and never drift-checks", [job_id, problem, step_label(step, index), spelling_target]),
	)
}

deny contains f if {
	applicable
	some workflow in input.workflows
	some [job_id, index, step] in workflow_steps(workflow)
	is_string(step.uses)
	startswith(lower(step.uses), "crate-ci/typos")
	f := finding(
		"PD-010", "noncompliant", workflow.path, 0,
		sprintf("job %q runs the crate-ci/typos action (%s); run make %s instead", [job_id, step_label(step, index), spelling_target]),
	)
}

# -- PD-011: the builder's cache stays untracked ------------------------------------

# Git lets the last matching pattern decide, so a later `!` pattern unignores
# the cache. The entries are root-level files: a pattern matches when, stripped
# of `!` and a leading `/` or `**/`, it globs the name with `/` as the only
# separator. A comment never matches, because no entry begins with `#`.
gitignore_pattern(line) := trim_prefix(trim_prefix(trim_prefix(line, "!"), "**/"), "/")

gitignore_matches(line, entry) if glob.match(gitignore_pattern(line), ["/"], entry)

gitignore_has(entry) if {
	lines := input.gitignore.lines
	matching := [index | some index, line in lines; gitignore_matches(line, entry)]
	count(matching) > 0
	not startswith(lines[matching[count(matching) - 1]], "!")
}

deny contains f if {
	applicable
	input.gitignore == null
	f := finding("PD-011", "noncompliant", ".gitignore", 0, sprintf(".gitignore is missing; it must list %s", [concat(" and ", gitignore_entries)]))
}

deny contains f if {
	applicable
	input.gitignore != null
	input.gitignore.error != null
	f := finding("PD-011", "indeterminate", ".gitignore", 0, sprintf(".gitignore could not be read: %s", [input.gitignore.error]))
}

deny contains f if {
	applicable
	input.gitignore != null
	input.gitignore.error == null
	some entry in gitignore_entries
	not gitignore_has(entry)
	f := finding("PD-011", "noncompliant", ".gitignore", 0, sprintf(".gitignore does not ignore %s, the builder's untracked cache", [entry]))
}

# -- PD-012: the local overlay exists at the supported schema ------------------------

deny contains f if {
	applicable
	input.typos_local == null
	f := finding("PD-012", "noncompliant", "typos.local.toml", 0, sprintf("typos.local.toml is missing; the gate needs a schema %d overlay, even an empty one", [overlay_schema]))
}

deny contains f if {
	applicable
	input.typos_local != null
	input.typos_local.error != null
	f := finding("PD-012", "indeterminate", "typos.local.toml", 0, sprintf("typos.local.toml could not be decoded: %s", [input.typos_local.error]))
}

deny contains f if {
	applicable
	input.typos_local != null
	input.typos_local.error == null
	object.get(input.typos_local.parsed, "schema", null) != overlay_schema
	f := finding(
		"PD-012", "noncompliant", "typos.local.toml", 0,
		sprintf("typos.local.toml declares schema %v; the builder reads schema %d", [object.get(input.typos_local.parsed, "schema", "none"), overlay_schema]),
	)
}

# -- PD-013: AGENTS.md carries the canonical spelling block --------------------------
#
# typos-config-builder publishes the block agents read, between two markers, in
# docs/agents-md-spelling.md. A repository copies it verbatim; the policy
# compares the text strictly between the markers, with whitespace normalized,
# against the text for the pinned release: the newest published text at or
# below the pin. A pin older than every published text is compared with the
# earliest one, since the block is policy text rather than builder behaviour,
# and a checkout whose pin cannot be proven is compared with the newest.

agents_start_marker := "<!-- typos-config-builder:agents-md:start -->"

agents_end_marker := "<!-- typos-config-builder:agents-md:end -->"

agents_text := input.agents_md.text if {
	input.agents_md != null
	input.agents_md.error == null
}

normalized(text) := trim_space(regex.replace(text, `\s+`, " "))

marker_offsets(marker) := indexof_n(agents_text, marker)

block_bounds := [start, end] if {
	starts := marker_offsets(agents_start_marker)
	ends := marker_offsets(agents_end_marker)
	count(starts) == 1
	count(ends) == 1
	start := starts[0] + count(agents_start_marker)
	end := ends[0]
	end >= start
}

block_body := substring(agents_text, bounds[0], bounds[1] - bounds[0]) if {
	bounds := block_bounds
}

pinned_releases := {version |
	some recipe in spelling_recipes
	some invocation in builder_invocations(recipe)
	runs_gate(invocation)
	version := release_version(git_ref(from_spec(invocation)))
}

block_versions := {key: release_version(key) | some key, _ in agents_md_blocks}

version_after(a, b) if {
	a != b
	version_at_least(a, b)
}

newest_key(keys) := key if {
	some key in keys
	not any_newer(key, keys)
}

any_newer(key, keys) if {
	some other in keys
	version_after(block_versions[other], block_versions[key])
}

oldest_key(keys) := key if {
	some key in keys
	not any_older(key, keys)
}

any_older(key, keys) if {
	some other in keys
	version_after(block_versions[key], block_versions[other])
}

published_keys := {key | some key, _ in block_versions}

expected_key := newest_key({key |
	some key in published_keys
	version_at_least(pin, block_versions[key])
}) if {
	count(pinned_releases) == 1
	some pin in pinned_releases
	some key in published_keys
	version_at_least(pin, block_versions[key])
} else := oldest_key(published_keys) if {
	count(pinned_releases) == 1
} else := newest_key(published_keys)

deny contains f if {
	applicable
	count(agents_md_blocks) == 0
	f := finding("PD-013", "indeterminate", "AGENTS.md", 0, "no canonical AGENTS.md spelling block is configured; the block cannot be compared")
}

deny contains f if {
	applicable
	count(agents_md_blocks) > 0
	input.agents_md == null
	f := finding("PD-013", "noncompliant", "AGENTS.md", 0, "AGENTS.md is missing; it must carry typos-config-builder's spelling block between its markers")
}

deny contains f if {
	applicable
	input.agents_md != null
	input.agents_md.error != null
	f := finding("PD-013", "indeterminate", "AGENTS.md", 0, sprintf("AGENTS.md could not be read: %s", [input.agents_md.error]))
}

deny contains f if {
	applicable
	count(agents_md_blocks) > 0
	agents_text
	not block_bounds
	f := finding(
		"PD-013", "noncompliant", "AGENTS.md", 0,
		sprintf("AGENTS.md does not carry exactly one spelling block between %s and %s", [agents_start_marker, agents_end_marker]),
	)
}

deny contains f if {
	applicable
	key := expected_key
	normalized(block_body) != normalized(agents_md_blocks[key])
	f := finding(
		"PD-013", "noncompliant", "AGENTS.md", 0,
		sprintf("AGENTS.md's spelling block differs from typos-config-builder %s's docs/agents-md-spelling.md; copy it verbatim", [key]),
	)
}

# Spelling guidance outside the block duplicates it and drifts from it. The
# pattern is narrow on purpose: a `make spelling` command or the generated
# `typos.toml` named outside the markers, judged only when the file carries
# exactly one well-formed block (a malformed one is already its own finding). `typos.local.toml`, where a repository
# may document its own exceptions, is a different word and is not matched.
agents_lines := split(agents_text, "\n")

block_line_range := [first, last] if {
	bounds := block_bounds
	first := count(indexof_n(substring(agents_text, 0, bounds[0]), "\n"))
	last := count(indexof_n(substring(agents_text, 0, bounds[1]), "\n"))
}

outside_block(index) if {
	range := block_line_range
	index < range[0]
}

outside_block(index) if {
	range := block_line_range
	index > range[1]
}

duplicate_guidance(line) if regex.match(`(^|[^A-Za-z0-9_.-])make[[:space:]]+spelling([^A-Za-z0-9_-]|$)`, line)

duplicate_guidance(line) if regex.match(`(^|[^A-Za-z0-9_.-])typos\.toml([^A-Za-z0-9_-]|$)`, line)

deny contains f if {
	applicable
	count(agents_md_blocks) > 0
	some index, line in agents_lines
	outside_block(index)
	duplicate_guidance(line)
	f := finding(
		"PD-013", "noncompliant", "AGENTS.md", index + 1,
		"AGENTS.md gives spelling guidance outside the canonical block; the block replaces it",
	)
}

