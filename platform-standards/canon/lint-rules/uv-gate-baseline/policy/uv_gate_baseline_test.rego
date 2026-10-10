# Tests for uv-gate-baseline. Each fixture under fixtures/envelopes is the
# production envelope for a synthetic checkout (see fixtures/generate.py); the
# expected set is the exact rule and verdict pairs the policy must report, so a
# test fails on a missing finding and on an extra one.
package canon.lint_rules.uv_gate_baseline_test

import rego.v1

import data.canon.lint_rules.uv_gate_baseline as policy

reported(envelope) := {sprintf("%s/%s", [f.rule_id, f.verdict]) |
	some f in policy.deny with input as envelope
}

findings(envelope) := [f | some f in policy.deny with input as envelope]

test_bypass_dollar_uv if reported(data.fixtures.bypass_dollar_uv) == {"UV-003/noncompliant"}

test_bypass_unresolved_uv if reported(data.fixtures.bypass_unresolved_uv) == {"UV-003/noncompliant"}

test_bypass_uv_run if reported(data.fixtures.bypass_uv_run) == {"UV-003/noncompliant"}

test_bypass_uv_sync if reported(data.fixtures.bypass_uv_sync) == {"UV-003/noncompliant"}

test_bypass_uv_tool_run if reported(data.fixtures.bypass_uv_tool_run) == {"UV-003/noncompliant"}

test_bypass_uvx if reported(data.fixtures.bypass_uvx) == {"UV-003/noncompliant"}

test_cache_action_env if reported(data.fixtures.cache_action_env) == {"UV-002/noncompliant"}

test_cache_clean if reported(data.fixtures.cache_clean) == {"UV-005/noncompliant"}

test_cache_in_env_variable if reported(data.fixtures.cache_in_env_variable) == {"UV-002/noncompliant"}

test_cache_in_recipe if reported(data.fixtures.cache_in_recipe) == {"UV-002/noncompliant"}

test_cache_prune if reported(data.fixtures.cache_prune) == {"UV-005/noncompliant"}

test_cache_variable if reported(data.fixtures.cache_variable) == {"UV-002/noncompliant"}

test_cache_workflow_env if reported(data.fixtures.cache_workflow_env) == {"UV-002/noncompliant"}

test_cache_workflow_script if reported(data.fixtures.cache_workflow_script) == {"UV-002/noncompliant"}

test_compliant if reported(data.fixtures.compliant) == set()

test_gate_drifted if reported(data.fixtures.gate_drifted) == {"UV-001/noncompliant"}

test_gate_missing if reported(data.fixtures.gate_missing) == {"UV-001/noncompliant"}

test_gate_variable_twice if reported(data.fixtures.gate_variable_twice) == {"UV-003/indeterminate"}

test_gate_variable_wrong if reported(data.fixtures.gate_variable_wrong) == {"UV-003/noncompliant"}

test_git_dep_bare if reported(data.fixtures.git_dep_bare) == {"UV-007/noncompliant"}

test_git_dep_sha if reported(data.fixtures.git_dep_sha) == set()

test_git_dep_tag if reported(data.fixtures.git_dep_tag) == {"UV-007/noncompliant"}

test_git_source_branch if reported(data.fixtures.git_source_branch) == {"UV-007/noncompliant"}

test_git_source_rev if reported(data.fixtures.git_source_rev) == set()

test_git_source_tag if reported(data.fixtures.git_source_tag) == {"UV-007/noncompliant"}

test_lock_command if reported(data.fixtures.lock_command) == {"UV-005/noncompliant"}

test_lock_missing if reported(data.fixtures.lock_missing) == {"UV-004/noncompliant"}

test_maintenance_lock_ok if reported(data.fixtures.maintenance_lock_ok) == set()

test_makefile_include if reported(data.fixtures.makefile_include) == {"UV-003/indeterminate"}

test_not_applicable if reported(data.fixtures.not_applicable) == set()

test_pyproject_malformed if reported(data.fixtures.pyproject_malformed) == {"UV-007/indeterminate"}

test_refresh_flag if reported(data.fixtures.refresh_flag) == {"UV-005/noncompliant"}

test_retry_loop if reported(data.fixtures.retry_loop) == {"UV-005/noncompliant"}

test_tool_git_branch if reported(data.fixtures.tool_git_branch) == {"UV-006/noncompliant"}

test_tool_git_sha if reported(data.fixtures.tool_git_sha) == set()

test_tool_git_tag_other_repo if reported(data.fixtures.tool_git_tag_other_repo) == {"UV-006/noncompliant"}

test_tool_latest if reported(data.fixtures.tool_latest) == {"UV-006/noncompliant"}

test_tool_pinned_at if reported(data.fixtures.tool_pinned_at) == set()

test_tool_pinned_equals if reported(data.fixtures.tool_pinned_equals) == set()

test_tool_positional_unpinned if reported(data.fixtures.tool_positional_unpinned) == {"UV-006/noncompliant"}

test_tool_range if reported(data.fixtures.tool_range) == {"UV-006/noncompliant"}

test_tool_typos_builder_branch if reported(data.fixtures.tool_typos_builder_branch) == {"UV-006/noncompliant"}

test_tool_typos_builder_sha if reported(data.fixtures.tool_typos_builder_sha) == set()

test_tool_typos_builder_tag if reported(data.fixtures.tool_typos_builder_tag) == set()

test_tool_unpinned if reported(data.fixtures.tool_unpinned) == {"UV-006/noncompliant"}

test_tool_uvx_pinned_only_bypass if reported(data.fixtures.tool_uvx_pinned_only_bypass) == {"UV-003/noncompliant"}

test_tool_variable_nested if reported(data.fixtures.tool_variable_nested) == set()

test_tool_variable_resolved if reported(data.fixtures.tool_variable_resolved) == set()

test_tool_variable_unpinned if reported(data.fixtures.tool_variable_unpinned) == {"UV-006/noncompliant"}

test_tool_variable_unresolved if reported(data.fixtures.tool_variable_unresolved) == {"UV-006/indeterminate"}

test_tool_workflow_unpinned if reported(data.fixtures.tool_workflow_unpinned) == {"UV-006/noncompliant"}

test_upgrade_flag if reported(data.fixtures.upgrade_flag) == {"UV-005/noncompliant"}

test_upgrade_short_flag if reported(data.fixtures.upgrade_short_flag) == {"UV-005/noncompliant"}

test_workflow_malformed_only if reported(data.fixtures.workflow_malformed_only) == {"UV-003/indeterminate"}

test_workflow_only_no_gate if reported(data.fixtures.workflow_only_no_gate) == {"UV-001/noncompliant"}

test_bypass_unresolved_uvx if reported(data.fixtures.bypass_unresolved_uvx) == {"UV-003/noncompliant"}

test_gate_in_recipe_only if reported(data.fixtures.gate_in_recipe_only) == {"UV-001/noncompliant"}

test_gate_only_drifted if reported(data.fixtures.gate_only_drifted) == {"UV-001/noncompliant"}

test_gate_variable_other if reported(data.fixtures.gate_variable_other) == {"UV-003/noncompliant"}

test_lock_only if reported(data.fixtures.lock_only) == {"UV-001/noncompliant"}

test_makefile_recovered if reported(data.fixtures.makefile_recovered) == {"UV-003/indeterminate"}

test_retry_without_uv_ok if reported(data.fixtures.retry_without_uv_ok) == set()

test_retry_word if reported(data.fixtures.retry_word) == {"UV-005/noncompliant"}

test_uv_in_recipe_only if reported(data.fixtures.uv_in_recipe_only) == {"UV-001/noncompliant", "UV-003/noncompliant"}

test_uv_in_variable_only if reported(data.fixtures.uv_in_variable_only) == {"UV-001/noncompliant"}

test_uv_tool_run_from_unpinned if reported(data.fixtures.uv_tool_run_from_unpinned) == {"UV-003/noncompliant", "UV-006/noncompliant"}

test_uv_tool_run_unpinned_positional if reported(data.fixtures.uv_tool_run_unpinned_positional) == {"UV-003/noncompliant", "UV-006/noncompliant"}

test_uvx_unpinned_positional if reported(data.fixtures.uvx_unpinned_positional) == {"UV-003/noncompliant", "UV-006/noncompliant"}

test_workflow_malformed_with_uv if reported(data.fixtures.workflow_malformed_with_uv) == {"UV-002/indeterminate"}

test_uvx_from_unpinned if reported(data.fixtures.uvx_from_unpinned) == {"UV-003/noncompliant", "UV-006/noncompliant"}

test_uvx_from_equals_pinned if reported(data.fixtures.uvx_from_equals_pinned) == {"UV-003/noncompliant"}

# A repository that still vendors an earlier canonical helper passes while that
# version is listed, and fails once it is dropped.
test_gate_older_canon_is_accepted_while_listed if reported(data.fixtures.gate_older_canon) == set()

test_gate_older_canon_fails_once_dropped if {
	current_only := {"fixture": data.parameters.gate_digests.fixture}
	reported(data.fixtures.gate_older_canon) == {"UV-001/noncompliant"} with data.parameters.gate_digests as current_only
}

# Fail closed where uv may be hidden or named by path, and the release-tag
# exception applies to direct invocations only.
test_gate_variable_conditional if reported(data.fixtures.gate_variable_conditional) == {"UV-003/indeterminate"}

test_git_dep_marker if reported(data.fixtures.git_dep_marker) == set()

test_include_without_uv if reported(data.fixtures.include_without_uv) == {"UV-003/indeterminate"}

test_nested_action_cache if reported(data.fixtures.nested_action_cache) == {"UV-002/noncompliant"}

test_path_qualified_uv if reported(data.fixtures.path_qualified_uv) == {"UV-003/noncompliant"}

test_path_qualified_uv_only if reported(data.fixtures.path_qualified_uv_only) == {"UV-001/noncompliant", "UV-003/noncompliant"}

test_recovered_without_uv if reported(data.fixtures.recovered_without_uv) == {"UV-003/indeterminate"}

test_tool_other_tag_direct if reported(data.fixtures.tool_other_tag_direct) == {"UV-003/noncompliant", "UV-006/noncompliant"}

test_tool_typos_builder_branch_direct if reported(data.fixtures.tool_typos_builder_branch_direct) == {"UV-003/noncompliant", "UV-006/noncompliant"}

test_tool_typos_builder_tag_via_gate if reported(data.fixtures.tool_typos_builder_tag_via_gate) == {"UV-006/noncompliant"}

# The exception names typos-config-builder alone: another tool at a tag, run
# directly, fails both checks, even beside a listed one on the same line.
test_tool_mixed_tags_one_line if reported(data.fixtures.tool_mixed_tags_one_line) == {"UV-003/noncompliant", "UV-006/noncompliant"}

# Each command on a chained recipe line is judged on its own, so an exempt
# release-tag command cannot hide a bare uv beside it.
test_bypass_beside_release_tag if reported(data.fixtures.bypass_beside_release_tag) == {"UV-003/noncompliant"}

test_bypass_beside_release_tag_semicolon if reported(data.fixtures.bypass_beside_release_tag_semicolon) == {"UV-003/noncompliant"}

test_bypass_beside_release_tag_or if reported(data.fixtures.bypass_beside_release_tag_or) == {"UV-003/noncompliant"}

test_bypass_beside_release_tag_pipe if reported(data.fixtures.bypass_beside_release_tag_pipe) == {"UV-003/noncompliant"}

test_release_tag_beside_release_tag if reported(data.fixtures.release_tag_beside_release_tag) == set()

# Make unescapes `\#` in a variable's value (so the cmd-mox form is a real
# pin) but not in a recipe line, where the backslash reaches the shell.
test_tool_git_sha_variable_escaped_fragment if reported(data.fixtures.tool_git_sha_variable_escaped_fragment) == set()

test_tool_git_short_sha_variable_escaped_fragment if reported(data.fixtures.tool_git_short_sha_variable_escaped_fragment) == {"UV-006/noncompliant"}

test_tool_git_branch_variable_escaped_fragment if reported(data.fixtures.tool_git_branch_variable_escaped_fragment) == {"UV-006/noncompliant"}

test_tool_git_sha_recipe_escaped_fragment if reported(data.fixtures.tool_git_sha_recipe_escaped_fragment) == {"UV-006/noncompliant"}

test_tool_git_sha_bare_fragment if reported(data.fixtures.tool_git_sha_bare_fragment) == set()

test_tool_git_sha_with_suffix if reported(data.fixtures.tool_git_sha_with_suffix) == {"UV-006/noncompliant"}

# Lines and messages that make a finding usable.

test_bypass_names_the_recipe_line if {
	some f in findings(data.fixtures.bypass_uv_run)
	f.rule_id == "UV-003"
	f.path == "Makefile"
	f.line == 4
}

test_drifted_gate_names_its_digest if {
	some f in findings(data.fixtures.gate_drifted)
	f.rule_id == "UV-001"
	contains(f.msg, "not a canonical uv_gate.py")
}

test_typos_builder_tag_is_accepted_as_the_spelling_baseline_requires if {
	count(findings(data.fixtures.tool_typos_builder_tag)) == 0
	count(findings(data.fixtures.compliant)) == 0
}

test_a_tag_is_not_accepted_for_a_tool_that_is_not_listed if {
	reported(data.fixtures.tool_git_tag_other_repo) == {"UV-006/noncompliant"}
}

test_release_tag_tools_param_widens_the_tag_exception if {
	widened := ["github.com/example/tool"]
	count(findings(data.fixtures.tool_other_tag_direct)) == 0 with data.parameters.release_tag_tools as widened
}

test_a_gated_tag_stays_refused_even_for_a_listed_tool if {
	widened := ["github.com/example/tool"]
	reported(data.fixtures.tool_git_tag_other_repo) == {"UV-006/noncompliant"} with data.parameters.release_tag_tools as widened
}

test_no_canonical_digest_is_indeterminate_not_clean if {
	reported(data.fixtures.compliant) == {"UV-001/indeterminate"} with data.parameters.gate_digests as {}
}

test_a_second_canonical_digest_is_accepted if {
	count(findings(data.fixtures.compliant)) == 0 with data.parameters.gate_digests as {"old": "0", "new": data.parameters.gate_digests.fixture}
}

test_the_maintenance_target_list_is_a_parameter if {
	reported(data.fixtures.maintenance_lock_ok) == {"UV-005/noncompliant"} with data.parameters.maintenance_targets as []
}

test_a_foreign_envelope_is_indeterminate if {
	foreign := {"schema_version": 2, "kind": "policy-input/uv-gate-baseline", "makefile": null}
	reported(foreign) == {"EN-001/indeterminate"}
}

test_a_foreign_kind_is_indeterminate if {
	foreign := object.union(data.fixtures.compliant, {"kind": "policy-input/other"})
	reported(foreign) == {"EN-001/indeterminate"}
}
