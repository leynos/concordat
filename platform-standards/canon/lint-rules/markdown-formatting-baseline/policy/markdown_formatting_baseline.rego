# markdown-formatting-baseline: PD-002 to PD-006.
#
# Input is a policy-input/markdown-formatting-baseline envelope. Makefile facts
# inside it come from the pinned `makeutil parse` report, the markdownlint
# configuration from a JSONC decode, and each workflow from a YAML decode. The
# policy never re-parses any of those formats: it reasons only over the fact
# documents, and anything it cannot prove produces an `indeterminate` finding
# (fail closed) rather than a silent pass.
package canon.lint_rules.markdown_formatting_baseline

import rego.v1

# -- parameters --------------------------------------------------------------

default required_targets := ["fmt", "check-fmt"]

required_targets := data.parameters.required_targets

default mdtablefix_select_flags := ["--git", "--include-untracked"]

mdtablefix_select_flags := data.parameters.mdtablefix_select_flags

default markdownlint_action := "DavidAnson/markdownlint-cli2-action"

markdownlint_action := data.parameters.markdownlint_action

default markdownlint_globs := "**/*.md"

markdownlint_globs := data.parameters.markdownlint_globs

default markdownlint_config_path := ".markdownlint-cli2.jsonc"

markdownlint_config_path := data.parameters.markdownlint_config_path

default markdownlint_config := {
	"MD004": {"style": "dash"},
	"MD010": {"code_blocks": false},
	"MD013": {
		"line_length": 80,
		"code_block_line_length": 120,
		"tables": false,
		"headings": false,
	},
	"MD029": {"style": "ordered"},
}

markdownlint_config := data.parameters.markdownlint_config

default markdownlint_ignores := [
	"**/.venv/**",
	".vtcode/**",
	"**/node_modules/**",
	"**/target/**",
	".terraform/**",
	".uv-cache/**",
	"memories/**",
	"CRUSH.md",
]

markdownlint_ignores := data.parameters.markdownlint_ignores

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

# -- envelope and applicability guards ---------------------------------------

envelope_ok if input.schema_version == 1

applicable if {
	envelope_ok
	input.applicability.markdown_files == true
}

deny contains f if {
	not envelope_ok
	f := finding(
		"EN-001", "indeterminate", makefile_path, 0,
		"policy input has an unknown schema version; expected 1",
	)
}

# -- FP-003: the Makefile and its formatting targets -------------------------

has_makefile if {
	applicable
	input.makefile != null
}

deny contains f if {
	applicable
	input.makefile == null
	f := finding("FP-003", "noncompliant", makefile_path, 0, "root Makefile is missing")
}

rules_defining(target) := [rule |
	some rule in input.makefile.rules
	target in rule.targets
]

deny contains f if {
	has_makefile
	some target in required_targets
	count(rules_defining(target)) == 0
	f := finding(
		"FP-003", "noncompliant", makefile_path, 0,
		sprintf("required Make target %q is absent", [target]),
	)
}

# -- Make variable expansion -------------------------------------------------

# Recipes name their tools through variables (`$(MDTABLEFIX) --check
# $(MDTABLEFIX_SELECT)`), so the flags a recipe passes are only visible after
# expansion. Makeutil records every assignment; a variable with exactly one
# unconditional, non-`define` assignment has one value the policy can
# substitute. Three substitution passes resolve a value that itself names a
# variable; deeper nesting is left unexpanded and is not proven.
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

variable_value[name] := value if {
	some variable in input.makefile.variables
	name := variable.name
	single_valued(name)
	value := variable.raw_value
}

paren_replacements := {sprintf("$(%s)", [name]): value |
	some name, value in variable_value
}

brace_replacements := {sprintf("${%s}", [name]): value |
	some name, value in variable_value
}

replacements := object.union(paren_replacements, brace_replacements)

expand(text) := strings.replace_n(
	replacements,
	strings.replace_n(replacements, strings.replace_n(replacements, text)),
)

# A plain `$(NAME)` reference left after expansion names a variable the policy
# could not resolve: one assigned more than once, under a conditional, in a
# `define` block, or not at all in this Makefile. A function call such as
# `$(shell ...)` has a space after its name and is not a variable reference.
unresolved_references(text) := {name |
	some match in regex.find_all_string_submatch_n(
		`\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]`,
		expand(text), -1,
	)
	name := match[1]
	name != "MAKE"
}

# -- static closure from a root target ---------------------------------------
#
# Prerequisites and a complete, literal same-file `$(MAKE) target` chain are
# the only edges the fact model can prove, exactly as rust-makefile-baseline
# proves them for `lint`. A reference inside `echo`, or a recursive command
# whose status can be masked, is not an edge.

env_assignment_prefix := `[A-Za-z_][A-Za-z0-9_]*=("[^"]*"|'[^']*'|[^[:space:]]*)[[:space:]]+`

static_make_recipe_pattern := sprintf(
	`^[[:space:]]*[-@+]*[[:space:]]*(%s)*\$\(MAKE\)[[:space:]]+[A-Za-z0-9_.-]+([[:space:]]*&&[[:space:]]*(%s)*\$\(MAKE\)[[:space:]]+[A-Za-z0-9_.-]+)*[[:space:]]*$`,
	[env_assignment_prefix, env_assignment_prefix],
)

static_make_recipe_is_binding(recipe) if {
	regex.match(static_make_recipe_pattern, recipe.text)
}

static_make_segment_target(segment) := target if {
	matches := regex.find_all_string_submatch_n(
		sprintf(
			`^[[:space:]]*[-@+]*[[:space:]]*(%s)*\$\(MAKE\)[[:space:]]+([A-Za-z0-9_.-]+)[[:space:]]*$`,
			[env_assignment_prefix],
		),
		segment,
		1,
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

target_graph := {target: object.get(target_edges, target, []) |
	some target in known_targets
}

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

# The closure from `root` is trustworthy only when the fact model is complete
# and every edge in it is proven. Otherwise the check reports indeterminate
# below and no recipe-level finding is attempted.
closure_provable(root) if {
	has_makefile
	count(rules_defining(root)) > 0
	not includes_present
	not parse_recovered
	not root_definitions_ambiguous(root)
	not conditional_path(root)
	not dynamic_make_delegation(root)
	not unproven_make_delegation(root)
}

# -- tool invocations ----------------------------------------------------------
#
# A mention is not an invocation: `echo "$(MDTABLEFIX) --check"` prints the
# command. The policy does not parse shell, so it proves the one shape it can
# read directly: the tool as the command word at the start of a command
# segment, after Make's recipe prefixes and any POSIX environment assignments.
#
# The command word may be the literal tool name, optionally under a directory,
# or a `$(shell command -v <tool> ...)` / `$(shell which <tool> ...)` probe:
# the estate pattern `MDLINT ?= $(shell command -v markdownlint-cli2 ...)`
# expands to that probe in command position.
tool_word(tool) := sprintf(
	`(([^[:space:];|&()]*/)?%s|\$\(shell[[:space:]]+(command -v|which)[[:space:]]+%s[^)]*\))`,
	[tool, tool],
)

segment_start := `(^|[;&(]|[^|]\|)[[:space:]]*[-@+]*[[:space:]]*`

# Arguments run to the next separator; `&&` is the only separator after which
# a failure still fails the recipe line, so a tail containing `;`, `|`, or a
# bare `&` leaves the tool's status masked.
tool_segment_pattern(tool) := sprintf(
	`%s(%s)*%s([^;|&]*)`,
	[segment_start, env_assignment_prefix, tool_word(tool)],
)

tool_binding_pattern(tool) := sprintf(
	`%s(%s)*%s(([[:space:]][^;|&]*)?(&&[^;|&]*)*)$`,
	[segment_start, env_assignment_prefix, tool_word(tool)],
)

recipe_is_comment(recipe) if regex.match(`^[[:space:]]*[-@+]*[[:space:]]*#`, recipe.text)

# Every argument list the tool receives in the recipe, as whitespace-separated
# tokens. A recipe can invoke the tool more than once; each is judged alone.
tool_arguments(recipe, tool) := {tokens |
	not recipe_is_comment(recipe)
	some match in regex.find_all_string_submatch_n(
		tool_segment_pattern(tool), expand(recipe.text), -1,
	)
	arguments := match[count(match) - 1]
	tokens := {token |
		some token in split(trim_space(arguments), " ")
		token != ""
	}
}

tool_invoked(recipe, tool) if count(tool_arguments(recipe, tool)) > 0

tool_binding(recipe, tool) if {
	tool_invoked(recipe, tool)
	recipe.ignore_errors == false
	regex.match(tool_binding_pattern(tool), expand(recipe.text))
}

# A token such as `$(MDTABLEFIX_SELECT)` left in the argument list names a
# variable the policy could not resolve; the flags behind it are unproven.
unresolved_tokens(tokens) := {name |
	some token in tokens
	some match in regex.find_all_string_submatch_n(
		`\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]`, token, -1,
	)
	name := match[1]
}

mdtablefix_mode(recipe, mode) if {
	some tokens in tool_arguments(recipe, "mdtablefix")
	mode in tokens
}

missing_select_flags(tokens) := [flag |
	some flag in mdtablefix_select_flags
	not flag in tokens
]

mdtablefix_compliant(recipe, mode) if {
	tool_binding(recipe, "mdtablefix")
	some tokens in tool_arguments(recipe, "mdtablefix")
	mode in tokens
	count(missing_select_flags(tokens)) == 0
}

markdownlint_compliant(recipe) if {
	tool_binding(recipe, "markdownlint-cli2")
	some tokens in tool_arguments(recipe, "markdownlint-cli2")
	"--fix" in tokens
}

# -- PD-002 / PD-003 / PD-004: Makefile formatting recipes --------------------

# Each Makefile check names the root target whose closure it inspects.
makefile_check_root := {
	"PD-002": "check-fmt",
	"PD-003": "fmt",
	"PD-004": "fmt",
}

check_satisfied("PD-002") if {
	some recipe in path_recipes("check-fmt")
	mdtablefix_compliant(recipe, "--check")
}

check_satisfied("PD-003") if {
	some recipe in path_recipes("fmt")
	mdtablefix_compliant(recipe, "--in-place")
}

check_satisfied("PD-004") if {
	some recipe in path_recipes("fmt")
	markdownlint_compliant(recipe)
}

check_tool := {
	"PD-002": "mdtablefix",
	"PD-003": "mdtablefix",
	"PD-004": "markdownlint-cli2",
}

check_mode := {"PD-002": "--check", "PD-003": "--in-place"}

closure_indeterminate_reason(root) := "Makefile includes other files; the recipes cannot be proven" if {
	includes_present
} else := "Makefile parse was recovered from syntax errors; facts may be incomplete" if {
	parse_recovered
} else := sprintf("the %q target has multiple or double-colon definitions", [root]) if {
	root_definitions_ambiguous(root)
} else := sprintf("the %q target reaches a conditional Make rule; its recipes cannot be proven", [root]) if {
	conditional_path(root)
} else := sprintf("the %q target reaches dynamic recursive Make; its recipes cannot be proven", [root]) if {
	dynamic_make_delegation(root)
} else := sprintf("the %q target reaches an unproven recursive Make invocation; its recipes cannot be proven", [root]) if {
	unproven_make_delegation(root)
}

deny contains f if {
	has_makefile
	some check_id, root in makefile_check_root
	count(rules_defining(root)) > 0
	not closure_provable(root)
	f := finding(
		check_id, "indeterminate", makefile_path, 0,
		closure_indeterminate_reason(root),
	)
}

# An invocation whose status cannot fail the target: `-` prefix, `|| true`,
# a trailing pipe, or a following `;` command.
deny contains f if {
	some check_id, root in makefile_check_root
	closure_provable(root)
	not check_satisfied(check_id)
	some recipe in path_recipes(root)
	tool := check_tool[check_id]
	tool_invoked(recipe, tool)
	not tool_binding(recipe, tool)
	f := finding(
		check_id, "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe soft-skips %s; its exit status cannot fail the target", [root, tool]),
	)
}

# mdtablefix runs in the wrong mode for the target: `--in-place` under
# `check-fmt` rewrites files where a check was expected, and `--check` under
# `fmt` verifies where a rewrite was expected.
deny contains f if {
	some check_id, mode in check_mode
	root := makefile_check_root[check_id]
	closure_provable(root)
	not check_satisfied(check_id)
	some recipe in path_recipes(root)
	tool_binding(recipe, "mdtablefix")
	not mdtablefix_mode(recipe, mode)
	f := finding(
		check_id, "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs mdtablefix without %s", [root, mode]),
	)
}

deny contains f if {
	some check_id, mode in check_mode
	root := makefile_check_root[check_id]
	closure_provable(root)
	not check_satisfied(check_id)
	some recipe in path_recipes(root)
	tool_binding(recipe, "mdtablefix")
	some tokens in tool_arguments(recipe, "mdtablefix")
	mode in tokens
	count(unresolved_tokens(tokens)) == 0
	missing := missing_select_flags(tokens)
	count(missing) > 0
	f := finding(
		check_id, "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs mdtablefix %s without %s", [root, mode, concat(" ", missing)]),
	)
}

deny contains f if {
	some check_id, mode in check_mode
	root := makefile_check_root[check_id]
	closure_provable(root)
	not check_satisfied(check_id)
	some recipe in path_recipes(root)
	tool_binding(recipe, "mdtablefix")
	some tokens in tool_arguments(recipe, "mdtablefix")
	mode in tokens
	unresolved := unresolved_tokens(tokens)
	count(unresolved) > 0
	f := finding(
		check_id, "indeterminate", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs mdtablefix %s with unresolved Make variables: %s; its flags cannot be proven", [root, mode, concat(", ", sort(unresolved))]),
	)
}

deny contains f if {
	closure_provable("fmt")
	not check_satisfied("PD-004")
	some recipe in path_recipes("fmt")
	tool_binding(recipe, "markdownlint-cli2")
	some tokens in tool_arguments(recipe, "markdownlint-cli2")
	count(unresolved_tokens(tokens)) == 0
	f := finding(
		"PD-004", "noncompliant", makefile_path, recipe.location.start_line,
		"\"fmt\"-path recipe runs markdownlint-cli2 without --fix",
	)
}

deny contains f if {
	closure_provable("fmt")
	not check_satisfied("PD-004")
	some recipe in path_recipes("fmt")
	tool_binding(recipe, "markdownlint-cli2")
	some tokens in tool_arguments(recipe, "markdownlint-cli2")
	unresolved := unresolved_tokens(tokens)
	count(unresolved) > 0
	f := finding(
		"PD-004", "indeterminate", makefile_path, recipe.location.start_line,
		sprintf("\"fmt\"-path recipe runs markdownlint-cli2 with unresolved Make variables: %s; its flags cannot be proven", [concat(", ", sort(unresolved))]),
	)
}

# The wrapper hides both tools behind a script the policy cannot read; the
# baseline requires each tool to be called directly so its flags are audited.
wrapper_recipes(root) := {recipe |
	some recipe in path_recipes(root)
	not recipe_is_comment(recipe)
	contains(expand(recipe.text), "mdformat-all")
}

deny contains f if {
	some check_id in ["PD-003", "PD-004"]
	closure_provable("fmt")
	not check_satisfied(check_id)
	some recipe in wrapper_recipes("fmt")
	f := finding(
		check_id, "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("\"fmt\"-path recipe delegates to the mdformat-all wrapper; call %s directly", [check_tool[check_id]]),
	)
}

tool_invoked_on_path(root, tool) if {
	some recipe in path_recipes(root)
	tool_invoked(recipe, tool)
}

# No invocation was found, and a reachable recipe still carries a variable the
# policy could not resolve: the tool may be hidden behind it.
unresolved_on_path(root) := {name |
	some recipe in path_recipes(root)
	some name in unresolved_references(recipe.text)
}

deny contains f if {
	some check_id, root in makefile_check_root
	closure_provable(root)
	not check_satisfied(check_id)
	tool := check_tool[check_id]
	not tool_invoked_on_path(root, tool)
	not wrapper_recipe_on_path(root)
	unresolved := unresolved_on_path(root)
	count(unresolved) > 0
	f := finding(
		check_id, "indeterminate", makefile_path, 0,
		sprintf("no recipe reachable from %q provably runs %s; unresolved Make variables: %s", [root, tool, concat(", ", sort(unresolved))]),
	)
}

wrapper_recipe_on_path(root) if count(wrapper_recipes(root)) > 0

deny contains f if {
	some check_id, root in makefile_check_root
	closure_provable(root)
	not check_satisfied(check_id)
	tool := check_tool[check_id]
	not tool_invoked_on_path(root, tool)
	not wrapper_recipe_on_path(root)
	count(unresolved_on_path(root)) == 0
	f := finding(
		check_id, "noncompliant", makefile_path, 0,
		sprintf("no recipe reachable from %q runs %s", [root, tool]),
	)
}

# -- PD-005: the markdownlint configuration ------------------------------------

config_present if {
	applicable
	input.markdownlint != null
}

alternate_hint := sprintf(" (found %s instead)", [concat(", ", input.alternate_markdownlint_configs)]) if {
	count(input.alternate_markdownlint_configs) > 0
} else := ""

deny contains f if {
	applicable
	input.markdownlint == null
	f := finding(
		"PD-005", "noncompliant", markdownlint_config_path, 0,
		sprintf("%s is missing%s", [markdownlint_config_path, alternate_hint]),
	)
}

deny contains f if {
	config_present
	input.markdownlint.error != null
	f := finding(
		"PD-005", "indeterminate", markdownlint_config_path, 0,
		sprintf("%s could not be decoded: %s", [markdownlint_config_path, input.markdownlint.error]),
	)
}

config_decoded if {
	config_present
	input.markdownlint.error == null
}

deny contains f if {
	config_decoded
	not is_object(input.markdownlint.parsed)
	f := finding(
		"PD-005", "noncompliant", markdownlint_config_path, 0,
		sprintf("%s must be a JSON object", [markdownlint_config_path]),
	)
}

config_object := input.markdownlint.parsed if {
	config_decoded
	is_object(input.markdownlint.parsed)
}

config_rules := object.get(config_object, "config", null)

deny contains f if {
	config_object
	not is_object(config_rules)
	f := finding(
		"PD-005", "noncompliant", markdownlint_config_path, 0,
		sprintf("%s must carry a \"config\" object", [markdownlint_config_path]),
	)
}

deny contains f if {
	is_object(config_rules)
	some rule_name, expected in markdownlint_config
	object.get(config_rules, rule_name, null) != expected
	f := finding(
		"PD-005", "noncompliant", markdownlint_config_path, 0,
		sprintf("config.%s must be %s", [rule_name, json.marshal(expected)]),
	)
}

config_ignores := object.get(config_object, "ignores", null)

deny contains f if {
	config_object
	not is_array(config_ignores)
	f := finding(
		"PD-005", "noncompliant", markdownlint_config_path, 0,
		sprintf("%s must carry an \"ignores\" array", [markdownlint_config_path]),
	)
}

deny contains f if {
	is_array(config_ignores)
	some pattern in markdownlint_ignores
	not pattern in config_ignores
	f := finding(
		"PD-005", "noncompliant", markdownlint_config_path, 0,
		sprintf("ignores must include %q", [pattern]),
	)
}

# -- PD-006: CI lints Markdown through the pinned action ----------------------

deny contains f if {
	applicable
	some workflow in input.workflows
	workflow.error != null
	f := finding(
		"PD-006", "indeterminate", workflow.path, 0,
		sprintf("%s could not be decoded: %s", [workflow.path, workflow.error]),
	)
}

workflow_jobs(workflow) := jobs if {
	workflow.error == null
	jobs := object.get(workflow.parsed, "jobs", {})
	is_object(jobs)
}

workflow_steps(workflow) := {[job_id, index, step] |
	some job_id, job in workflow_jobs(workflow)
	is_object(job)
	steps := object.get(job, "steps", [])
	is_array(steps)
	some index, step in steps
	is_object(step)
}

# A `run:` step that installs or invokes markdownlint-cli2, or drives the
# Makefile's markdownlint target, resolves the linter at run time instead of
# through the action's pinned release.
shell_lint_pattern := `markdownlint-cli2|\bmake\b[^\n]*\bmarkdownlint\b`

step_label(step, index) := step.name if {
	is_string(step.name)
} else := sprintf("step %d", [index + 1])

deny contains f if {
	applicable
	some workflow in input.workflows
	some [job_id, index, step] in workflow_steps(workflow)
	is_string(step.run)
	regex.match(shell_lint_pattern, step.run)
	f := finding(
		"PD-006", "noncompliant", workflow.path, 0,
		sprintf("job %q lints Markdown from a shell step (%s); use %s", [job_id, step_label(step, index), markdownlint_action]),
	)
}

action_prefix := sprintf("%s@", [markdownlint_action])

action_step(step) if {
	is_string(step.uses)
	startswith(step.uses, action_prefix)
}

action_ref(step) := substring(step.uses, count(action_prefix), -1)

action_pinned(step) if regex.match(`^[0-9a-f]{40}$`, action_ref(step))

action_globs(step) := object.get(object.get(step, "with", {}), "globs", null)

deny contains f if {
	applicable
	some workflow in input.workflows
	some [job_id, index, step] in workflow_steps(workflow)
	action_step(step)
	not action_pinned(step)
	f := finding(
		"PD-006", "noncompliant", workflow.path, 0,
		sprintf("job %q pins %s to %q; pin it to a full commit SHA", [job_id, markdownlint_action, action_ref(step)]),
	)
}

deny contains f if {
	applicable
	some workflow in input.workflows
	some [job_id, index, step] in workflow_steps(workflow)
	action_step(step)
	action_globs(step) != markdownlint_globs
	f := finding(
		"PD-006", "noncompliant", workflow.path, 0,
		sprintf("job %q must pass globs: %q to %s", [job_id, markdownlint_globs, markdownlint_action]),
	)
}

# A defective action step is already reported in its own right above; the
# absence finding is reserved for checkouts with no action step at all.
action_step_present if {
	some workflow in input.workflows
	some [_, _, step] in workflow_steps(workflow)
	action_step(step)
}

# A job that calls a reusable workflow may lint Markdown inside it; the policy
# cannot see that far, so absence is not proven while such a job exists.
reusable_job_present if {
	some workflow in input.workflows
	some _, job in workflow_jobs(workflow)
	is_object(job)
	is_string(job.uses)
}

workflow_decode_failed if {
	some workflow in input.workflows
	workflow.error != null
}

deny contains f if {
	applicable
	not action_step_present
	not workflow_decode_failed
	reusable_job_present
	f := finding(
		"PD-006", "indeterminate", ".github/workflows", 0,
		sprintf("no workflow provably lints Markdown with %s; a job calls a reusable workflow that was not inspected", [markdownlint_action]),
	)
}

deny contains f if {
	applicable
	not action_step_present
	not workflow_decode_failed
	not reusable_job_present
	f := finding(
		"PD-006", "noncompliant", ".github/workflows", 0,
		sprintf("no CI workflow lints Markdown with the pinned %s (globs: %q)", [markdownlint_action, markdownlint_globs]),
	)
}
