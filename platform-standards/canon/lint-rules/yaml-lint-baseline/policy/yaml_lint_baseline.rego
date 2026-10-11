# yaml-lint-baseline: YL-001 to YL-005.
#
# Input is a policy-input/yaml-lint-baseline envelope. Makefile facts inside it
# come from the pinned `makeutil parse` report, the yamllint and actionlint
# configurations from a YAML decode, the estate base file from a digest, and
# each workflow from a YAML decode. The policy never re-parses any of those
# formats: it reasons only over the fact documents, and anything it cannot
# prove produces an `indeterminate` finding (fail closed) rather than a silent
# pass.
package canon.lint_rules.yaml_lint_baseline

import rego.v1

# -- parameters --------------------------------------------------------------

default lint_target := "lint"

lint_target := data.parameters.lint_target

# Both spellings must reach `git ls-files` as pathspecs, so a repository that
# keeps its YAML under `.yaml` is linted as fully as one that uses `.yml`.
default yaml_pathspecs := ["*.yml", "*.yaml"]

yaml_pathspecs := data.parameters.yaml_pathspecs

default base_sha256s := []

base_sha256s := data.parameters.base_sha256s

default base_filename := ".yamllint-estate.yml"

base_filename := data.parameters.base_filename

default local_filename := ".yamllint.yml"

local_filename := data.parameters.local_filename

default local_allowed_keys := ["extends", "ignore"]

local_allowed_keys := data.parameters.local_allowed_keys

default runner_labels := ["ubicloud-*"]

runner_labels := data.parameters.runner_labels

default installer_action := "leynos/shared-actions/.github/actions/install-yaml-lint"

installer_action := data.parameters.installer_action

default hand_rolled_patterns := []

hand_rolled_patterns := data.parameters.hand_rolled_patterns

default lint_actions := []

lint_actions := data.parameters.lint_actions

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

yaml_applicable if {
	envelope_ok
	input.applicability.yaml_files == true
}

workflows_applicable if {
	envelope_ok
	input.applicability.workflows == true
}

applicable if yaml_applicable

applicable if workflows_applicable

deny contains f if {
	not envelope_ok
	f := finding(
		"EN-001", "indeterminate", makefile_path, 0,
		"policy input has an unknown schema version; expected 1",
	)
}

has_makefile if {
	applicable
	input.makefile != null
}

rules_defining(target) := [rule |
	some rule in input.makefile.rules
	target in rule.targets
]

# -- Make variable expansion -------------------------------------------------

# Recipes name their tools through variables (`$(YAMLLINT) --strict`), so the
# flags a recipe passes are only visible after expansion. Makeutil records
# every assignment; a variable with exactly one unconditional, non-`define`
# assignment has one value the policy can substitute. Three substitution passes resolve a value that itself names a
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

# Make exports the process environment as variables, so `$(HOME)` in a
# recipe is defined even though no Makefile assigns it. The shell receives
# the same value through `$HOME`, so the reference is rewritten to that
# spelling: it then reads as an ordinary path component rather than as an
# unresolved variable, and a tool under `$(HOME)/.cargo/bin/` is still the
# command word.
environment_variables := {"HOME", "PATH", "PWD", "SHELL", "TMPDIR", "USER"}

environment_replacements := {reference: sprintf("$%s", [name]) |
	some name in environment_variables
	not single_valued(name)
	some reference in [sprintf("$(%s)", [name]), sprintf("${%s}", [name])]
}

replacements := object.union(
	environment_replacements,
	object.union(paren_replacements, brace_replacements),
)

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
# A mention is not an invocation: `echo "$(YAMLLINT) --strict"` prints the
# command. The policy does not parse shell, so it proves the one shape it can
# read directly: the tool as the command word at the start of a command
# segment, after Make's recipe prefixes and any POSIX environment assignments.
#
# The command word may be the literal tool name, optionally under a directory,
# or a `$(shell command -v <tool> ...)` / `$(shell which <tool> ...)` probe:
# the estate pattern `YAMLLINT ?= $(shell command -v yamllint ...)`
# expands to that probe in command position. The probe's fallback may name
# one nested Make reference such as `$(HOME)`, so one level of parentheses
# is allowed inside it before the closing parenthesis.
tool_word(tool) := sprintf(
	`%s(([^[:space:];|&()]*/)?%s|\$\(shell[[:space:]]+(command -v|which)[[:space:]]+%s([^()]|\([^()]*\))*\))`,
	[xargs_wrapper, tool, tool],
)

# `git ls-files -z ... | xargs -0 -r yamllint --strict --` is the one shape
# that feeds a Git-selected file list to a tool that has no Git mode, so xargs
# is read through as a transparent wrapper: its own exit status (123 when any
# invocation fails) is the pipeline's, and so reaches Make. Only xargs's
# short options, with an optional numeric argument, are accepted.
xargs_wrapper := `(xargs([[:space:]]+-[^[:space:]]+([[:space:]]+[0-9]+)?)*[[:space:]]+)?`

segment_start := `(^|[;&(]|[^|]\|)[[:space:]]*[-@+]*[[:space:]]*`

# Arguments run to the next separator; `&&` is the only separator after which
# a failure still fails the recipe line, so a tail containing `;`, `|`, or a
# bare `&` leaves the tool's status masked.
tool_segment_pattern(tool) := sprintf(
	`%s(%s)*%s([^;|&]*)`,
	[segment_start, env_assignment_prefix, tool_word(tool)],
)

# The same shape, with the argument list as the final capture and the `&&`
# tail uncaptured, so the arguments of the invocation whose status reaches
# Make can be read rather than inferred.
tool_binding_segment_pattern(tool) := sprintf(
	`%s(?:%s)*%s([^;|&]*)(?:&&[^;|&]*)*$`,
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
	tokens := [token |
		some token in split(trim_space(arguments), " ")
		token != ""
	]
}

tool_invoked(recipe, tool) if count(tool_arguments(recipe, tool)) > 0

# The arguments of the invocations whose exit status reaches Make. Flags and
# status must be read from the *same* invocation: a recipe such as
# `yamllint --strict . || true; yamllint --version`
# has one invocation carrying the required flags and a different one carrying
# the binding status, and judging the two independently reports it compliant
# while the check it was asked to run cannot fail the target.
binding_arguments(recipe, tool) := {tokens |
	not recipe_is_comment(recipe)
	recipe.ignore_errors == false
	some match in regex.find_all_string_submatch_n(
		tool_binding_segment_pattern(tool), expand(recipe.text), -1,
	)
	arguments := match[count(match) - 1]
	tokens := [token |
		some token in split(trim_space(arguments), " ")
		token != ""
	]
}

invocation_binds(recipe, tool, tokens) if tokens in binding_arguments(recipe, tool)

# A token such as `$(YAMLLINT_PATHS)` left in the argument list names a
# variable the policy could not resolve; the flags behind it are unproven.
unresolved_tokens(tokens) := {name |
	some token in tokens
	some match in regex.find_all_string_submatch_n(
		`\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]`, token, -1,
	)
	name := match[1]
}



# -- YL-001 / YL-002: the lint gate runs yamllint and actionlint ---------------

# Both checks name the root target whose closure they inspect, and the tool
# each must find on it.
makefile_check_root := {"YL-001": lint_target, "YL-002": lint_target}

check_tool := {"YL-001": "yamllint", "YL-002": "actionlint"}

# A tool that is told not to do its job still exits zero, so the flags are part
# of the invocation's meaning. The tokens are an ordered list, because several
# of the flags take the next word as their value.
#
# yamllint: `-d`/`--config-data` (in every spelling) replaces the estate
# configuration inline, `-c`/`--config-file` replaces it with a file other than
# the repository's own `.yamllint.yml`, and `--no-warnings` hides what
# `--strict` exists to fail on.
yamllint_flag_overrides(tokens) := {token |
	some token in tokens
	token in {"-d", "--config-data", "--no-warnings"}
}

yamllint_config_data_overrides(tokens) := {token |
	some token in tokens
	regex.match(`^(-d.+|--config-data=.*)$`, token)
}

yamllint_short_config_overrides(tokens) := {token |
	some token in tokens
	regex.match(`^-c.+$`, token)
	token != concat("", ["-c", local_filename])
}

yamllint_long_config_overrides(tokens) := {token |
	some token in tokens
	startswith(token, "--config-file=")
	token != concat("", ["--config-file=", local_filename])
}

yamllint_separate_config_overrides(tokens) := {pair |
	some index, token in tokens
	token in {"-c", "--config-file"}
	value := next_token(tokens, index)
	value != local_filename
	pair := trim_space(concat(" ", [token, value]))
}

next_token(tokens, index) := tokens[index + 1] if count(tokens) > index + 1

next_token(tokens, index) := "" if count(tokens) <= index + 1

# actionlint: an empty `-shellcheck` value, attached or separate, switches off
# the script analysis that makes actionlint read `run:` scripts.
actionlint_attached_overrides(tokens) := {token |
	some token in tokens
	regex.match(`^--?shellcheck=(""|'')?$`, token)
}

actionlint_separate_overrides(tokens) := {pair |
	some index, token in tokens
	token in {"-shellcheck", "--shellcheck"}
	value := next_token(tokens, index)
	value in {`""`, "''"}
	pair := concat(" ", [token, value])
}

yamllint_overrides(tokens) := union({
	yamllint_flag_overrides(tokens),
	yamllint_config_data_overrides(tokens),
	yamllint_short_config_overrides(tokens),
	yamllint_long_config_overrides(tokens),
	yamllint_separate_config_overrides(tokens),
})

actionlint_overrides(tokens) := union({
	actionlint_attached_overrides(tokens),
	actionlint_separate_overrides(tokens),
})

# Informational flags make a tool print and exit zero without linting anything.
informational_flags := {
	"yamllint": {"-h", "--help", "--version"},
	"actionlint": {"-h", "-help", "--help", "-version", "--version"},
}

informational(tool, tokens) if {
	some token in tokens
	token in informational_flags[tool]
}

# `git ls-files` must feed yamllint through `xargs`, with every pathspec in the
# producer's arguments, so the lint covers the tracked YAML and nothing from
# vendored or ignored trees. Co-occurrence in one recipe is not enough: the
# list could be discarded and one file linted by name.
git_fed_yamllint_pattern := `git[[:space:]]+ls-files([^;|&]*)\|[[:space:]]*xargs([[:space:]]+-[^[:space:]]+([[:space:]]+[0-9]+)?)*[[:space:]]+([^[:space:];|&()]*/)?yamllint([[:space:]]|$)`

recipe_selects_tracked_yaml(recipe) if {
	some match in regex.find_all_string_submatch_n(
		git_fed_yamllint_pattern, expand(recipe.text), -1,
	)
	producer := match[1]
	every pathspec in yaml_pathspecs {
		contains(producer, pathspec)
	}
}

strict_flag(tokens) if "--strict" in tokens

strict_flag(tokens) if "-s" in tokens

recipe_satisfies("YL-001", recipe) if {
	some tokens in binding_arguments(recipe, "yamllint")
	strict_flag(tokens)
	count(yamllint_overrides(tokens)) == 0
	not informational("yamllint", tokens)
	count(unresolved_tokens(tokens)) == 0
	recipe_selects_tracked_yaml(recipe)
}

recipe_satisfies("YL-002", recipe) if {
	some tokens in binding_arguments(recipe, "actionlint")
	count(actionlint_overrides(tokens)) == 0
	not informational("actionlint", tokens)
	count(unresolved_tokens(tokens)) == 0
}

check_satisfied(check_id) if {
	root := makefile_check_root[check_id]
	some recipe in path_recipes(root)
	recipe_satisfies(check_id, recipe)
}

deny contains f if {
	applicable
	input.makefile == null
	some check_id, _ in check_tool
	f := finding(check_id, "noncompliant", makefile_path, 0, "root Makefile is missing")
}

deny contains f if {
	has_makefile
	some check_id, root in makefile_check_root
	count(rules_defining(root)) == 0
	f := finding(
		check_id, "noncompliant", makefile_path, 0,
		sprintf("required Make target %q is absent", [root]),
	)
}

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
	some recipe in path_recipes(root)
	not recipe_satisfies(check_id, recipe)
	tool := check_tool[check_id]
	some tokens in tool_arguments(recipe, tool)
	not invocation_binds(recipe, tool, tokens)
	f := finding(
		check_id, "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe soft-skips %s; its exit status cannot fail the target", [root, tool]),
	)
}

# YL-001: a binding yamllint that lacks --strict, or overrides the config.
deny contains f if {
	closure_provable(lint_target)
	some recipe in path_recipes(lint_target)
	not recipe_satisfies("YL-001", recipe)
	some tokens in binding_arguments(recipe, "yamllint")
	count(unresolved_tokens(tokens)) == 0
	not strict_flag(tokens)
	f := finding(
		"YL-001", "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs yamllint without --strict; warnings would not fail the target", [lint_target]),
	)
}

deny contains f if {
	closure_provable(lint_target)
	some recipe in path_recipes(lint_target)
	not recipe_satisfies("YL-001", recipe)
	some tokens in binding_arguments(recipe, "yamllint")
	overrides := yamllint_overrides(tokens)
	count(overrides) > 0
	f := finding(
		"YL-001", "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs yamllint with %s, which overrides the estate configuration or hides warnings", [lint_target, concat(", ", sort(overrides))]),
	)
}

deny contains f if {
	closure_provable(lint_target)
	some recipe in path_recipes(lint_target)
	not recipe_satisfies("YL-001", recipe)
	some tokens in binding_arguments(recipe, "yamllint")
	count(unresolved_tokens(tokens)) == 0
	strict_flag(tokens)
	count(yamllint_overrides(tokens)) == 0
	not recipe_selects_tracked_yaml(recipe)
	f := finding(
		"YL-001", "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs yamllint without a `git ls-files` list covering %s; select tracked YAML only", [lint_target, concat(" and ", yaml_pathspecs)]),
	)
}

deny contains f if {
	closure_provable(lint_target)
	some recipe in path_recipes(lint_target)
	not recipe_satisfies("YL-001", recipe)
	some tokens in binding_arguments(recipe, "yamllint")
	unresolved := unresolved_tokens(tokens)
	count(unresolved) > 0
	f := finding(
		"YL-001", "indeterminate", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs yamllint with unresolved Make variables: %s; its flags cannot be proven", [lint_target, concat(", ", sort(unresolved))]),
	)
}

deny contains f if {
	closure_provable(lint_target)
	some recipe in path_recipes(lint_target)
	not recipe_satisfies("YL-002", recipe)
	some tokens in binding_arguments(recipe, "actionlint")
	unresolved := unresolved_tokens(tokens)
	count(unresolved) > 0
	f := finding(
		"YL-002", "indeterminate", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs actionlint with unresolved Make variables: %s; its flags cannot be proven", [lint_target, concat(", ", sort(unresolved))]),
	)
}

deny contains f if {
	closure_provable(lint_target)
	some recipe in path_recipes(lint_target)
	not recipe_satisfies("YL-002", recipe)
	some tokens in binding_arguments(recipe, "actionlint")
	overrides := actionlint_overrides(tokens)
	count(overrides) > 0
	f := finding(
		"YL-002", "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs actionlint with %s, which switches off the shellcheck integration", [lint_target, concat(", ", sort(overrides))]),
	)
}

deny contains f if {
	some check_id, tool in check_tool
	root := makefile_check_root[check_id]
	closure_provable(root)
	some recipe in path_recipes(root)
	not recipe_satisfies(check_id, recipe)
	some tokens in binding_arguments(recipe, tool)
	informational(tool, tokens)
	f := finding(
		check_id, "noncompliant", makefile_path, recipe.location.start_line,
		sprintf("%q-path recipe runs %s only to print information; it lints nothing", [root, tool]),
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
	unresolved := unresolved_on_path(root)
	count(unresolved) > 0
	f := finding(
		check_id, "indeterminate", makefile_path, 0,
		sprintf("no recipe reachable from %q provably runs %s; unresolved Make variables: %s", [root, tool, concat(", ", sort(unresolved))]),
	)
}

deny contains f if {
	some check_id, root in makefile_check_root
	closure_provable(root)
	not check_satisfied(check_id)
	tool := check_tool[check_id]
	not tool_invoked_on_path(root, tool)
	count(unresolved_on_path(root)) == 0
	f := finding(
		check_id, "noncompliant", makefile_path, 0,
		sprintf("no recipe reachable from %q runs %s", [root, tool]),
	)
}

# -- YL-003: the yamllint configuration ----------------------------------------

alternate_names := input.alternate_yamllint_configs

# yamllint reads `.yamllint` and `.yamllint.yaml` before `.yamllint.yml`, so
# either of them silently replaces the file the baseline governs.
deny contains f if {
	yaml_applicable
	some name in alternate_names
	f := finding(
		"YL-003", "noncompliant", name, 0,
		sprintf("%s takes precedence over or duplicates %s; remove it and keep one local configuration", [name, local_filename]),
	)
}

deny contains f if {
	yaml_applicable
	input.yamllint_base == null
	f := finding(
		"YL-003", "noncompliant", base_filename, 0,
		sprintf("%s is missing; copy the canonical estate yamllint base verbatim", [base_filename]),
	)
}

deny contains f if {
	yaml_applicable
	input.yamllint_base != null
	input.yamllint_base.error != null
	f := finding(
		"YL-003", "indeterminate", base_filename, 0,
		sprintf("%s could not be read: %s", [base_filename, input.yamllint_base.error]),
	)
}

deny contains f if {
	yaml_applicable
	input.yamllint_base != null
	input.yamllint_base.error == null
	not input.yamllint_base.sha256 in base_sha256s
	f := finding(
		"YL-003", "noncompliant", base_filename, 0,
		sprintf("%s differs from the canonical estate yamllint base; copy it verbatim", [base_filename]),
	)
}

deny contains f if {
	yaml_applicable
	input.yamllint_local == null
	f := finding(
		"YL-003", "noncompliant", local_filename, 0,
		sprintf("%s is missing; it must extend %s", [local_filename, base_filename]),
	)
}

deny contains f if {
	yaml_applicable
	input.yamllint_local != null
	input.yamllint_local.error != null
	f := finding(
		"YL-003", "indeterminate", local_filename, 0,
		sprintf("%s could not be decoded: %s", [local_filename, input.yamllint_local.error]),
	)
}

local_decoded if {
	yaml_applicable
	input.yamllint_local != null
	input.yamllint_local.error == null
}

deny contains f if {
	local_decoded
	not is_object(input.yamllint_local.parsed)
	f := finding(
		"YL-003", "noncompliant", local_filename, 0,
		sprintf("%s must be a YAML mapping that extends %s", [local_filename, base_filename]),
	)
}

local_object := input.yamllint_local.parsed if {
	local_decoded
	is_object(input.yamllint_local.parsed)
}

deny contains f if {
	local_object
	object.get(local_object, "extends", null) != base_filename
	f := finding(
		"YL-003", "noncompliant", local_filename, 0,
		sprintf("%s must set `extends: %s`", [local_filename, base_filename]),
	)
}

# The base is the estate policy. A rule, a `yaml-files` list or an
# `ignore-from-file` in the local file would fork it, so only the keys the
# manifest allows (by default `extends` and `ignore`) are accepted.
deny contains f if {
	local_object
	some key, _ in local_object
	not key in local_allowed_keys
	f := finding(
		"YL-003", "noncompliant", local_filename, 0,
		sprintf("%s sets %q; the baseline allows only %s", [local_filename, key, concat(", ", local_allowed_keys)]),
	)
}

deny contains f if {
	local_object
	ignore := object.get(local_object, "ignore", null)
	ignore != null
	not is_string(ignore)
	not is_array(ignore)
	f := finding(
		"YL-003", "noncompliant", local_filename, 0,
		sprintf("%s must give `ignore` as a block of patterns or a list", [local_filename]),
	)
}

# -- YL-004: the actionlint configuration --------------------------------------

actionlint_path := input.actionlint_config.path

deny contains f if {
	workflows_applicable
	input.actionlint_config == null
	f := finding(
		"YL-004", "noncompliant", ".github/actionlint.yaml", 0,
		".github/actionlint.yaml is missing; it must declare the estate's self-hosted runner labels",
	)
}

deny contains f if {
	workflows_applicable
	input.actionlint_config != null
	input.actionlint_config.error != null
	f := finding(
		"YL-004", "indeterminate", actionlint_path, 0,
		sprintf("%s could not be decoded: %s", [actionlint_path, input.actionlint_config.error]),
	)
}

declared_labels := labels if {
	workflows_applicable
	input.actionlint_config != null
	input.actionlint_config.error == null
	is_object(input.actionlint_config.parsed)
	runner := object.get(input.actionlint_config.parsed, "self-hosted-runner", {})
	is_object(runner)
	labels := object.get(runner, "labels", [])
	is_array(labels)
}

deny contains f if {
	workflows_applicable
	input.actionlint_config != null
	input.actionlint_config.error == null
	not declared_labels
	f := finding(
		"YL-004", "noncompliant", actionlint_path, 0,
		sprintf("%s must carry `self-hosted-runner.labels` as a list", [actionlint_path]),
	)
}

deny contains f if {
	some label in runner_labels
	declared := declared_labels
	not label in declared
	f := finding(
		"YL-004", "noncompliant", actionlint_path, 0,
		sprintf("%s must list the runner label %q, so actionlint accepts the estate's self-hosted runners", [actionlint_path, label]),
	)
}

# -- YL-005: CI installs the tools through the pinned action -------------------

workflow_jobs(workflow) := jobs if {
	workflow.error == null
	jobs := object.get(workflow.parsed, "jobs", {})
	is_object(jobs)
}

# A condition GitHub Actions never satisfies, written either as the YAML
# boolean or as an expression whose whole body is the `false` literal. Only
# the literal is judged: any other expression depends on run-time context the
# policy cannot evaluate, and is treated as a step that may run.
disabled_condition(value) if value == false

disabled_condition(value) if {
	is_string(value)
	body := trim_space(trim_suffix(trim_prefix(trim_space(value), "${{"), "}}"))
	lower(trim_space(body)) == "false"
}

condition_disabled(holder) if disabled_condition(object.get(holder, "if", null))

# Steps that CI can actually reach. A step guarded by a literally false
# condition, or sitting in a job guarded by one, never runs, so it can neither
# install the lint tools nor be the evidence that CI runs the lint gate.
workflow_enabled_steps(workflow) := {[job_id, index, step] |
	some job_id, job in workflow_jobs(workflow)
	is_object(job)
	not condition_disabled(job)
	steps := object.get(job, "steps", [])
	is_array(steps)
	some index, step in steps
	is_object(step)
	not condition_disabled(step)
}

step_label(step, index) := step.name if {
	is_string(step.name)
} else := sprintf("step %d", [index + 1])

# A job that calls a reusable workflow may run the lint gate inside it; the
# policy cannot see that far.
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


# A step that runs the lint gate: `make lint`, in command position, with any
# options or other targets, as a whole word so `make markdownlint` and
# `make lint-whitaker` are not mistaken for it. The policy does not parse
# shell, so it proves the one shape it can read directly, after erasing what
# the shell would not execute: quoted text (`echo '; make lint'` passes the
# words to echo) and comments. A dry run (`-n`, `--dry-run`, `-q`, `-t`) runs
# no recipe, and anything after the target but `&&` or a redirect (`|| true`,
# `; exit 0`, a pipe) leaves the exit status unable to fail the job.
without_quoted_text(script) := regex.replace(
	regex.replace(script, `'[^'\n]*'`, "''"),
	`"[^"\n]*"`, `""`,
)

without_comments(script) := regex.replace(script, `(?m)(^|[[:space:]])#.*$`, "${1}")

lint_command_pattern := sprintf(
	`(?m)(^|[;&(])[[:space:]]*(%s)*([^[:space:];|&()\n]*/)?make(([[:space:]]+[^[:space:];|&()\n]+)*)[[:space:]]+lint(([[:space:]]+([^[:space:];|&()\n]+|[0-9]*>&[0-9]+))*)[[:space:]]*(&&|$)`,
	[env_assignment_prefix],
)

dry_run_pattern := `(^|[[:space:]])(-[a-zA-Z]*[nqt][a-zA-Z]*|--dry-run|--just-print|--recon|--question|--touch)([[:space:]]|$)`

lint_commands(script) := {match[0] |
	some match in regex.find_all_string_submatch_n(
		lint_command_pattern,
		without_comments(without_quoted_text(script)),
		-1,
	)
	not regex.match(dry_run_pattern, match[0])
}

# `continue-on-error` turns a failing step or job into a pass.
continues_on_error(holder) if object.get(holder, "continue-on-error", false) in {true, "true"}

step_runs_lint(step) if {
	is_string(step.run)
	not continues_on_error(step)
	count(lint_commands(step.run)) > 0
}

lint_steps := {[workflow.path, job_id, index, step] |
	some workflow in input.workflows
	some [job_id, index, step] in workflow_enabled_steps(workflow)
	not continues_on_error(workflow_jobs(workflow)[job_id])
	step_runs_lint(step)
}

installer_prefix := sprintf("%s@", [installer_action])

installer_step(step) if {
	is_string(step.uses)
	startswith(step.uses, installer_prefix)
}

installer_ref(step) := substring(step.uses, count(installer_prefix), -1)

installer_pinned(step) if regex.match(`^[0-9a-f]{40}$`, installer_ref(step))

# An installer step counts when it is enabled, in the same job and earlier than
# the lint step, because the job's PATH only changes for the steps after it.
installers_before(workflow_path, job_id, index) := {[installer_index, step] |
	some workflow in input.workflows
	workflow.path == workflow_path
	some [installer_job, installer_index, step] in workflow_enabled_steps(workflow)
	installer_job == job_id
	installer_index < index
	installer_step(step)
}

deny contains f if {
	workflows_applicable
	some workflow in input.workflows
	workflow.error != null
	f := finding(
		"YL-005", "indeterminate", workflow.path, 0,
		sprintf("%s could not be decoded: %s", [workflow.path, workflow.error]),
	)
}

deny contains f if {
	workflows_applicable
	some [workflow_path, job_id, index, step] in lint_steps
	count(installers_before(workflow_path, job_id, index)) == 0
	f := finding(
		"YL-005", "noncompliant", workflow_path, 0,
		sprintf("job %q runs the lint gate (%s) without an earlier step using %s", [job_id, step_label(step, index), installer_action]),
	)
}

deny contains f if {
	workflows_applicable
	some [workflow_path, job_id, index, _] in lint_steps
	some [_, installer] in installers_before(workflow_path, job_id, index)
	not installer_pinned(installer)
	f := finding(
		"YL-005", "noncompliant", workflow_path, 0,
		sprintf("job %q pins %s to %q; pin it to a full commit SHA", [job_id, installer_action, installer_ref(installer)]),
	)
}

deny contains f if {
	workflows_applicable
	not workflow_decode_failed
	count(lint_steps) == 0
	not reusable_job_present
	f := finding(
		"YL-005", "noncompliant", ".github/workflows", 0,
		sprintf("no CI job runs `make %s`; the YAML lint gate is not enforced in CI", [lint_target]),
	)
}

# A job that calls a reusable workflow may run the lint gate inside it; the
# policy cannot see that far, so absence is not proven while such a job exists.
deny contains f if {
	workflows_applicable
	not workflow_decode_failed
	count(lint_steps) == 0
	reusable_job_present
	f := finding(
		"YL-005", "indeterminate", ".github/workflows", 0,
		sprintf("no workflow provably runs `make %s`; a job calls a reusable workflow that was not inspected", [lint_target]),
	)
}

# Hand-rolled provisioning of the tools, or a third-party lint action, forks
# the versions the installer pins; the installer is the one place they move.
deny contains f if {
	workflows_applicable
	some workflow in input.workflows
	some [job_id, index, step] in workflow_enabled_steps(workflow)
	is_string(step.run)
	some pattern in hand_rolled_patterns
	regex.match(pattern, step.run)
	f := finding(
		"YL-005", "noncompliant", workflow.path, 0,
		sprintf("job %q provisions a lint tool by hand (%s); use %s", [job_id, step_label(step, index), installer_action]),
	)
}

deny contains f if {
	workflows_applicable
	some workflow in input.workflows
	some [job_id, index, step] in workflow_enabled_steps(workflow)
	is_string(step.uses)
	some action in lint_actions
	startswith(step.uses, sprintf("%s@", [action]))
	f := finding(
		"YL-005", "noncompliant", workflow.path, 0,
		sprintf("job %q lints through %s (%s); run the Make gate with %s instead", [job_id, action, step_label(step, index), installer_action]),
	)
}
