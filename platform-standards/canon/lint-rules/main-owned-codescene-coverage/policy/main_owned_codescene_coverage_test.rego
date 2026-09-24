# Fixture tests for CV-005 main-owned CodeScene coverage publication.
package canon.lint_rules.main_owned_codescene_coverage_test

import rego.v1

import data.canon.lint_rules.main_owned_codescene_coverage as policy

profile(findings) := {
  [finding.verdict, finding.path, finding.msg] | some finding in findings
}

# The compliant fixture spells neither baseline path, so this is also the
# case where both workflows take the shared action's defaults and their
# coverage identities match on those.
test_compliant_workflows_have_no_findings if {
  findings := policy.deny with input as data.fixtures.compliant
  count(findings) == 0
}

test_explicit_baseline_identity_matches if {
  findings := policy.deny with input as data.fixtures.explicit_baseline_match
  count(findings) == 0
}

test_mismatched_python_baseline_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.mismatched_python_baseline
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request coverage baseline has no matching main publisher"],
  }
}

test_mismatched_rust_baseline_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.mismatched_rust_baseline
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request coverage baseline has no matching main publisher"],
  }
}

test_unrelated_pr_workflow_does_not_require_ratchet if {
  findings := policy.deny with input as data.fixtures.unrelated_pr_workflow
  count(findings) == 0
}

test_pr_check_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.pr_check
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request workflow invokes CodeScene"],
  }
}

test_pr_upload_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.pr_upload
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request workflow invokes CodeScene"],
    ["noncompliant", ".github/workflows/ci.yml", "pull-request workflow receives CS_ACCESS_TOKEN"],
  }
}

test_pr_shell_check_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.pr_shell_check
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request workflow invokes CodeScene"],
  }
}

test_main_shell_upload_is_compliant if {
  findings := policy.deny with input as data.fixtures.main_shell_upload
  count(findings) == 0
}

test_missing_main_upload_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.missing_main_upload
  profile(findings) == {
    ["noncompliant", ".github/workflows", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage"],
    ["noncompliant", ".github/workflows", "pull requests ratchet coverage on Linux with no Linux lane on the trunk push"],
  }
}

test_missing_pr_ratchet_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.missing_pr_ratchet
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yaml", "pull-request workflow lacks ratcheting coverage generation"],
  }
}

test_main_dispatch_wrong_branch_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.main_dispatch_wrong_branch
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene publication is not restricted to a main-only workflow"],
    ["noncompliant", ".github/workflows", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage"],
  }
}

test_main_dispatch_extra_trigger_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.main_dispatch_extra_trigger
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene publication is not restricted to a main-only workflow"],
    ["noncompliant", ".github/workflows", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage"],
  }
}

# A document that cannot be decoded is reported as indeterminate and nothing
# further is claimed about the repository: it has no readable coverage lane,
# so asserting that its publisher is missing would be a guess.
test_malformed_workflow_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.malformed
  profile(findings) == {
    ["indeterminate", ".github/workflows/ci.yml", "workflow shape cannot be evaluated safely"],
  }
}

# A reusable job hides its own steps, not the document. The finding names the
# job so an operator knows what was not read; the workflow's other jobs stay
# readable, which is what keeps the rule from going silent on a repository
# whose pull-request lane happens to delegate one leg.
test_a_reusable_job_is_indeterminate_by_name if {
  findings := policy.deny with input as data.fixtures.reusable
  profile(findings) == {
    ["indeterminate", ".github/workflows/ci.yml", "job coverage delegates to a reusable workflow this audit cannot read"],
  }
}

test_unrelated_reusable_workflow_is_ignored if {
  findings := policy.deny with input as data.fixtures.unrelated_reusable
  count(findings) == 0
}

test_all_pr_coverage_steps_require_ratcheting if {
  findings := policy.deny with input as data.fixtures.multiple_pr_coverage_steps
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request workflow lacks ratcheting coverage generation"],
  }
}

test_each_pr_generator_requires_a_matching_main_identity if {
  findings := policy.deny with input as data.fixtures.multiple_pr_generators_one_unmatched
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request coverage baseline has no matching main publisher"],
  }
}

test_aliased_token_references_are_noncompliant if {
  findings := policy.deny with input as data.fixtures.aliased_token_references
  profile(findings) == {
    ["noncompliant", ".github/workflows/workflow-env.yml", "pull-request workflow receives CS_ACCESS_TOKEN"],
    ["noncompliant", ".github/workflows/job-env.yml", "pull-request workflow receives CS_ACCESS_TOKEN"],
    ["noncompliant", ".github/workflows/step-env.yml", "pull-request workflow receives CS_ACCESS_TOKEN"],
    ["noncompliant", ".github/workflows/action-input.yml", "pull-request workflow receives CS_ACCESS_TOKEN"],
  }
}

test_non_object_workflow_uses_safe_finding_path if {
  findings := policy.deny with input as data.fixtures.non_object_workflow
  profile(findings) == {
    ["indeterminate", ".github/workflows", "workflow shape cannot be evaluated safely"],
  }
}

test_duplicate_findings_retain_count_and_paths if {
  findings := policy.deny with input as data.fixtures.duplicate_pr_coverage
  count(findings) == 2
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request workflow lacks ratcheting coverage generation"],
    ["noncompliant", ".github/workflows/other-ci.yml", "pull-request workflow lacks ratcheting coverage generation"],
  }
}

test_environment_prefixed_pr_cli_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.pr_shell_environment_prefix
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request workflow invokes CodeScene"],
  }
}

test_environment_prefixed_main_cli_upload_is_compliant if {
  findings := policy.deny with input as data.fixtures.main_shell_environment_prefix
  count(findings) == 0
}

# --------------------------------------------------------------------------
# Clause 1: a pull-request lane ratchets and keeps its report local.
# --------------------------------------------------------------------------

test_pr_coverage_omitting_publish_artefact_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause1_publish_artefact_omitted
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request coverage does not set publish-artefact false"],
  }
}

test_pr_coverage_publishing_its_artefact_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause1_publish_artefact_true
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request coverage does not set publish-artefact false"],
  }
}

test_pr_coverage_with_local_artefact_is_compliant if {
  findings := policy.deny with input as data.fixtures.clause1_publish_artefact_false
  count(findings) == 0
}

# A YAML 1.1 loader resolves the unquoted `on:` key to the boolean True, which
# JSON renders as "true". Reading only the string key would classify both
# workflows below as triggerless, and every trigger-derived clause would then
# range over an empty set.
test_boolean_trigger_key_compliant_repository_has_no_findings if {
  findings := policy.deny with input as data.fixtures.clause1_boolean_trigger_key_compliant
  count(findings) == 0
}

test_boolean_trigger_key_pull_request_codescene_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause1_boolean_trigger_key_pr_codescene
  profile(findings) == {
    ["noncompliant", ".github/workflows/ci.yml", "pull-request workflow invokes CodeScene"],
  }
}

# --------------------------------------------------------------------------
# Clause 2: the single publisher is guarded on the trunk ref and the
# credential, and runs under a concurrency block.
# --------------------------------------------------------------------------

test_upload_without_ref_guard_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause2_upload_without_ref_guard
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on github.ref == 'refs/heads/main'"],
  }
}

test_upload_without_token_guard_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause2_upload_without_token_guard
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

# The output is a guard only when an earlier step in the same job writes it
# from the credential check, and the uploader receives that same credential.
test_direct_token_step_output_guard_is_compliant if {
  findings := policy.deny with input as data.fixtures.clause2_direct_token_step_output_guard
  count(findings) == 0
}

test_step_output_producer_block_scalar_is_compliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps/1/run",
    "value": concat("", [policy.token_availability_run, "\n"]),
  }])
  findings := policy.deny with input as fixture
  count(findings) == 0
}

test_step_output_producer_without_run_is_noncompliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "remove",
    "path": "/workflows/1/parsed/jobs/coverage/steps/1/run",
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_producer_with_non_string_run_is_noncompliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps/1/run",
    "value": [policy.token_availability_run],
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_producer_with_altered_command_is_noncompliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps/1/run",
    "value": `echo "available=${{ secrets.CS_ACCESS_TOKEN != '' }}" | tee -a "$GITHUB_OUTPUT"`,
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_producer_without_credential_is_noncompliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps/1/run",
    "value": `echo "available=true" >> "$GITHUB_OUTPUT"`,
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_upload_without_output_guard_is_noncompliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps/2/if",
    "value": "github.ref == 'refs/heads/main'",
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_upload_without_ref_guard_is_noncompliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps/2/if",
    "value": "steps.codescene-token.outputs.available == 'true'",
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on github.ref == 'refs/heads/main'"],
  }
}

test_step_output_upload_without_access_token_is_noncompliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "remove",
    "path": "/workflows/1/parsed/jobs/coverage/steps/2/with/access-token",
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_upload_with_indirect_access_token_is_noncompliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps/2/with/access-token",
    "value": "${{ env.CS_ACCESS_TOKEN }}",
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_disjunction_is_not_a_guard if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps/2/if",
    "value": "steps.codescene-token.outputs.available == 'true' && github.ref == 'refs/heads/main' || always()",
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on github.ref == 'refs/heads/main'"],
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_guard_with_unmatched_producer_id_is_noncompliant if {
  fixture := json.patch(data.fixtures.clause2_direct_token_step_output_guard, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps/1/id",
    "value": "different-token",
  }])
  findings := policy.deny with input as fixture
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_producer_after_upload_is_noncompliant if {
  fixture := data.fixtures.clause2_direct_token_step_output_guard
  steps := fixture.workflows[1].parsed.jobs.coverage.steps
  reordered := [steps[0], steps[2], steps[1]]
  changed := json.patch(fixture, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs/coverage/steps",
    "value": reordered,
  }])
  findings := policy.deny with input as changed
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_step_output_producer_in_another_job_is_noncompliant if {
  fixture := data.fixtures.clause2_direct_token_step_output_guard
  coverage := fixture.workflows[1].parsed.jobs.coverage
  steps := coverage.steps
  jobs := {
    "coverage": object.union(coverage, {"steps": [steps[0], steps[2]]}),
    "token": {"runs-on": "ubuntu-latest", "steps": [steps[1]]},
  }
  changed := json.patch(fixture, [{
    "op": "replace",
    "path": "/workflows/1/parsed/jobs",
    "value": jobs,
  }])
  findings := policy.deny with input as changed
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_arbitrary_step_output_alias_is_noncompliant if {
  fixture := data.fixtures.clause2_direct_token_step_output_guard
  changed := json.patch(fixture, [
    {
      "op": "replace",
      "path": "/workflows/1/parsed/jobs/coverage/steps/1/run",
      "value": `echo "ready=${{ secrets.CS_ACCESS_TOKEN != '' }}" >> "$GITHUB_OUTPUT"`,
    },
    {
      "op": "replace",
      "path": "/workflows/1/parsed/jobs/coverage/steps/2/if",
      "value": "steps.codescene-token.outputs.ready == 'true' && github.ref == 'refs/heads/main'",
    },
  ])
  findings := policy.deny with input as changed
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_unguarded_upload_reports_both_missing_guards if {
  findings := policy.deny with input as data.fixtures.clause2_upload_without_any_guard
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on github.ref == 'refs/heads/main'"],
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

test_ref_guard_written_in_either_order_is_compliant if {
  findings := policy.deny with input as data.fixtures.clause2_reversed_ref_guard_is_compliant
  count(findings) == 0
}

# The push filter restricts pushes, not dispatches: a `workflow_dispatch` runs
# on whatever ref the dispatcher selects. A publisher reachable that way with
# only the credential guard (netsuke's shape) uploads a feature branch as the
# trunk, so the trigger-level `branches: [main]` is not an alternative guard.
test_dispatch_reachable_publisher_without_ref_guard_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause2_dispatch_reachable_publisher_without_ref_guard
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on github.ref == 'refs/heads/main'"],
  }
}

# A trailing disjunction contains the comparison while making it optional,
# so a dispatch from any branch would upload. Both guards are refused.
test_disjunction_makes_the_guards_optional if {
  findings := policy.deny with input as data.fixtures.clause2_ref_guard_made_optional_by_disjunction
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on github.ref == 'refs/heads/main'"],
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential"],
  }
}

# The comparison must be a whole conjunct; inside a negation it guards the
# opposite ref.
test_negated_ref_guard_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause2_negated_ref_guard
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "CodeScene upload step is not guarded on github.ref == 'refs/heads/main'"],
  }
}

test_disjunction_inside_a_quoted_literal_is_compliant if {
  findings := policy.deny with input as data.fixtures.clause2_quoted_disjunction_is_compliant
  count(findings) == 0
}

test_guard_without_expression_wrapper_is_compliant if {
  findings := policy.deny with input as data.fixtures.clause2_unwrapped_guard_is_compliant
  count(findings) == 0
}

test_publisher_cancelling_in_progress_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause2_publisher_cancels_in_progress
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "main coverage publisher cancels in progress rather than queueing"],
  }
}

test_publisher_cancelling_by_expression_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause2_publisher_cancels_by_expression
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "main coverage publisher cancels in progress rather than queueing"],
  }
}

test_publisher_without_cancel_key_is_compliant if {
  findings := policy.deny with input as data.fixtures.clause2_publisher_without_cancel_key_is_compliant
  count(findings) == 0
}

test_publisher_without_concurrency_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause2_publisher_without_concurrency
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "main coverage publisher has no concurrency block"],
  }
}

test_two_main_publishers_are_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause2_two_publishers
  profile(findings) == {
    ["noncompliant", ".github/workflows", "more than one main-only workflow uploads CodeScene coverage: .github/workflows/coverage-main.yml, .github/workflows/coverage-publish.yml"],
  }
}

# --------------------------------------------------------------------------
# Clause 3: the installer digest the uploader stopped reading is gone.
# --------------------------------------------------------------------------

test_installer_checksum_input_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause3_installer_checksum_input
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "workflow passes the removed installer-checksum input"],
    ["noncompliant", ".github/workflows/coverage-main.yml", "workflow references the CODESCENE_CLI_SHA256 variable"],
  }
}

test_digest_variable_reference_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause3_digest_variable_reference
  profile(findings) == {
    ["noncompliant", ".github/workflows/coverage-main.yml", "workflow references the CODESCENE_CLI_SHA256 variable"],
  }
}

test_digest_refresher_workflow_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause3_digest_refresher_workflow
  profile(findings) == {
    ["noncompliant", ".github/workflows/get-codescene-sha.yml", "workflow refreshes the CodeScene installer digest"],
  }
}

# --------------------------------------------------------------------------
# Clause 4: generate-coverage keys the ratchet baseline by `runner.os` and
# saves it only on a push to main, so a platform that ratchets on pull
# requests needs that platform on the trunk push or it never has a baseline.
# --------------------------------------------------------------------------

test_windows_pr_ratchet_without_trunk_lane_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause4_windows_pr_without_trunk_lane
  profile(findings) == {
    ["noncompliant", ".github/workflows", "pull requests ratchet coverage on Windows with no Windows lane on the trunk push"],
  }
}

test_windows_pr_ratchet_with_trunk_lane_is_compliant if {
  findings := policy.deny with input as data.fixtures.clause4_windows_pr_with_trunk_lane
  count(findings) == 0
}

test_macos_pr_ratchet_without_trunk_lane_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.clause4_macos_pr_without_trunk_lane
  profile(findings) == {
    ["noncompliant", ".github/workflows", "pull requests ratchet coverage on macOS with no macOS lane on the trunk push"],
  }
}

test_unclassified_runner_label_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.clause4_unclassified_runner_label
  profile(findings) == {
    ["indeterminate", ".github/workflows/ci.yml", "coverage job coverage has no recognized runner platform"],
  }
}

test_ubicloud_labels_are_one_linux_platform if {
  findings := policy.deny with input as data.fixtures.clause4_ubicloud_label_is_linux
  count(findings) == 0
}

# A repository that generates no coverage anywhere has nothing for a main
# publisher to publish, so it is not a CV-005 subject.
test_repository_without_a_coverage_lane_is_not_a_subject if {
  findings := policy.deny with input as data.fixtures.no_coverage_lane
  count(findings) == 0
}

# The uploader defaults `mode` to "upload". A publisher that omits the input
# uploads, and is the wiring the estate's reference repositories use; reading
# the spelling rather than the effective value would report correct wiring as
# broken and teach people to edit a working workflow to satisfy the audit.
test_publisher_omitting_the_upload_mode_is_compliant if {
  findings := policy.deny with input as data.fixtures.clause2_upload_mode_defaulted
  count(findings) == 0
}

test_a_main_only_check_step_is_not_a_publisher if {
  findings := policy.deny with input as data.fixtures.clause2_check_mode_is_not_a_publisher
  profile(findings) == {
    ["noncompliant", ".github/workflows", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage"],
  }
}

test_a_reusable_job_is_reported_beside_a_visible_job if {
  findings := policy.deny with input as data.fixtures.reusable_job_beside_a_visible_job
  profile(findings) == {
    ["indeterminate", ".github/workflows/ci.yml", "job windows delegates to a reusable workflow this audit cannot read"],
  }
}

# The clause that matters: a delegated job must not hide its neighbour. This
# is the reference wiring's shape, and treating the whole document as
# unreadable made the rule silent about a lane it could see perfectly well.
test_a_reusable_job_does_not_hide_its_neighbour if {
  findings := policy.deny with input as data.fixtures.reusable_job_does_not_hide_its_neighbour
  profile(findings) == {
    ["indeterminate", ".github/workflows/ci.yml", "job windows delegates to a reusable workflow this audit cannot read"],
    ["noncompliant", ".github/workflows/ci.yml", "pull-request coverage does not set publish-artefact false"],
  }
}

# A runner label written as an expression is classified only when every
# literal it can select agrees on a platform. The estate's fork fallback
# chooses between two Linux labels, so it does.
test_a_fork_fallback_between_two_linux_labels_classifies if {
  findings := policy.deny with input as data.fixtures.clause4_fork_fallback_expression_agrees
  count(findings) == 0
}

test_an_expression_whose_arms_disagree_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.clause4_expression_arms_disagree
  profile(findings) == {
    ["indeterminate", ".github/workflows/ci.yml", "coverage job coverage has no recognized runner platform"],
  }
}

test_an_expression_naming_an_unknown_label_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.clause4_expression_names_an_unknown_label
  profile(findings) == {
    ["indeterminate", ".github/workflows/ci.yml", "coverage job coverage has no recognized runner platform"],
  }
}

# --------------------------------------------------------------------------
# The envelope guard. A document of another shape is indeterminate: nothing
# about the repository has been read, so nothing about it can be asserted.
# --------------------------------------------------------------------------

test_a_future_schema_version_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.envelope_wrong_schema_version
  profile(findings) == {
    ["indeterminate", ".github/workflows", "workflow envelope has an unknown shape"],
  }
}

test_a_missing_schema_version_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.envelope_without_schema_version
  profile(findings) == {
    ["indeterminate", ".github/workflows", "workflow envelope has an unknown shape"],
  }
}

test_another_packages_envelope_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.envelope_wrong_kind
  profile(findings) == {
    ["indeterminate", ".github/workflows", "workflow envelope has an unknown shape"],
  }
}

test_a_non_array_workflows_field_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.envelope_workflows_not_an_array
  profile(findings) == {
    ["indeterminate", ".github/workflows", "workflow envelope has an unknown shape"],
  }
}
