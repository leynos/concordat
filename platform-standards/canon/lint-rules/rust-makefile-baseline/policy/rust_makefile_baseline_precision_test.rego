# Precision tests for QG-001's soft-skip and command-position readings.
#
# Each reading is pinned in both directions: the shape that used to be
# misread now passes, and the real defect it resembles is still reported.
# The shapes come from netsuke's Makefile, where all three produced false
# findings.
package canon.lint_rules.rust_makefile_baseline_test

import rego.v1

import data.canon.lint_rules.rust_makefile_baseline as policy

recipe_at(text, line) := {
	"ordinal": 0,
	"text": text,
	"silent": false,
	"ignore_errors": false,
	"always_execute": false,
	"location": object.union(loc, {"start_line": line}),
}

variable_fact(name, operator, raw_value) := {
	"ordinal": 0,
	"name": name,
	"operator": operator,
	"raw_value": raw_value,
	"exported": false,
	"overridden": false,
	"define_block": false,
	"conditions": [],
	"location": loc,
}

# `lint` runs the gate on line 1 and then every recipe in `extra`, and the
# Makefile defines `WHITAKER` plus any `variables` given.
lint_input(lint_recipes, variables) := object.union(
	data.fixtures.compliant,
	{"makefile": object.union(data.fixtures.compliant.makefile, {
		"rules": [
			make_rule(["build"], [], [recipe_at("cargo build", 1)]),
			make_rule(["test"], [], [recipe_at("cargo test", 1)]),
			make_rule(["lint"], [], lint_recipes),
		],
		"variables": array.concat(
			[variable_fact("WHITAKER", "?=", "whitaker")],
			variables,
		),
	})},
)

guarded_lint(guard) := lint_input(
	[recipe_at("$(WHITAKER) --all", 1), recipe_at(guard, 7)],
	[],
)

# netsuke's actionlint guard, as makeutil reports its recipe text.
hard_failing_guard := concat("", [
	"@command -v \"$$ACTIONLINT\" >/dev/null 2>&1 || { \\\n",
	"\tprintf '%s\\n' \\\n",
	"\t\t\"actionlint could not be run: ACTIONLINT is '$$ACTIONLINT'.\" \\\n",
	"\t\t\"Install it with go (go install github.com/rhysd/actionlint/cmd/actionlint@latest),\" \\\n",
	"\t\t\"which writes $$GO_BIN/actionlint, or set ACTIONLINT=/path/to/actionlint.\" >&2; \\\n",
	"\texit 1; \\\n",
	"}",
])

# -- command -v ------------------------------------------------------------

test_a_hard_failing_command_probe_is_not_a_soft_skip if {
	findings := policy.deny with input as guarded_lint(hard_failing_guard)
	profile(findings) == set()
}

test_a_probe_exiting_one_directly_is_not_a_soft_skip if {
	findings := policy.deny with input as guarded_lint("command -v actionlint >/dev/null || exit 1")
	profile(findings) == set()
}

test_a_probe_exiting_zero_is_still_a_soft_skip if {
	findings := policy.deny with input as guarded_lint("command -v actionlint >/dev/null || exit 0")
	some f in findings
	f.rule_id == "QG-001"
	contains(f.msg, "command -v")
	f.line == 7
}

test_a_probe_guarding_the_tool_is_still_a_soft_skip if {
	findings := policy.deny with input as guarded_lint("command -v actionlint && actionlint")
	some f in findings
	contains(f.msg, "command -v")
}

test_a_block_that_exits_zero_is_still_a_soft_skip if {
	findings := policy.deny with input as guarded_lint("command -v actionlint || { echo skipped; exit 0; }")
	some f in findings
	contains(f.msg, "command -v")
}

# Every probe must fail hard; one soft probe beside a hard one still skips.
test_one_soft_probe_among_hard_ones_is_still_a_soft_skip if {
	guard := "command -v a || exit 1; command -v b || exit 0"
	findings := policy.deny with input as guarded_lint(guard)
	some f in findings
	contains(f.msg, "command -v")
}

# -- which -----------------------------------------------------------------

test_which_inside_quoted_prose_is_not_a_guard if {
	findings := policy.deny with input as guarded_lint(hard_failing_guard)
	every f in findings {
		not contains(f.msg, "which")
	}
}

test_a_leading_which_guard_is_still_reported if {
	findings := policy.deny with input as guarded_lint("which actionlint >/dev/null || true")
	some f in findings
	contains(f.msg, "\"which\" existence guard")
}

test_a_which_guard_after_a_separator_is_still_reported if {
	findings := policy.deny with input as guarded_lint("cd tools && which actionlint || exit 0")
	some f in findings
	contains(f.msg, "\"which\" existence guard")
}

test_a_which_guard_in_an_if_is_still_reported if {
	findings := policy.deny with input as guarded_lint("if which actionlint; then actionlint; fi || :")
	some f in findings
	contains(f.msg, "\"which\" existence guard")
}

# -- environment prefixes held in Make variables ---------------------------

gate_rustflags := variable_fact("GATE_RUSTFLAGS", "=", `RUSTFLAGS="-D warnings $(STANDARD_RUSTFLAGS)"`)

test_a_variable_of_env_assignments_is_seen_through if {
	lint := [recipe_at(`DYLINT_TOML="$$(cat dylint.toml)" $(GATE_RUSTFLAGS) $(WHITAKER) --all`, 1)]
	findings := policy.deny with input as lint_input(lint, [gate_rustflags])
	profile(findings) == set()
}

test_the_brace_form_is_seen_through_too if {
	lint := [recipe_at(`${GATE_RUSTFLAGS} $(WHITAKER) --all`, 1)]
	findings := policy.deny with input as lint_input(lint, [gate_rustflags])
	profile(findings) == set()
}

# An export directive naming the variable assigns nothing and does not
# disqualify it.
test_an_export_directive_does_not_disqualify_the_variable if {
	lint := [recipe_at(`$(GATE_RUSTFLAGS) $(WHITAKER) --all`, 1)]
	directive := object.union(variable_fact("GATE_RUSTFLAGS", "", ""), {"exported": true})
	findings := policy.deny with input as lint_input(lint, [gate_rustflags, directive])
	profile(findings) == set()
}

test_a_variable_holding_a_command_hides_the_gate if {
	lint := [recipe_at(`$(ECHO) $(WHITAKER) --all`, 1)]
	findings := policy.deny with input as lint_input(lint, [variable_fact("ECHO", "=", "echo")])
	profile(findings) == {["QG-001", "indeterminate"]}
}

# One definition that is not an assignment run is enough to hide the gate,
# since the policy cannot tell which definition Make will use.
test_a_variable_with_a_command_definition_hides_the_gate if {
	lint := [recipe_at(`$(GATE_RUSTFLAGS) $(WHITAKER) --all`, 1)]
	other := variable_fact("GATE_RUSTFLAGS", "=", "echo")
	findings := policy.deny with input as lint_input(lint, [gate_rustflags, other])
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_an_undefined_prefix_variable_hides_the_gate if {
	lint := [recipe_at(`$(UNDEFINED) $(WHITAKER) --all`, 1)]
	findings := policy.deny with input as lint_input(lint, [])
	profile(findings) == {["QG-001", "indeterminate"]}
}

# A variable named only by an export directive has no known value, so it is
# not seen through either.
test_a_variable_known_only_from_an_export_directive_hides_the_gate if {
	lint := [recipe_at(`$(GATE_RUSTFLAGS) $(WHITAKER) --all`, 1)]
	directive := object.union(variable_fact("GATE_RUSTFLAGS", "", ""), {"exported": true})
	findings := policy.deny with input as lint_input(lint, [directive])
	profile(findings) == {["QG-001", "indeterminate"]}
}

# -- review round: holes in the three readings ------------------------------

prefixed_lint(value) := lint_input(
	[recipe_at(`$(GATE_ENV) $(WHITAKER) --all`, 1)],
	[variable_fact("GATE_ENV", "=", value)],
)

# `X=1||true` expands to a command that runs `true` when the assignment
# succeeds, and `X=1;true` to one that runs `true` with the gate as an
# argument. Neither prefix may be seen through.
test_an_or_operator_in_a_prefix_value_hides_the_gate if {
	findings := policy.deny with input as prefixed_lint("X=1||true")
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_a_semicolon_in_a_prefix_value_hides_the_gate if {
	findings := policy.deny with input as prefixed_lint("RUSTFLAGS=x;true")
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_a_subshell_in_a_prefix_value_hides_the_gate if {
	findings := policy.deny with input as prefixed_lint("X=$(true)")
	profile(findings) == {["QG-001", "indeterminate"]}
}

# Inside quotes the same characters are part of the value.
test_operators_inside_a_quoted_prefix_value_are_seen_through if {
	findings := policy.deny with input as prefixed_lint(`RUSTFLAGS="-D warnings; -C x|y"`)
	profile(findings) == set()
}

# An earlier successful exit ends the recipe before the non-zero one.
test_a_block_with_an_earlier_zero_exit_is_a_soft_skip if {
	findings := policy.deny with input as guarded_lint("command -v tool || { exit 0; exit 1; }")
	some f in findings
	contains(f.msg, "command -v")
}

test_a_block_with_an_earlier_exit_of_any_status_is_a_soft_skip if {
	findings := policy.deny with input as guarded_lint("command -v tool || { test -n x && exit 0; exit 1; }")
	some f in findings
	contains(f.msg, "command -v")
}

# "exit" inside quoted prose is not an exit.
test_exit_inside_quoted_prose_does_not_spoil_a_hard_failure if {
	guard := "command -v tool || { printf 'will exit now\\n' >&2; exit 1; }"
	findings := policy.deny with input as guarded_lint(guard)
	profile(findings) == set()
}

# A separator inside quoted prose is not a command boundary.
test_a_separator_inside_quoted_prose_does_not_make_a_which_guard if {
	findings := policy.deny with input as guarded_lint("printf 'note; which tool' || exit 1")
	every f in findings {
		not contains(f.msg, "which")
	}
}

test_a_which_guard_in_a_brace_group_is_still_reported if {
	lint := [recipe_at("{ which actionlint >/dev/null || exit 0; } && $(WHITAKER) --all", 1)]
	findings := policy.deny with input as lint_input(lint, [])
	some f in findings
	contains(f.msg, "\"which\" existence guard")
}

test_a_which_guard_after_then_is_still_reported if {
	findings := policy.deny with input as guarded_lint("if true; then which actionlint || exit 0; fi")
	some f in findings
	contains(f.msg, "\"which\" existence guard")
}

test_a_which_guard_after_else_is_still_reported if {
	findings := policy.deny with input as guarded_lint("if false; then :; else which actionlint || exit 0; fi")
	some f in findings
	contains(f.msg, "\"which\" existence guard")
}

test_a_which_guard_after_do_is_still_reported if {
	findings := policy.deny with input as guarded_lint("for t in a; do which $$t || exit 0; done")
	some f in findings
	contains(f.msg, "\"which\" existence guard")
}

# Nested surfaces qualify through the same prefix grammar as the root.
nested_input(path, recipe, variables) := object.union(
	manifest_surface_input(path, recipe),
	{"makefile": object.union(
		manifest_surface_input(path, recipe).makefile,
		{"variables": array.concat([variable_fact("WHITAKER", "?=", "whitaker")], variables)},
	)},
)

test_a_prefixed_gate_after_cd_qualifies_a_nested_surface if {
	input_doc := nested_input("rust/Cargo.toml", `cd rust && $(GATE_RUSTFLAGS) $(WHITAKER) --all`, [gate_rustflags])
	findings := policy.deny with input as input_doc
	profile(findings) == set()
}

test_a_prefixed_gate_with_manifest_path_qualifies_a_nested_surface if {
	input_doc := nested_input("rust/Cargo.toml", `$(GATE_RUSTFLAGS) $(WHITAKER) --manifest-path rust/Cargo.toml`, [gate_rustflags])
	findings := policy.deny with input as input_doc
	profile(findings) == set()
}

test_a_command_prefix_after_cd_does_not_qualify_a_nested_surface if {
	input_doc := nested_input("rust/Cargo.toml", `cd rust && $(ECHO) $(WHITAKER) --all`, [variable_fact("ECHO", "=", "echo")])
	findings := policy.deny with input as input_doc
	profile(findings) != set()
}

test_a_prefixed_manifest_path_for_another_surface_does_not_qualify if {
	input_doc := nested_input("rust/Cargo.toml", `$(GATE_RUSTFLAGS) $(WHITAKER) --manifest-path other/Cargo.toml`, [gate_rustflags])
	findings := policy.deny with input as input_doc
	profile(findings) != set()
}

# An unquoted `which` that is an argument, not a command word, is not a guard.
test_which_as_an_unquoted_argument_is_not_a_guard if {
	findings := policy.deny with input as guarded_lint("echo which tool || exit 1")
	every f in findings {
		not contains(f.msg, "which")
	}
}
