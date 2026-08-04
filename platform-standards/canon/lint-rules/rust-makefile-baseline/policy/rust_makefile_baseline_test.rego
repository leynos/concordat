# Policy tests for the rust-makefile-baseline rule package.
#
# Fixture envelopes are supplied via `conftest verify --data fixtures/data.json`
# and appear under `data.fixtures`. Each test pins the exact finding set a
# fixture must produce, so any drift in policy semantics fails loudly.
package canon.lint_rules.rust_makefile_baseline_test

import rego.v1

import data.canon.lint_rules.rust_makefile_baseline as policy

# -- helpers ---------------------------------------------------------------

profile(findings) := {[f.rule_id, f.verdict] | some f in findings}

# -- clean fixtures --------------------------------------------------------

test_compliant_has_no_findings if {
	findings := policy.deny with input as data.fixtures.compliant
	count(findings) == 0
}

test_one_hop_delegation_is_compliant if {
	findings := policy.deny with input as data.fixtures.one_hop
	count(findings) == 0
}

# -- FP-003 ----------------------------------------------------------------

test_missing_makefile_is_fp003 if {
	findings := policy.deny with input as data.fixtures.no_makefile
	count(findings) == 1
	profile(findings) == {["FP-003", "noncompliant"]}
}

test_missing_lint_target_is_fp003_and_qg001 if {
	findings := policy.deny with input as data.fixtures.missing_target
	count(findings) == 2
	profile(findings) == {["FP-003", "noncompliant"], ["QG-001", "noncompliant"]}
	some f in findings
	f.rule_id == "FP-003"
	contains(f.msg, `"lint"`)
}

test_conditional_lint_target_is_fp003 if {
	findings := policy.deny with input as data.fixtures.conditional_lint
	count(findings) == 1
	profile(findings) == {["FP-003", "noncompliant"]}
}

# -- QG-001 noncompliant ---------------------------------------------------

# `WHITAKER ?= whitaker` is the sanctioned estate pattern (rollout
# convention: local override permitted, CI installs the real binary), so
# the gate variable's `?=` assignment is not a finding.
test_overridable_gate_is_compliant if {
	findings := policy.deny with input as data.fixtures.overridable_gate
	count(findings) == 0
}

test_soft_skip_guard_is_qg001_with_line if {
	findings := policy.deny with input as data.fixtures.soft_skip
	count(findings) == 1
	profile(findings) == {["QG-001", "noncompliant"]}
	some f in findings
	contains(f.msg, "command -v")
	f.line == 12
}

test_suppressed_recipes_are_two_qg001_findings if {
	findings := policy.deny with input as data.fixtures.suppressed
	count(findings) == 2
	profile(findings) == {["QG-001", "noncompliant"]}
}

# -- QG-001 indeterminate (fail closed) ------------------------------------

test_include_makes_qg001_indeterminate if {
	findings := policy.deny with input as data.fixtures.with_include
	count(findings) == 1
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_two_hop_delegation_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.two_hop
	count(findings) == 1
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_duplicate_lint_rules_are_indeterminate if {
	findings := policy.deny with input as data.fixtures.duplicate_lint
	count(findings) == 1
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_recovered_parse_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.recovered
	count(findings) == 1
	profile(findings) == {["QG-001", "indeterminate"]}
}

# -- applicability and envelope guards -------------------------------------

test_not_rust_is_single_applicability_finding if {
	findings := policy.deny with input as data.fixtures.not_rust
	count(findings) == 1
	profile(findings) == {["AP-001", "indeterminate"]}
}

test_unknown_schema_version_is_rejected if {
	findings := policy.deny with input as {"schema_version": 2}
	count(findings) == 1
	profile(findings) == {["EN-001", "indeterminate"]}
}

# -- bounded reachability contract -----------------------------------------
#
# QG-001 proves gate delegation within one prerequisite hop. These enumerate
# `lint` chains of increasing depth over one envelope, so the boundary between
# "provable" and "indeterminate" is pinned rather than sampled: depth 0 and 1
# are compliant, and everything deeper fails closed. `build` and `test` are
# kept in every case so FP-003 stays silent and QG-001 is the only variable.

loc := {"start_byte": 0, "end_byte": 1, "start_line": 1, "start_column": 1, "end_line": 1, "end_column": 1}

gate_recipe := {"ordinal": 0, "text": "$(WHITAKER) --all", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}

make_rule(targets, prerequisites, recipes) := {
	"ordinal": 1,
	"targets": targets,
	"prerequisites": prerequisites,
	"double_colon": false,
	"conditions": [],
	"recipes": recipes,
	"location": loc,
}

stage(i) := sprintf("stage%d", [i])

# `lint` delegates to the first stage when there is one, and otherwise runs
# the gate itself.
lint_rule(depth) := make_rule(
	["lint"],
	[stage(0) | depth > 0],
	[gate_recipe | depth == 0],
)

# Stage *i* hands on to stage *i + 1*, except the last, which runs the gate.
stage_rules(depth) := [make_rule(
	[stage(i)],
	[stage(i + 1) | i < (depth - 1)],
	[gate_recipe | i == (depth - 1)],
) |
	depth > 0
	i := numbers.range(0, depth - 1)[_]
]

chain(depth) := array.concat(
	[
		make_rule(["build"], [], [{"ordinal": 0, "text": "cargo build", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
		make_rule(["test"], [], [{"ordinal": 0, "text": "cargo test", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
		lint_rule(depth),
	],
	stage_rules(depth),
)

chain_input(depth) := object.union(
	data.fixtures.compliant,
	{"makefile": object.union(data.fixtures.compliant.makefile, {"rules": chain(depth)})},
)

test_direct_gate_invocation_is_compliant if {
	findings := policy.deny with input as chain_input(0)
	count(findings) == 0
}

test_one_hop_delegation_is_compliant if {
	findings := policy.deny with input as chain_input(1)
	count(findings) == 0
}

test_two_hop_delegation_is_indeterminate if {
	findings := policy.deny with input as chain_input(2)
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_three_hop_delegation_is_indeterminate if {
	findings := policy.deny with input as chain_input(3)
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_four_hop_delegation_is_indeterminate if {
	findings := policy.deny with input as chain_input(4)
	profile(findings) == {["QG-001", "indeterminate"]}
}

# -- gate references must be Make variable references -----------------------
#
# Only `$(WHITAKER)` and `${WHITAKER}` invoke the gate. Text that merely
# contains the name — `WHITAKER_HOME`, an `echo`, a comment, a filename —
# must not, or a repository that never runs Whitaker reads as compliant.

gate_recipe_text(text) := {"ordinal": 0, "text": text, "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}

# A `lint` rule whose recipe is *text*, over an otherwise compliant envelope.
lint_recipe_input(text) := object.union(
	data.fixtures.compliant,
	{"makefile": object.union(
		data.fixtures.compliant.makefile,
		{"rules": array.concat(
			[
				make_rule(["build"], [], [gate_recipe_text("cargo build")]),
				make_rule(["test"], [], [gate_recipe_text("cargo test")]),
			],
			[make_rule(["lint"], [], [gate_recipe_text(text)])],
		)},
	)},
)

test_bare_gate_name_does_not_invoke_the_gate if {
	not policy.gate_invoked_somewhere with input as lint_recipe_input("echo WHITAKER; $(WHITAKER_HOME)/bin/lint whitaker.log")
}

test_bare_gate_name_reports_no_invocation if {
	findings := policy.deny with input as lint_recipe_input("echo WHITAKER; $(WHITAKER_HOME)/bin/lint whitaker.log")
	profile(findings) == {["QG-001", "noncompliant"]}
	some f in findings
	contains(f.msg, "no recipe invokes")
}

test_paren_reference_invokes_the_gate if {
	policy.gate_invoked_somewhere with input as lint_recipe_input("$(WHITAKER) --all")
}

test_brace_reference_invokes_the_gate if {
	policy.gate_invoked_somewhere with input as lint_recipe_input("${WHITAKER} --all")
}

test_brace_reference_is_compliant if {
	findings := policy.deny with input as lint_recipe_input("${WHITAKER} --all")
	count(findings) == 0
}

# -- QG-001 command-position contract --------------------------------------
#
# A recipe may contain the gate reference without running it. The policy does
# not parse shell, so it proves only the shape it can read — the expansion
# standing alone as the command word — and reports anything else it cannot
# prove as indeterminate rather than passing it. Each case pins the whole
# QG-001 profile; `build` and `test` are present throughout so FP-003 stays
# silent and QG-001 is the only variable.

gate_position_input(text) := object.union(
	data.fixtures.compliant,
	{"makefile": object.union(data.fixtures.compliant.makefile, {"rules": [
		make_rule(["build"], [], [{"ordinal": 0, "text": "cargo build", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
		make_rule(["test"], [], [{"ordinal": 0, "text": "cargo test", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
		make_rule(["lint"], [], [{"ordinal": 0, "text": text, "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
	]})},
)

test_paren_command_position_is_compliant if {
	findings := policy.deny with input as gate_position_input("$(WHITAKER) --all")
	profile(findings) == set()
}

test_brace_command_position_is_compliant if {
	findings := policy.deny with input as gate_position_input("${WHITAKER} --all")
	profile(findings) == set()
}

# The estate's own recipes carry an environment prefix; it still executes.
test_assignment_prefixed_command_is_compliant if {
	findings := policy.deny with input as gate_position_input(`RUSTFLAGS="-D warnings" $(WHITAKER) --all`)
	profile(findings) == set()
}

test_echoed_gate_is_indeterminate if {
	findings := policy.deny with input as gate_position_input(`echo "$(WHITAKER)"`)
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_shell_noop_gate_is_indeterminate if {
	findings := policy.deny with input as gate_position_input(": $(WHITAKER)")
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_assigned_gate_value_is_indeterminate if {
	findings := policy.deny with input as gate_position_input("TOOL=$(WHITAKER)")
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_commented_gate_is_indeterminate if {
	findings := policy.deny with input as gate_position_input("# $(WHITAKER)")
	profile(findings) == {["QG-001", "indeterminate"]}
}

# Ambiguous: the gate is inside a string handed to another shell, so whether
# it runs depends on quoting the policy does not evaluate.
test_ambiguous_nested_shell_is_indeterminate if {
	findings := policy.deny with input as gate_position_input(`sh -c "$(WHITAKER) --all"`)
	profile(findings) == {["QG-001", "indeterminate"]}
}

# Proven absence stays noncompliant: nothing mentions the gate at all.
test_absent_gate_remains_noncompliant if {
	findings := policy.deny with input as gate_position_input("cargo clippy --all")
	profile(findings) == {["QG-001", "noncompliant"]}
}

# A command word ends at a separator as well as at whitespace, and a recipe
# line that is entirely a shell comment executes nothing at all — including
# any separator inside it.

test_semicolon_terminated_command_is_compliant if {
	findings := policy.deny with input as gate_position_input("$(WHITAKER); cargo test")
	profile(findings) == set()
}

test_and_terminated_command_is_compliant if {
	findings := policy.deny with input as gate_position_input("$(WHITAKER) && cargo test")
	profile(findings) == set()
}

test_bare_gate_command_is_compliant if {
	findings := policy.deny with input as gate_position_input("$(WHITAKER)")
	profile(findings) == set()
}

# A separator inside a comment must not stand in for a command boundary.
test_commented_separator_is_indeterminate if {
	findings := policy.deny with input as gate_position_input("# note; $(WHITAKER)")
	profile(findings) == {["QG-001", "indeterminate"]}
}

# A separator inside a quoted string is likewise not a command boundary.
test_quoted_separator_is_indeterminate if {
	findings := policy.deny with input as gate_position_input(`echo "a; $(WHITAKER)"`)
	profile(findings) == {["QG-001", "indeterminate"]}
}
