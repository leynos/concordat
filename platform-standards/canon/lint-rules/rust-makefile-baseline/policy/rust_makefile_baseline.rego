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

applicable if {
	envelope_ok
	input.applicability.root_cargo_toml == true
}

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
	input.applicability.root_cargo_toml != true
	f := finding(
		"AP-001", "indeterminate", 0,
		"root Cargo.toml is absent; the repository is not provably a Rust project",
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

lint_prerequisites contains prerequisite if {
	some rule in lint_rules
	some prerequisite in rule.prerequisites
}

lint_path_rule(rule) if "lint" in rule.targets

lint_path_rule(rule) if {
	some target in rule.targets
	target in lint_prerequisites
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
	count(lint_rules) > 0
	not gate_reachable
	f := finding(
		"QG-001", "indeterminate", 0,
		"the lint target does not reach the gate within one prerequisite hop",
	)
}
