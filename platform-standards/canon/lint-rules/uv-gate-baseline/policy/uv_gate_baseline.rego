# uv-gate-baseline: UV-001 to UV-007.
#
# Input is a policy-input/uv-gate-baseline envelope. Makefile facts come from
# the pinned `makeutil parse` report, workflows and composite actions from a YAML
# decode, pyproject.toml from a TOML decode, and the vendored helper as its
# SHA-256. The policy reasons only over those facts, and anything it cannot
# prove is `indeterminate` (fail closed).
#
# The rule holds a repository to one way of reaching uv: through the vendored
# `scripts/uv_gate.py` helper, which cleans the environment, selects the global
# cache, goes offline first and goes online at most once. The seven checks
# mirror the helper's design: the helper is the canonical file (UV-001), nothing
# overrides its cache choice (UV-002), no recipe bypasses it (UV-003), the lock
# is committed (UV-004), no recipe refreshes, upgrades, purges or retries
# (UV-005), tools are pinned (UV-006), and Git dependencies are pinned to a
# commit (UV-007).
package canon.lint_rules.uv_gate_baseline

import rego.v1

# -- parameters --------------------------------------------------------------

default gate_variable := "UV_GATE"

gate_variable := data.parameters.gate_variable

default gate_command := "python3 scripts/uv_gate.py"

gate_command := data.parameters.gate_command

default gate_path := "scripts/uv_gate.py"

gate_path := data.parameters.gate_path

# Accepted SHA-256 digests of the canonical helper, keyed by its version. The
# Rego default is empty, so a run without them is indeterminate rather than
# compared with nothing.
default gate_digests := {}

gate_digests := data.parameters.gate_digests

default forbidden_variables := ["UV_CACHE_DIR", "UV_TOOL_DIR"]

forbidden_variables := data.parameters.forbidden_variables

# Git repositories whose tool pin is a release tag, because another rule owns
# that pin's form (spelling-config-baseline requires a release tag for
# typos-config-builder and rejects the commit form). The floor is that rule's.
default release_tag_tools := []

release_tag_tools := data.parameters.release_tag_tools

# Targets that exist to change the lock; they may run `uv lock` and upgrade.
default maintenance_targets := ["lock"]

maintenance_targets := data.parameters.maintenance_targets

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

# -- envelope ------------------------------------------------------------------

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/uv-gate-baseline"
}

deny contains f if {
	not envelope_ok
	f := finding(
		"EN-001", "indeterminate", makefile_path, 0,
		"policy input is not a policy-input/uv-gate-baseline envelope at schema version 1",
	)
}

# -- Make variable expansion -----------------------------------------------------
#
# A variable with exactly one unconditional, non-`define` assignment has one
# value the policy can substitute. Substitution runs three passes, so a value
# that names another variable (a pin built from a revision variable) resolves.

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

# Make turns `\#` in a variable's value into a literal `#`, so a pin such as
# `@<sha>\#subdirectory=x` reaches the shell as `@<sha>#subdirectory=x`. In a
# recipe line the backslash is kept and, inside quotes, reaches the shell, so
# only variable values are unescaped.
make_unescaped(text) := replace(text, `\#`, "#")

variable_value[name] := make_unescaped(joined(variable.raw_value)) if {
	some variable in input.makefile.variables
	name := variable.name
	single_valued(name)
}

replacements := {key: value |
	some name, value in variable_value
	some key in [sprintf("$(%s)", [name]), sprintf("${%s}", [name])]
}

substituted(text) := strings.replace_n(replacements, text)

expanded(text) := substituted(substituted(substituted(joined(text))))

# -- facts -----------------------------------------------------------------------

has_makefile if input.makefile != null

# Every recipe line, expanded, with where it is and the targets it belongs to.
recipe_lines := [{"text": expanded(recipe.text), "line": recipe.location.start_line, "targets": rule.targets} |
	has_makefile
	some rule in input.makefile.rules
	some recipe in rule.recipes
]

# Every string value in a workflow or composite action, with its file.
workflow_strings := [{"text": value, "path": file.path} |
	some file in array.concat(input.workflows, input.actions)
	file.error == null
	some _, value in walk(file.parsed)
	is_string(value)
]

# `uv`, `uvx`, or an unresolved `$(UV)`, as a command word.
uv_word := `(^|[^A-Za-z0-9_.$-])(uvx?|\$[({]UVX?[)}])([[:space:]]|$)`

# What a gate must route: `uv run`, `uv sync`, `uv tool run`, and `uvx`.
bypass_pattern := `(^|[^A-Za-z0-9_.$-])(uvx([[:space:]]|$)|\$[({]UVX[)}]|(uv|\$[({]UV[)}])[[:space:]]([^;&|]*[[:space:]])?(run|sync)([[:space:]]|$))`

uses_uv_in_recipes if {
	some line in recipe_lines
	regex.match(uv_word, line.text)
}

uses_uv_in_variables if {
	has_makefile
	some variable in input.makefile.variables
	regex.match(uv_word, variable.raw_value)
}

uses_uv_in_workflows if {
	some item in workflow_strings
	regex.match(uv_word, item.text)
}

uses_uv if uses_uv_in_recipes

uses_uv if uses_uv_in_variables

uses_uv if uses_uv_in_workflows

uses_uv if input.applicability.uv_lock == true

# The helper itself is evidence: a repository that routes everything through it
# has no bare uv word left to find.
uses_uv if input.applicability.gate_file == true

uses_uv if {
	some line in recipe_lines
	contains(line.text, "uv_gate.py")
}

applicable if {
	envelope_ok
	uses_uv
}

# A file that cannot be decoded might use uv, so with no other evidence the
# repository's scope is unknown: indeterminate, never passed.
undecodable_files := [file |
	some file in array.concat(input.workflows, input.actions)
	file.error != null
]

deny contains f if {
	envelope_ok
	not uses_uv
	some file in undecodable_files
	f := finding(
		"UV-003", "indeterminate", file.path, 0,
		sprintf("%s could not be decoded, so whether the repository uses uv is unknown: %s", [file.path, file.error]),
	)
}

# Once the repository is known to use uv, a file that cannot be decoded might
# still set the cache location, so UV-002 cannot be proven for it.
deny contains f if {
	applicable
	some file in undecodable_files
	f := finding(
		"UV-002", "indeterminate", file.path, 0,
		sprintf("%s could not be decoded, so it may assign a uv cache location: %s", [file.path, file.error]),
	)
}

# makeutil does not follow includes and a recovered parse may drop recipes, so
# neither can prove that a Makefile never reaches uv. They are indeterminate
# whether or not other evidence shows that the repository uses uv.
deny contains f if {
	envelope_ok
	has_makefile
	count(input.makefile.includes) > 0
	f := finding(
		"UV-003", "indeterminate", makefile_path, input.makefile.includes[0].location.start_line,
		"the Makefile includes another file, so whether the recipes reach uv cannot be seen",
	)
}

deny contains f if {
	envelope_ok
	has_makefile
	input.makefile.parse.status != "complete"
	f := finding(
		"UV-003", "indeterminate", makefile_path, 0,
		sprintf("the Makefile parse was %s, so its recipes may be incomplete", [input.makefile.parse.status]),
	)
}

# -- UV-001: the helper is the canonical file -------------------------------------

deny contains f if {
	applicable
	input.gate == null
	f := finding(
		"UV-001", "noncompliant", gate_path, 0,
		sprintf("%s is missing; vendor the canonical uv_gate.py byte for byte", [gate_path]),
	)
}

deny contains f if {
	applicable
	input.gate != null
	count(gate_digests) == 0
	f := finding(
		"UV-001", "indeterminate", input.gate.path, 0,
		"the rule carries no canonical digest to compare the helper with",
	)
}

deny contains f if {
	applicable
	input.gate != null
	count(gate_digests) > 0
	not input.gate.sha256 in {digest | some digest in gate_digests}
	f := finding(
		"UV-001", "noncompliant", input.gate.path, 0,
		sprintf("%s has digest %s, which is not a canonical uv_gate.py; copy the canonical file unedited", [input.gate.path, input.gate.sha256]),
	)
}

# -- UV-002: nothing overrides the helper's cache choice -------------------------

assignment_pattern := sprintf(`(^|[^A-Za-z0-9_])(%s)[[:space:]]*[:?+]?=`, [concat("|", forbidden_variables)])

deny contains f if {
	applicable
	has_makefile
	some variable in input.makefile.variables
	variable.name in forbidden_variables
	f := finding(
		"UV-002", "noncompliant", makefile_path, variable.location.start_line,
		sprintf("the Makefile assigns %s; the helper selects the global uv cache itself", [variable.name]),
	)
}

deny contains f if {
	applicable
	has_makefile
	some variable in input.makefile.variables
	not variable.name in forbidden_variables
	regex.match(assignment_pattern, variable.raw_value)
	f := finding(
		"UV-002", "noncompliant", makefile_path, variable.location.start_line,
		sprintf("%s sets a uv cache location; the helper selects the global uv cache itself", [variable.name]),
	)
}

deny contains f if {
	applicable
	some line in recipe_lines
	regex.match(assignment_pattern, line.text)
	f := finding(
		"UV-002", "noncompliant", makefile_path, line.line,
		"a recipe sets a uv cache location; the helper selects the global uv cache itself",
	)
}

deny contains f if {
	applicable
	some file in array.concat(input.workflows, input.actions)
	file.error == null
	walk(file.parsed, [path, _])
	count(path) > 0
	path[count(path) - 1] in forbidden_variables
	f := finding(
		"UV-002", "noncompliant", file.path, 0,
		sprintf("%s sets %s; the helper selects the global uv cache itself", [file.path, path[count(path) - 1]]),
	)
}

deny contains f if {
	applicable
	some item in workflow_strings
	regex.match(assignment_pattern, item.text)
	f := finding(
		"UV-002", "noncompliant", item.path, 0,
		sprintf("%s assigns a uv cache location in a script; the helper selects the global uv cache itself", [item.path]),
	)
}

# -- UV-003: no recipe bypasses the helper ---------------------------------------

deny contains f if {
	applicable
	some line in recipe_lines
	some command in commands(line.text)
	regex.match(bypass_pattern, command)
	not direct_release_tag_invocation(command)
	f := finding(
		"UV-003", "noncompliant", makefile_path, line.line,
		sprintf("the recipe reaches uv directly; run it through $(%s)", [gate_variable]),
	)
}

gate_assignments := assignments_of(gate_variable)

deny contains f if {
	applicable
	has_makefile
	count(gate_assignments) > 1
	f := finding(
		"UV-003", "indeterminate", makefile_path, gate_assignments[0].location.start_line,
		sprintf("%s is assigned more than once, so the command recipes run cannot be proven", [gate_variable]),
	)
}

deny contains f if {
	applicable
	has_makefile
	count(gate_assignments) == 1
	not single_valued(gate_variable)
	f := finding(
		"UV-003", "indeterminate", makefile_path, gate_assignments[0].location.start_line,
		sprintf("%s is assigned conditionally or in a define block, so the command recipes run cannot be proven", [gate_variable]),
	)
}

deny contains f if {
	applicable
	has_makefile
	count(gate_assignments) == 1
	single_valued(gate_variable)
	variable_value[gate_variable] != gate_command
	f := finding(
		"UV-003", "noncompliant", makefile_path, gate_assignments[0].location.start_line,
		sprintf("%s is %q; it must be %q", [gate_variable, variable_value[gate_variable], gate_command]),
	)
}

# -- UV-004: the lock is committed -----------------------------------------------

deny contains f if {
	applicable
	input.applicability.pyproject == true
	input.applicability.uv_lock != true
	f := finding(
		"UV-004", "noncompliant", "uv.lock", 0,
		"pyproject.toml exists but uv.lock does not; commit the lock the gates install from",
	)
}

# -- UV-005: no refresh, upgrade, purge, lock or retry in the gates --------------

maintenance_line(line) if {
	some target in line.targets
	target in maintenance_targets
}

gate_line(line) if regex.match(uv_word, line.text)

gate_line(line) if contains(line.text, "uv_gate.py")

unsafe_checks := {
	"refresh": {"pattern": `(^|[[:space:]])--refresh`, "exempt": true, "what": "refreshes the cache"},
	"upgrade": {"pattern": `(^|[[:space:]])(--upgrade|-U)([[:space:]=]|$)`, "exempt": true, "what": "upgrades dependencies"},
	"lock": {"pattern": `(^|[^A-Za-z0-9_.$-])(uv|\$[({]UV[)}])[[:space:]]+lock([[:space:]]|$)`, "exempt": true, "what": "rewrites the lock"},
	"purge": {"pattern": `(^|[^A-Za-z0-9_.$-])(uv|\$[({]UV[)}])[[:space:]]+cache[[:space:]]+(clean|prune)`, "exempt": false, "what": "purges the uv cache"},
	"retry": {"pattern": `(^|[^A-Za-z0-9_])(retry|retries|until)([^A-Za-z0-9_]|$)`, "exempt": false, "what": "retries around uv"},
}

deny contains f if {
	applicable
	some line in recipe_lines
	gate_line(line)
	some name, check in unsafe_checks
	regex.match(check.pattern, line.text)
	not check.exempt == true
	f := finding(
		"UV-005", "noncompliant", makefile_path, line.line,
		sprintf("the recipe %s (%s); the helper's bounded online step is the only recovery", [check.what, name]),
	)
}

deny contains f if {
	applicable
	some line in recipe_lines
	gate_line(line)
	not maintenance_line(line)
	some name, check in unsafe_checks
	check.exempt == true
	regex.match(check.pattern, line.text)
	f := finding(
		"UV-005", "noncompliant", makefile_path, line.line,
		sprintf("the recipe %s (%s); only a maintenance target (%s) may", [check.what, name, concat(", ", maintenance_targets)]),
	)
}

# -- UV-006: tools are pinned ----------------------------------------------------

# The specs a tool recipe names: every `--from SPEC`, and the first positional
# word after `uvx` or the helper's `tool` command when there is no `--from`.
from_pattern := `--from[ =]["']?([^"'[:space:]]+)`

uvx_positional := `(^|[^A-Za-z0-9_.$-])uvx[[:space:]]+((--python[ =][^[:space:]]+|-[^[:space:]]+)[[:space:]]+)*([^-[:space:]]["']?[^"'[:space:]]*)`

tool_run_positional := `(^|[^A-Za-z0-9_.$-])uv[[:space:]]+tool[[:space:]]+run[[:space:]]+((--python[ =][^[:space:]]+|-[^[:space:]]+)[[:space:]]+)*([^-[:space:]]["']?[^"'[:space:]]*)`

gate_positional := `uv_gate\.py[[:space:]]+tool[[:space:]]+((-[^[:space:]]+)[[:space:]]+)*([^-[:space:]]["']?[^"'[:space:]]*)`

tool_context(text) if regex.match(`(^|[^A-Za-z0-9_.$-])uvx([[:space:]]|$)`, text)

tool_context(text) if regex.match(`uv[[:space:]]+tool[[:space:]]+run`, text)

tool_context(text) if regex.match(`uv_gate\.py[[:space:]]+tool`, text)

strip_quotes(spec) := trim(spec, `"'`)

specs_in(text) := {strip_quotes(match[1]) |
	tool_context(text)
	some match in regex.find_all_string_submatch_n(from_pattern, text, -1)
}

positional_in(text) := {strip_quotes(match[count(match) - 1]) |
	not regex.match(from_pattern, text)
	some pattern in [uvx_positional, tool_run_positional, gate_positional]
	some match in regex.find_all_string_submatch_n(pattern, text, -1)
}

tool_specs(text) := specs_in(text) | positional_in(text)

exact_pin := `^[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]+\])?(==|@)[A-Za-z0-9][A-Za-z0-9._+!-]*$`

git_commit_pin := `^git\+[^@[:space:]]+@[0-9a-f]{40}(#[^[:space:]]*)?$`

pinned(spec) if {
	regex.match(exact_pin, spec)
	not endswith(spec, "latest")
}

pinned(spec) if regex.match(git_commit_pin, spec)

# A release tag is accepted only for a direct invocation: the helper itself
# refuses a Git spec that is not a full commit, so a tag routed through it could
# never run.
gated(text) if contains(text, "uv_gate.py")

release_tag_accepted(spec, text) if {
	release_tag_pinned(spec)
	not gated(text)
}

# A recipe line may chain commands, and each is judged on its own: an exempt
# command must not hide a bare `uv sync` beside it.
commands(text) := regex.split(`&&|\|\||[;|&]`, text)

# A direct `uvx --from <listed repository>@vX.Y.Z` is the shape
# spelling-config-baseline (PD-007) requires, so UV-003 does not call it a
# bypass; UV-006 still judges the pin.
direct_release_tag_invocation(text) if {
	specs := tool_specs(text)
	count(specs) > 0
	every spec in specs {
		release_tag_pinned(spec)
	}
}

release_tag_pinned(spec) if {
	some repository in release_tag_tools
	pattern := sprintf(`^git\+https://%s(\.git)?@v[0-9]+\.[0-9]+\.[0-9]+$`, [regex.replace(repository, `[.]`, `\.`)])
	regex.match(pattern, spec)
}

unresolved(spec) if regex.match(`\$[({]`, spec)

tool_sources := array.concat(
	[{"text": line.text, "path": makefile_path, "line": line.line} | some line in recipe_lines],
	[{"text": item.text, "path": item.path, "line": 0} | some item in workflow_strings],
)

deny contains f if {
	applicable
	some source in tool_sources
	some spec in tool_specs(source.text)
	unresolved(spec)
	f := finding(
		"UV-006", "indeterminate", source.path, source.line,
		sprintf("the tool spec %q names a variable that cannot be resolved to one value", [spec]),
	)
}

deny contains f if {
	applicable
	some source in tool_sources
	some spec in tool_specs(source.text)
	not unresolved(spec)
	not pinned(spec)
	not release_tag_accepted(spec, source.text)
	f := finding(
		"UV-006", "noncompliant", source.path, source.line,
		sprintf("the tool spec %q is not pinned; use name==VERSION, name@VERSION, git+URL@<full commit SHA>, or a release tag for a listed tool", [spec]),
	)
}

# -- UV-007: Git dependencies are pinned to a commit -----------------------------

deny contains f if {
	applicable
	input.pyproject != null
	input.pyproject.error != null
	f := finding(
		"UV-007", "indeterminate", input.pyproject.path, 0,
		sprintf("pyproject.toml could not be decoded, so its dependencies are unknown: %s", [input.pyproject.error]),
	)
}

deny contains f if {
	applicable
	some requirement in input.requirements
	contains(requirement.spec, "git+")
	not regex.match(`@[0-9a-f]{40}(#[^[:space:];]*)?([[:space:]]*;.*)?$`, requirement.spec)
	f := finding(
		"UV-007", "noncompliant", "pyproject.toml", 0,
		sprintf("%s requires %q, a Git dependency that is not pinned to a full commit SHA", [requirement.origin, requirement.spec]),
	)
}

source_has_commit_rev(source) if {
	is_string(source.rev)
	regex.match(`^[0-9a-f]{40}$`, source.rev)
}

deny contains f if {
	applicable
	some source in input.git_sources
	not source_has_commit_rev(source)
	f := finding(
		"UV-007", "noncompliant", "pyproject.toml", 0,
		sprintf("tool.uv.sources.%s is a Git source without a full commit rev (a tag or branch can move)", [source.name]),
	)
}
