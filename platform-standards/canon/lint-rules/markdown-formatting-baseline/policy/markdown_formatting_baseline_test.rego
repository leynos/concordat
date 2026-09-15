# Policy tests for the markdown-formatting-baseline rule package.
#
# Fixture envelopes are supplied via `conftest verify --data fixtures/data.json`
# and appear under `data.fixtures`. Each test pins the exact finding profile a
# fixture must produce, so any drift in policy semantics fails loudly.
package canon.lint_rules.markdown_formatting_baseline_test

import rego.v1

import data.canon.lint_rules.markdown_formatting_baseline as policy

# -- helpers ---------------------------------------------------------------

profile(findings) := {[f.rule_id, f.verdict] | some f in findings}

messages(findings, rule_id) := {f.msg |
	some f in findings
	f.rule_id == rule_id
}

# -- clean fixtures --------------------------------------------------------

test_compliant_has_no_findings if {
	findings := policy.deny with input as data.fixtures.compliant
	count(findings) == 0
}

test_literal_tool_names_are_compliant if {
	findings := policy.deny with input as data.fixtures.literal_tools
	count(findings) == 0
}

test_prerequisite_and_recursive_delegation_is_compliant if {
	findings := policy.deny with input as data.fixtures.delegated
	count(findings) == 0
}

# A `$(shell command -v ...)` probe whose fallback names `$(HOME)` still
# reads as the tool in command position.
test_nested_shell_probe_is_compliant if {
	findings := policy.deny with input as data.fixtures.probe_nested
	count(findings) == 0
}

# `$(HOME)` comes from the environment, not the Makefile; a tool beneath it
# is still the command word.
test_environment_variable_path_prefix_is_compliant if {
	findings := policy.deny with input as data.fixtures.home_prefixed
	count(findings) == 0
}

test_repository_additions_to_the_baseline_config_are_compliant if {
	findings := policy.deny with input as data.fixtures.config_extended
	count(findings) == 0
}

test_checkout_without_markdown_is_not_applicable if {
	findings := policy.deny with input as data.fixtures.no_markdown
	count(findings) == 0
}

# -- envelope guard --------------------------------------------------------

test_unknown_schema_version_is_en001 if {
	envelope := object.union(data.fixtures.compliant, {"schema_version": 2})
	findings := policy.deny with input as envelope
	profile(findings) == {["EN-001", "indeterminate"]}
}

# -- FP-003 ----------------------------------------------------------------

test_missing_makefile_is_fp003 if {
	findings := policy.deny with input as data.fixtures.no_makefile
	profile(findings) == {["FP-003", "noncompliant"]}
	messages(findings, "FP-003") == {"root Makefile is missing"}
}

test_missing_targets_are_fp003 if {
	findings := policy.deny with input as data.fixtures.missing_targets
	profile(findings) == {["FP-003", "noncompliant"]}
	messages(findings, "FP-003") == {
		"required Make target \"fmt\" is absent",
		"required Make target \"check-fmt\" is absent",
	}
}

# -- PD-002 / PD-003 / PD-004: recipes -------------------------------------

test_mdformat_wrapper_is_noncompliant_for_every_recipe_check if {
	findings := policy.deny with input as data.fixtures.mdformat_wrapper
	profile(findings) == {
		["PD-002", "noncompliant"],
		["PD-003", "noncompliant"],
		["PD-004", "noncompliant"],
	}
	messages(findings, "PD-002") == {"no recipe reachable from \"check-fmt\" runs mdtablefix"}
	messages(findings, "PD-003") == {"\"fmt\"-path recipe delegates to the mdformat-all wrapper; call mdtablefix directly"}
	messages(findings, "PD-004") == {"\"fmt\"-path recipe delegates to the mdformat-all wrapper; call markdownlint-cli2 directly"}
}

test_missing_select_flags_are_noncompliant if {
	findings := policy.deny with input as data.fixtures.missing_flags
	profile(findings) == {
		["PD-002", "noncompliant"],
		["PD-003", "noncompliant"],
		["PD-004", "noncompliant"],
	}
	messages(findings, "PD-002") == {"\"check-fmt\"-path recipe runs mdtablefix --check without --include-untracked"}
	messages(findings, "PD-003") == {"\"fmt\"-path recipe runs mdtablefix --in-place without --include-untracked"}
	messages(findings, "PD-004") == {"\"fmt\"-path recipe runs markdownlint-cli2 without --fix"}
}

test_soft_skipped_tools_are_noncompliant_with_lines if {
	findings := policy.deny with input as data.fixtures.soft_skip
	profile(findings) == {
		["PD-002", "noncompliant"],
		["PD-003", "noncompliant"],
		["PD-004", "noncompliant"],
	}
	some f in findings
	f.rule_id == "PD-002"
	f.line == 11
	contains(f.msg, "soft-skips mdtablefix")
}

test_swapped_modes_are_noncompliant if {
	findings := policy.deny with input as data.fixtures.mode_swapped
	profile(findings) == {["PD-002", "noncompliant"], ["PD-003", "noncompliant"]}
	messages(findings, "PD-002") == {"\"check-fmt\"-path recipe runs mdtablefix without --check"}
	messages(findings, "PD-003") == {"\"fmt\"-path recipe runs mdtablefix without --in-place"}
}

test_echoed_commands_are_not_invocations if {
	findings := policy.deny with input as data.fixtures.echo_decoy
	profile(findings) == {
		["PD-002", "noncompliant"],
		["PD-003", "noncompliant"],
		["PD-004", "noncompliant"],
	}
	messages(findings, "PD-003") == {"no recipe reachable from \"fmt\" runs mdtablefix"}
}

test_conditional_fmt_is_indeterminate_and_check_fmt_is_judged if {
	findings := policy.deny with input as data.fixtures.conditional
	profile(findings) == {["PD-003", "indeterminate"], ["PD-004", "indeterminate"]}
}

test_include_renders_every_recipe_check_indeterminate if {
	findings := policy.deny with input as data.fixtures.with_include
	profile(findings) == {
		["PD-002", "indeterminate"],
		["PD-003", "indeterminate"],
		["PD-004", "indeterminate"],
	}
}

test_recovered_parse_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.recovered
	profile(findings) == {
		["PD-002", "indeterminate"],
		["PD-003", "indeterminate"],
		["PD-004", "indeterminate"],
	}
}

test_ambiguous_flag_variable_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.ambiguous_variable
	profile(findings) == {["PD-002", "indeterminate"], ["PD-003", "indeterminate"]}
	some f in findings
	f.rule_id == "PD-002"
	contains(f.msg, "MDTABLEFIX_SELECT")
}

test_undefined_tool_variable_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.undefined_variable
	profile(findings) == {
		["PD-002", "indeterminate"],
		["PD-003", "indeterminate"],
		["PD-004", "indeterminate"],
	}
	some f in findings
	f.rule_id == "PD-003"
	contains(f.msg, "MARKDOWN_FORMATTER")
}

# Variable expansion resolves nested references but leaves a name assigned
# twice untouched.
test_expansion_resolves_single_valued_variables if {
	makefile := {"variables": [
		{"name": "A", "raw_value": "$(B) --git", "conditions": [], "define_block": false},
		{"name": "B", "raw_value": "mdtablefix", "conditions": [], "define_block": false},
		{"name": "C", "raw_value": "one", "conditions": [], "define_block": false},
		{"name": "C", "raw_value": "two", "conditions": [], "define_block": false},
	]}
	expanded := policy.expand("$(A) $(C)") with input as {"makefile": makefile}
	expanded == "mdtablefix --git $(C)"
}

# The tool must be the command word: an environment prefix is fine, a mention
# inside `echo` or an assignment value is not.
test_tool_invocation_requires_command_position if {
	policy.tool_invoked({"text": "FORCE_COLOR=0 mdtablefix --check"}, "mdtablefix") with input as {"makefile": {"variables": []}}
	policy.tool_invoked({"text": "cd docs && /usr/local/bin/mdtablefix --check"}, "mdtablefix") with input as {"makefile": {"variables": []}}
	not policy.tool_invoked({"text": "echo \"mdtablefix --check\""}, "mdtablefix") with input as {"makefile": {"variables": []}}
	not policy.tool_invoked({"text": "TOOL=mdtablefix --check"}, "mdtablefix") with input as {"makefile": {"variables": []}}
	not policy.tool_invoked({"text": "# mdtablefix --check"}, "mdtablefix") with input as {"makefile": {"variables": []}}
}

test_tool_binding_rejects_masked_status if {
	unbound := {"text": "mdtablefix --check | tee log", "ignore_errors": false}
	not policy.tool_binding(unbound, "mdtablefix") with input as {"makefile": {"variables": []}}
	chained := {"text": "mdtablefix --check --git && echo ok", "ignore_errors": false}
	policy.tool_binding(chained, "mdtablefix") with input as {"makefile": {"variables": []}}
}

# -- PD-005 ----------------------------------------------------------------

test_missing_config_is_pd005 if {
	findings := policy.deny with input as data.fixtures.config_missing
	profile(findings) == {["PD-005", "noncompliant"]}
	messages(findings, "PD-005") == {".markdownlint-cli2.jsonc is missing"}
}

test_alternate_config_is_named_in_the_finding if {
	findings := policy.deny with input as data.fixtures.config_alternate
	profile(findings) == {["PD-005", "noncompliant"]}
	messages(findings, "PD-005") == {".markdownlint-cli2.jsonc is missing (found .markdownlint.yaml instead)"}
}

test_drifted_config_names_each_divergence if {
	findings := policy.deny with input as data.fixtures.config_drifted
	profile(findings) == {["PD-005", "noncompliant"]}
	messages(findings, "PD-005") == {
		"config.MD004 must be {\"style\":\"dash\"}",
		"config.MD013 must be {\"code_block_line_length\":120,\"headings\":false,\"line_length\":80,\"tables\":false}",
		"config.MD029 must be {\"style\":\"ordered\"}",
		"ignores must include \".vtcode/**\"",
		"ignores must include \"memories/**\"",
		"ignores must include \"CRUSH.md\"",
	}
}

test_malformed_config_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.config_malformed
	profile(findings) == {["PD-005", "indeterminate"]}
}

test_config_without_config_object_is_noncompliant if {
	# `object.union` merges recursively, so the fixture's `markdownlint` is
	# removed first; otherwise its baseline `config` would survive the union.
	envelope := object.union(object.remove(data.fixtures.compliant, ["markdownlint"]), {"markdownlint": {
		"path": ".markdownlint-cli2.jsonc",
		"parsed": {"ignores": []},
		"error": null,
	}})
	findings := policy.deny with input as envelope
	some f in findings
	f.rule_id == "PD-005"
	f.msg == ".markdownlint-cli2.jsonc must carry a \"config\" object"
}

# -- PD-006 ----------------------------------------------------------------

test_shell_lint_steps_are_noncompliant if {
	findings := policy.deny with input as data.fixtures.workflow_shell_lint
	profile(findings) == {["PD-006", "noncompliant"]}
	count(findings) == 3
	some f in findings
	f.path == ".github/workflows/ci.yml"
	contains(f.msg, "Install CLI tools")
}

test_floating_action_tag_is_noncompliant if {
	findings := policy.deny with input as data.fixtures.workflow_floating_tag
	profile(findings) == {["PD-006", "noncompliant"]}
	messages(findings, "PD-006") == {"job \"lint-test\" pins DavidAnson/markdownlint-cli2-action to \"v24\"; pin it to a full commit SHA"}
}

test_narrow_globs_are_noncompliant if {
	findings := policy.deny with input as data.fixtures.workflow_narrow_globs
	profile(findings) == {["PD-006", "noncompliant"]}
	messages(findings, "PD-006") == {"job \"lint-test\" must pass globs: \"**/*.md\" to DavidAnson/markdownlint-cli2-action"}
}

test_no_markdown_lint_in_any_workflow_is_noncompliant if {
	findings := policy.deny with input as data.fixtures.workflow_none
	profile(findings) == {["PD-006", "noncompliant"]}
	some f in findings
	f.path == ".github/workflows"
}

test_absent_workflows_directory_is_noncompliant if {
	findings := policy.deny with input as data.fixtures.workflow_absent
	profile(findings) == {["PD-006", "noncompliant"]}
}

test_reusable_workflow_call_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.workflow_reusable_only
	profile(findings) == {["PD-006", "indeterminate"]}
}

test_malformed_workflow_is_indeterminate if {
	findings := policy.deny with input as data.fixtures.workflow_malformed
	profile(findings) == {["PD-006", "indeterminate"]}
	some f in findings
	f.path == ".github/workflows/ci.yml"
}

test_shell_lint_beside_the_action_is_still_noncompliant if {
	findings := policy.deny with input as data.fixtures.workflow_mixed
	profile(findings) == {["PD-006", "noncompliant"]}
	every f in findings {
		f.path == ".github/workflows/docs.yml"
	}
}
