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

test_literal_recursive_make_delegation_is_compliant if {
	findings := policy.deny with input as data.fixtures.static_recursive
	count(findings) == 0
}

test_dynamic_recursive_make_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.dynamic_recursive
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_echoed_recursive_make_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.static_make_echo_decoy
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_masked_recursive_make_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.static_make_masked_decoy
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_multiple_binding_recursive_makes_are_compliant if {
	findings := policy.deny with input as data.fixtures.static_make_multiple
	count(findings) == 0
}

# A quoted environment value can mention a recursive Make target without
# executing it. Only the command-position target contributes a closure edge.
test_quoted_environment_make_target_is_not_reachable if {
	findings := policy.deny with input as data.fixtures.static_make_quoted_env
	profile(findings) == {["QG-001", "noncompliant"]}
}

# A binding recipe exposes every command-position recursive target as one
# relation, without evaluating shell-like decoys or depending on target order.
test_binding_recipe_extracts_multiple_static_targets if {
	targets := policy.static_make_targets({"text": "$(MAKE) stage-a && $(MAKE) stage-b"})
	targets == {"stage-a", "stage-b"}
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
	count(findings) == 2
	profile(findings) == {
		["FP-003", "noncompliant"],
		["QG-001", "indeterminate"],
	}
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

test_two_hop_fixture_delegation_is_compliant if {
	findings := policy.deny with input as data.fixtures.two_hop
	count(findings) == 0
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

test_declared_empty_surfaces_have_no_findings if {
	findings := policy.deny with input as data.fixtures.declared_empty
	count(findings) == 0
}

# A v0.3 envelope that carries a malformed surface field is evidence that
# cannot be evaluated. It must receive a structured finding, not an evaluator
# failure or legacy root fallback.
invalid_surfaces_envelope(surfaces) := object.union(
	data.fixtures.compliant,
	{"cargo": {"parsed": {"package": {"name": "fixture"}}, "surfaces": surfaces}},
)

test_null_surfaces_are_an_indeterminate_envelope_error if {
	findings := policy.deny with input as invalid_surfaces_envelope(null)
	profile(findings) == {["EN-001", "indeterminate"]}
}

test_scalar_surfaces_are_an_indeterminate_envelope_error if {
	findings := policy.deny with input as invalid_surfaces_envelope(1)
	profile(findings) == {["EN-001", "indeterminate"]}
}

# The v0.3 envelope field is additive within schema version 1. A stored v0.2
# evidence envelope must retain its root-Cargo applicability when replayed.
legacy_v02_envelope := object.union(
	object.remove(data.fixtures.compliant, {"applicability", "cargo"}),
	{
		"applicability": {"root_cargo_toml": true, "root_makefile": true},
		"cargo": {"parsed": {"package": {"name": "fixture"}}},
	},
)

test_v02_envelope_retains_root_surface_compatibility if {
	findings := policy.deny with input as legacy_v02_envelope
	count(findings) == 0
	surfaces := policy.cargo_surfaces with input as legacy_v02_envelope
	surfaces == [{"path": "Cargo.toml"}]
}

test_unknown_schema_version_is_rejected if {
	findings := policy.deny with input as {"schema_version": 2}
	count(findings) == 1
	profile(findings) == {["EN-001", "indeterminate"]}
}

# -- bounded reachability contract -----------------------------------------
#
# QG-001 proves the complete static closure from `lint`. These enumerate
# increasingly deep literal prerequisite chains, so the closure stays pinned
# rather than sampled. `build` and `test` are kept in every case so FP-003
# stays silent and QG-001 is the only variable.

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

input_with_rules(rules) := object.union(
	data.fixtures.compliant,
	{"makefile": object.union(data.fixtures.compliant.makefile, {"rules": rules})},
)

chain_input(depth) := input_with_rules(chain(depth))

recursive_make_recipe(targets) := object.union(
	gate_recipe,
	{
		"text": concat(" && ", [sprintf("$(MAKE) %s", [target]) | some target in targets]),
	},
)

test_direct_gate_invocation_is_compliant if {
	findings := policy.deny with input as chain_input(0)
	count(findings) == 0
}

test_one_hop_delegation_is_compliant if {
	findings := policy.deny with input as chain_input(1)
	count(findings) == 0
}

test_two_hop_delegation_is_compliant if {
	findings := policy.deny with input as chain_input(2)
	count(findings) == 0
}

test_three_hop_delegation_is_compliant if {
	findings := policy.deny with input as chain_input(3)
	count(findings) == 0
}

test_four_hop_delegation_is_compliant if {
	findings := policy.deny with input as chain_input(4)
	count(findings) == 0
}

# A literal recursive edge and an ordinary prerequisite edge share the same
# closure. The cycle must not prevent the separate gate target being reached.
test_mixed_recursive_and_prerequisite_closure_is_compliant if {
	rules := [
		make_rule(["build"], [], [{"ordinal": 0, "text": "cargo build", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
		make_rule(["test"], [], [{"ordinal": 0, "text": "cargo test", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
		make_rule(["lint"], ["prepare"], []),
		make_rule(["prepare"], [], [recursive_make_recipe(["cycle", "gate"])]),
		make_rule(["cycle"], ["prepare"], []),
		make_rule(["gate"], [], [gate_recipe]),
	]
	findings := policy.deny with input as input_with_rules(rules)
	count(findings) == 0
}

# A valid gate outside lint's closure cannot credit the lint target.
test_unreachable_gate_is_noncompliant if {
	rules := [
		make_rule(["build"], [], [{"ordinal": 0, "text": "cargo build", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
		make_rule(["test"], [], [{"ordinal": 0, "text": "cargo test", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
		make_rule(["lint"], [], [{"ordinal": 0, "text": "cargo clippy", "silent": false, "ignore_errors": false, "always_execute": false, "location": loc}]),
		make_rule(["isolated"], [], [gate_recipe]),
	]
	findings := policy.deny with input as input_with_rules(rules)
	profile(findings) == {["QG-001", "noncompliant"]}
}

test_surface_qualified_gate_is_compliant if {
	findings := policy.deny with input as data.fixtures.surface_qualified
	count(findings) == 0
}

test_surface_without_qualified_gate_is_noncompliant if {
	findings := policy.deny with input as data.fixtures.surface_unqualified
	profile(findings) == {["QG-001", "noncompliant"]}
	some f in findings
	contains(f.msg, "rust/Cargo.toml")
}

# A nested `cd` gate cannot audit a simultaneously declared root manifest.
test_nested_gate_does_not_qualify_a_root_surface if {
	findings := policy.deny with input as data.fixtures.mixed_root_nested
	profile(findings) == {["QG-001", "noncompliant"]}
	some f in findings
	contains(f.msg, "Cargo.toml")
}

# Surface context must be proved from a command-shaped recipe. The two decoys
# both execute `pwd` at the root, but a substring-only check mistakes their
# printed or assigned text for `cd rust &&`.
test_echoed_surface_context_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.surface_echo_decoy
	profile(findings) == {["QG-001", "indeterminate"]}
}

test_assigned_surface_context_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.surface_assignment_decoy
	profile(findings) == {["QG-001", "indeterminate"]}
}

# Declared manifest paths can contain regex metacharacters. Their qualification
# comparison must stay literal after the direct command shape has been proved.
manifest_surface_input(path, recipe) := object.union(
	gate_position_input(recipe),
	{
		"applicability": {
			"root_cargo_toml": false,
			"root_makefile": true,
			"rust_surfaces_declared": true,
		},
		"cargo": {
			"parsed": null,
			"surfaces": [{
				"path": path,
				"role": "workspace",
				"parsed": {"workspace": {"members": []}},
			}],
		},
	},
)

test_manifest_path_comparison_is_literal if {
	findings := policy.deny with input as manifest_surface_input(
		"rust.x/Cargo.toml",
		"$(WHITAKER) --manifest-path rustxxCargo.toml",
	)
	profile(findings) == {["QG-001", "noncompliant"]}
}

test_brace_manifest_path_is_compliant if {
	findings := policy.deny with input as manifest_surface_input(
		"rust/Cargo.toml",
		"${WHITAKER} --manifest-path rust/Cargo.toml",
	)
	profile(findings) == set()
}

# A conditional rule inside the static closure can make the gate disappear at
# execution time. Makeutil records conditional ancestry but not the condition
# outcome, so the policy must fail closed rather than credit its gate recipe.
test_conditional_stage_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.conditional_stage
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

brace_recursive_make_input := object.union(
	data.fixtures.compliant,
	{"makefile": object.union(data.fixtures.compliant.makefile, {"rules": [
		make_rule(["build"], [], [gate_recipe_text("cargo build")]),
		make_rule(["test"], [], [gate_recipe_text("cargo test")]),
		make_rule(["lint"], [], [gate_recipe_text("${MAKE} lint-rust")]),
		make_rule(["lint-rust"], [], [gate_recipe]),
	]})},
)

test_brace_recursive_make_is_indeterminate if {
	findings := policy.deny with input as brace_recursive_make_input
	profile(findings) == {["QG-001", "indeterminate"]}
}

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

# `;` discards the gate's exit status: make sees `cargo test`'s, so a failing
# gate would not fail the build.
test_semicolon_separated_command_is_indeterminate if {
	findings := policy.deny with input as gate_position_input("$(WHITAKER); cargo test")
	profile(findings) == {["QG-001", "indeterminate"]}
}

# A pipeline reports the last command's status, not the gate's.
test_piped_gate_is_indeterminate if {
	findings := policy.deny with input as gate_position_input("$(WHITAKER) | tee lint.log")
	profile(findings) == {["QG-001", "indeterminate"]}
}

# A backgrounded gate leaves the line succeeding regardless of the outcome.
test_backgrounded_gate_is_indeterminate if {
	findings := policy.deny with input as gate_position_input("$(WHITAKER) &")
	profile(findings) == {["QG-001", "indeterminate"]}
}

# `||` runs the gate only when the left side fails; when it succeeds the gate
# never runs and the line still succeeds, so the gate does not bind.
test_or_guarded_gate_is_indeterminate if {
	findings := policy.deny with input as gate_position_input("true || $(WHITAKER)")
	profile(findings) == {["QG-001", "indeterminate"]}
}

# A single pipe is different: the gate is last, so the pipeline reports its
# status.
test_gate_at_the_end_of_a_pipeline_is_compliant if {
	findings := policy.deny with input as gate_position_input("cat list | $(WHITAKER)")
	profile(findings) == set()
}

# A separator before the gate is harmless: it is still the last command, so
# its status is the line's.
test_gate_after_a_separator_is_compliant if {
	findings := policy.deny with input as gate_position_input("echo linting; $(WHITAKER)")
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
