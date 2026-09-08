# rust-makefile-baseline: FP-003 canonical targets and QG-001 binding lint gate.
#
# Input is a policy-input/v1 envelope; Makefile facts inside it come from the
# pinned `makeutil parse` report. The policy never re-parses Make syntax: it
# reasons only over the fact arrays, and anything it cannot prove produces an
# `indeterminate` finding (fail closed) rather than a silent pass.
package canon.lint_rules.rust_makefile_baseline

import rego.v1

default gate_variable := "WHITAKER"

gate_variable := data.parameters.gate_variable

default required_targets := ["build", "test", "lint"]

required_targets := data.parameters.required_targets

default source_path := "Makefile"

source_path := input.makefile.source.path

finding(rule_id, verdict, line, msg) := {
	"rule_id": rule_id,
	"severity": "error",
	"verdict": verdict,
	"path": source_path,
	"line": line,
	"msg": msg,
}

envelope_ok if input.schema_version == 1

# `cargo.surfaces` is additive within envelope schema 1.  Retain a root
# surface for envelopes produced by v0.2.0, so replaying recorded evidence
# cannot turn an audited Rust checkout into an unmeasured clean result.
cargo_surfaces := input.cargo.surfaces if input.cargo.surfaces

cargo_surfaces := [{"path": "Cargo.toml"}] if {
	not input.cargo.surfaces
	input.applicability.root_cargo_toml == true
}

cargo_surfaces := [] if {
	not input.cargo.surfaces
	input.applicability.root_cargo_toml != true
}

applicable if {
	envelope_ok
	count(cargo_surfaces) > 0
}

rust_surfaces_declared if input.applicability.rust_surfaces_declared == true

has_makefile if {
	applicable
	input.makefile != null
}

# -- envelope and applicability guards -------------------------------------

deny contains f if {
	not envelope_ok
	f := finding(
		"EN-001", "indeterminate", 0,
		"policy input has an unknown schema version; expected 1",
	)
}

deny contains f if {
	envelope_ok
	not rust_surfaces_declared
	count(cargo_surfaces) == 0
	f := finding(
		"AP-001", "indeterminate", 0,
		"no governed Cargo.toml surface is declared and root Cargo.toml is absent",
	)
}

# -- FP-003: canonical targets ---------------------------------------------

deny contains f if {
	applicable
	input.makefile == null
	f := finding("FP-003", "noncompliant", 0, "root Makefile is missing")
}

deny contains f if {
	has_makefile
	some target in required_targets
	count(rules_defining(target)) == 0
	f := finding(
		"FP-003", "noncompliant", 0,
		sprintf("required Make target %q is absent", [target]),
	)
}

deny contains f if {
	has_makefile
	some target in required_targets
	defining := rules_defining(target)
	count(defining) > 0
	every rule in defining {
		count(rule.conditions) > 0
	}
	f := finding(
		"FP-003", "noncompliant", defining[0].location.start_line,
		sprintf("required Make target %q is defined only under conditionals", [target]),
	)
}

rules_defining(target) := [rule |
	some rule in input.makefile.rules
	target in rule.targets
]

# -- QG-001: the lint gate must be binding ---------------------------------

lint_rules := rules_defining("lint")

# Every target in the static closure from `lint` is a potential gate location.
# Prerequisites and a complete, literal same-file `$(MAKE) target` command are
# the only edges the fact model can prove. A reference inside `echo`, or a
# recursive command followed by `|| true`, does not execute a child Make with
# a status that reaches its parent. The policy deliberately accepts the narrow
# sequence of literal recursive commands joined by `&&`; other shell shapes
# are indeterminate below rather than guessed.
#
# This is a relation rather than a complete function because one recipe can
# safely invoke more than one literal child Make target in an `&&` chain.
static_make_recipe_pattern := sprintf(
	`^[[:space:]]*[-@+]*[[:space:]]*(%s)*\$\(MAKE\)[[:space:]]+[A-Za-z0-9_.-]+([[:space:]]*&&[[:space:]]*(%s)*\$\(MAKE\)[[:space:]]+[A-Za-z0-9_.-]+)*[[:space:]]*$`,
	[gate_assignment_prefix, gate_assignment_prefix],
)

static_make_recipe_is_binding(recipe) if {
	regex.match(static_make_recipe_pattern, recipe.text)
}

static_make_target(recipe, target) if {
	static_make_recipe_is_binding(recipe)
	matches := regex.find_all_string_submatch_n(
		`\$\(MAKE\)[[:space:]]+([A-Za-z0-9_.-]+)`, recipe.text, -1,
	)
	some match in matches
	matched_target := match[1]
	target == matched_target
}

recipe_mentions_make(recipe) if contains(recipe.text, "$(MAKE)")

target_edges[target] contains next if {
	some rule in input.makefile.rules
	some target in rule.targets
	some next in rule.prerequisites
}

target_edges[target] contains next if {
	some rule in input.makefile.rules
	some target in rule.targets
	some recipe in rule.recipes
	some next in known_targets
	static_make_target(recipe, next)
}

known_targets := {target |
	some rule in input.makefile.rules
	some target in rule.targets
}

target_graph := {target: object.get(target_edges, target, []) |
	some target in known_targets
}

lint_reachable_targets contains "lint"

lint_reachable_targets contains target if {
	some target in graph.reachable(target_graph, {"lint"})
}

lint_path_rule(rule) if {
	some target in lint_reachable_targets
	target in rule.targets
}

# Makeutil reports conditional ancestry but not whether a branch will execute.
# A conditional rule within the closure might contain the only gate, so no
# clean result can be inferred from its text.
conditional_lint_path if {
	some rule in input.makefile.rules
	lint_path_rule(rule)
	count(rule.conditions) > 0
}

dynamic_make_delegation if {
	some rule in input.makefile.rules
	lint_path_rule(rule)
	some recipe in rule.recipes
	regex.match(
		`(\$\(MAKE\)|\$\{MAKE\})[[:space:]]+(\$\(|-C)`,
		recipe.text,
	)
}

unproven_static_make_delegation if {
	some rule in input.makefile.rules
	lint_path_rule(rule)
	some recipe in rule.recipes
	recipe_mentions_make(recipe)
	not static_make_recipe_is_binding(recipe)
	not dynamic_make_delegation
}

# Only a Make variable reference counts as mentioning the gate. Matching the
# bare name would count `WHITAKER_HOME`, `echo WHITAKER`, a comment, or a
# filename, and a repository that merely names the gate would read as
# compliant.
gate_reference := sprintf("$(%s)", [gate_variable])

gate_brace_reference := sprintf("${%s}", [gate_variable])

recipe_mentions_gate(recipe) if contains(recipe.text, gate_reference)

recipe_mentions_gate(recipe) if contains(recipe.text, gate_brace_reference)

# A mention is not an invocation. `echo "$(WHITAKER)"` prints the path,
# `: $(WHITAKER)` expands it and discards it, `TOOL=$(WHITAKER)` only assigns
# it, and `# $(WHITAKER)` is a comment — none runs the gate, yet all four
# contain the reference. The policy does not parse shell, so rather than
# guess it proves the one shape it can read directly: the expansion standing
# alone as the command word at the start of a command segment.
#
# The gate variable is interpolated into a pattern, so it must be a plain
# identifier; anything else leaves the pattern undefined and nothing is
# proven, which is the fail-closed direction.
gate_variable_is_simple if regex.match(`^[A-Za-z_][A-Za-z0-9_]*$`, gate_variable)

# `(^|[;&|(])` is start-of-text or a segment separator, so `cd sub && $(GATE)`
# still counts while `echo "$(GATE)"`, `: $(GATE)` and `# $(GATE)` do not —
# each puts a character before the reference that cannot end a command
# segment. `[-@+]*` allows Make's recipe prefixes, which change echoing and
# error handling but not what executes.
#
# The assignment group allows POSIX environment prefixes: in
# `RUSTFLAGS="-D warnings" $(GATE) --all` the gate is still the command word.
# `TOOL=$(GATE)` is not accepted by it — there the reference is the
# assignment's *value*, with no command word after it, so the trailing
# whitespace the group requires is absent.
gate_assignment_prefix := `[A-Za-z_][A-Za-z0-9_]*=("[^"]*"|'[^']*'|[^[:space:]]*)[[:space:]]+`

# Running the gate is not enough: its exit status has to reach make, or a
# failing gate does not fail the build. Make checks the status of the whole
# recipe line, so what follows the gate decides whether it binds.
#
#   $(GATE) --all            status is the gate's                   binding
#   $(GATE) && cargo test    `&&` short-circuits on failure         binding
#   $(GATE); cargo test      status is `cargo test`'s               MASKED
#   $(GATE) | tee log        status is `tee`'s                      MASKED
#   $(GATE) &                backgrounded; the line succeeds        MASKED
#
# So the tail after the reference may contain arguments and further
# `&&`-chained commands, but no `;`, `|` or bare `&`, and it must run to the
# end of the line. A separator *before* the gate is fine — in
# `echo x; $(GATE)` the gate is still the last command, so its status binds.
#
# The tail must also start at a word boundary — whitespace, or nothing at
# all. Without that, `echo "a; $(GATE)"` would match: the `;` inside the
# string anchors the prefix and the closing quote passes as an argument.
gate_binding_tail := `(([[:space:]][^;|&]*)?(&&[^;|&]*)*)$`

# The prefix is start-of-text or a separator that genuinely precedes a
# command. A bare `|` in the class matched the *second* bar of `||`, so
# `true || $(GATE)` read as an invocation — but there the gate runs only when
# the left side fails, and when it succeeds the gate never runs and the line
# still succeeds. `[^|]\|` consumes the character before the bar instead, so
# a doubled bar cannot anchor a match. A single `|` still can:
# `cat f | $(GATE)` puts the gate last in the pipeline, so its status is the
# line's.
gate_command_pattern := sprintf(
	`(^|[;&(]|[^|]\|)[[:space:]]*[-@+]*[[:space:]]*(%s)*(\$\(%s\)|\$\{%s\})%s`,
	[gate_assignment_prefix, gate_variable, gate_variable, gate_binding_tail],
) if gate_variable_is_simple

# A recipe line whose first word is `#` is a shell comment: the whole line is
# discarded, including any separator inside it. Without this, `# note; $(GATE)`
# matched — the `;` in the comment satisfied the segment-separator class — and
# a commented-out gate read as compliant.
recipe_is_comment(recipe) if regex.match(`^[[:space:]]*[-@+]*[[:space:]]*#`, recipe.text)

recipe_invokes_gate(recipe) if {
	gate_variable_is_simple
	not recipe_is_comment(recipe)
	regex.match(gate_command_pattern, recipe.text)
}

gate_invoked_somewhere if {
	some rule in input.makefile.rules
	some recipe in rule.recipes
	recipe_invokes_gate(recipe)
}

gate_mentioned_somewhere if {
	some rule in input.makefile.rules
	some recipe in rule.recipes
	recipe_mentions_gate(recipe)
}

gate_reachable if {
	some rule in input.makefile.rules
	lint_path_rule(rule)
	some recipe in rule.recipes
	recipe_invokes_gate(recipe)
}

# A root surface runs from the parsed root Makefile by construction. Every
# nested surface needs a command-shaped working-directory or manifest-path
# boundary, so a text value such as `echo cd rust` cannot credit a root gate to
# every crate. The policy does not parse shell, so forms outside these strict
# direct commands are deliberately indeterminate below.
surface_is_root(surface) if surface.path == "Cargo.toml"

surface_qualified(recipe, surface) if surface_is_root(surface)

surface_qualified(recipe, surface) if {
	not surface_is_root(surface)
	surface_directory := trim_suffix(surface.path, "/Cargo.toml")
	gate_variable_is_simple
	matches := regex.find_all_string_submatch_n(
		sprintf(
			`^[[:space:]]*[-@+]*[[:space:]]*cd[[:space:]]+([^[:space:];|&]+)[[:space:]]+&&[[:space:]]*(\$\(%s\)|\$\{%s\})%s`,
			[gate_variable, gate_variable, gate_binding_tail],
		),
		recipe.text,
		1,
	)
	count(matches) == 1
	matches[0][1] == surface_directory
}

direct_manifest_path(recipe, manifest_path) if {
	gate_variable_is_simple
	matches := regex.find_all_string_submatch_n(
		sprintf(
			`^[[:space:]]*[-@+]*[[:space:]]*\$\(%s\)([[:space:]]+[^[:space:];|&]+)*[[:space:]]+--manifest-path[[:space:]]+([^[:space:];|&]+)[[:space:]]*$`,
			[gate_variable],
		),
		recipe.text,
		1,
	)
	count(matches) == 1
	captured_path := matches[0][2]
	manifest_path == captured_path
}

direct_manifest_path(recipe, manifest_path) if {
	gate_variable_is_simple
	matches := regex.find_all_string_submatch_n(
		sprintf(
			`^[[:space:]]*[-@+]*[[:space:]]*\$\{%s\}([[:space:]]+[^[:space:];|&]+)*[[:space:]]+--manifest-path[[:space:]]+([^[:space:];|&]+)[[:space:]]*$`,
			[gate_variable],
		),
		recipe.text,
		1,
	)
	count(matches) == 1
	captured_path := matches[0][2]
	manifest_path == captured_path
}

surface_qualified(recipe, surface) if {
	not surface_is_root(surface)
	direct_manifest_path(recipe, surface.path)
}

surface_context_candidate(recipe, surface) if {
	not surface_is_root(surface)
	surface_directory := trim_suffix(surface.path, "/Cargo.toml")
	contains(recipe.text, sprintf("cd %s", [surface_directory]))
}

surface_context_candidate(recipe, surface) if {
	not surface_is_root(surface)
	contains(recipe.text, sprintf("--manifest-path %s", [surface.path]))
}

surface_context_ambiguous(surface) if {
	some rule in input.makefile.rules
	lint_path_rule(rule)
	some recipe in rule.recipes
	recipe_invokes_gate(recipe)
	surface_context_candidate(recipe, surface)
	not surface_qualified(recipe, surface)
}

surface_gate_reachable(surface) if {
	some rule in input.makefile.rules
	lint_path_rule(rule)
	some recipe in rule.recipes
	recipe_invokes_gate(recipe)
	surface_qualified(recipe, surface)
}

includes_present if count(input.makefile.includes) > 0

parse_recovered if input.makefile.parse.status != "complete"

lint_definitions_ambiguous if count(lint_rules) > 1

lint_definitions_ambiguous if {
	some rule in lint_rules
	rule.double_colon == true
}

# Detailed gate analysis only runs when the fact model is trustworthy;
# otherwise one of the indeterminate findings below stands in for it.
gate_provable if {
	has_makefile
	not includes_present
	not parse_recovered
	not lint_definitions_ambiguous
	not conditional_lint_path
	not dynamic_make_delegation
	not unproven_static_make_delegation
}

deny contains f if {
	has_makefile
	includes_present
	f := finding(
		"QG-001", "indeterminate",
		input.makefile.includes[0].location.start_line,
		"Makefile includes other files; the lint gate cannot be proven binding",
	)
}

deny contains f if {
	has_makefile
	not includes_present
	not parse_recovered
	not lint_definitions_ambiguous
	not conditional_lint_path
	dynamic_make_delegation
	f := finding(
		"QG-001", "indeterminate", 0,
		"the lint target reaches dynamic recursive Make; gate reachability cannot be proven",
	)
}

deny contains f if {
	has_makefile
	not includes_present
	not parse_recovered
	not lint_definitions_ambiguous
	not conditional_lint_path
	not dynamic_make_delegation
	unproven_static_make_delegation
	f := finding(
		"QG-001", "indeterminate", 0,
		"the lint target reaches an unproven recursive Make invocation; gate reachability cannot be proven",
	)
}

deny contains f if {
	has_makefile
	parse_recovered
	f := finding(
		"QG-001", "indeterminate", 0,
		"Makefile parse was recovered from syntax errors; facts may be incomplete",
	)
}

deny contains f if {
	has_makefile
	not includes_present
	not parse_recovered
	lint_definitions_ambiguous
	f := finding(
		"QG-001", "indeterminate", 0,
		"the lint target has multiple or double-colon definitions",
	)
}

deny contains f if {
	has_makefile
	not includes_present
	not parse_recovered
	not lint_definitions_ambiguous
	conditional_lint_path
	f := finding(
		"QG-001", "indeterminate", 0,
		"the lint target reaches a conditional Make rule; gate execution cannot be proven",
	)
}

# The gate variable's `?=` assignment (e.g. `WHITAKER ?= whitaker`) is the
# sanctioned estate pattern — local override permitted, CI installs the
# real binary — so it is deliberately NOT a finding (doctrine decision,
# 2026-07-19; see the Parabellum ExecPlan decision log).

deny contains f if {
	gate_provable
	some rule in input.makefile.rules
	lint_path_rule(rule)
	some recipe in rule.recipes
	recipe.ignore_errors == true
	f := finding(
		"QG-001", "noncompliant", recipe.location.start_line,
		"lint-path recipe ignores errors with the \"-\" prefix",
	)
}

deny contains f if {
	gate_provable
	some rule in input.makefile.rules
	lint_path_rule(rule)
	some recipe in rule.recipes
	some pattern in ["command -v", "|| true"]
	contains(recipe.text, pattern)
	f := finding(
		"QG-001", "noncompliant", recipe.location.start_line,
		sprintf("lint-path recipe soft-skips the gate (%q)", [pattern]),
	)
}

deny contains f if {
	gate_provable
	some rule in input.makefile.rules
	lint_path_rule(rule)
	some recipe in rule.recipes
	contains(recipe.text, "which ")
	contains(recipe.text, "||")
	f := finding(
		"QG-001", "noncompliant", recipe.location.start_line,
		"lint-path recipe soft-skips the gate (\"which\" existence guard)",
	)
}

deny contains f if {
	gate_provable
	not gate_mentioned_somewhere
	f := finding(
		"QG-001", "noncompliant", 0,
		sprintf("no recipe invokes the %q lint gate", [gate_variable]),
	)
}

# Mentioned, but not in a position this policy can prove executes it.
deny contains f if {
	gate_provable
	gate_mentioned_somewhere
	not gate_invoked_somewhere
	f := finding(
		"QG-001", "indeterminate", 0,
		sprintf(
			"a recipe mentions %q but not as a command; its execution cannot be proven",
			[gate_variable],
		),
	)
}

deny contains f if {
	gate_provable
	gate_invoked_somewhere
	not gate_reachable
	f := finding(
		"QG-001", "noncompliant", 0,
		"the lint target does not reach the gate through its static closure",
	)
}

deny contains f if {
	gate_provable
	gate_reachable
	some surface in cargo_surfaces
	not surface_context_ambiguous(surface)
	not surface_gate_reachable(surface)
	f := finding(
		"QG-001", "noncompliant", 0,
		sprintf("the lint target does not reach a gate qualified for %q", [surface.path]),
	)
}

deny contains f if {
	gate_provable
	gate_reachable
	some surface in cargo_surfaces
	surface_context_ambiguous(surface)
	f := finding(
		"QG-001", "indeterminate", 0,
		sprintf(
			"the lint target reaches an ambiguous gate context for %q",
			[surface.path],
		),
	)
}
