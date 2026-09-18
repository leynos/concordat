# Fixture tests for CV-005 main-owned CodeScene coverage publication.
package canon.lint_rules.main_owned_codescene_coverage_test

import rego.v1

import data.canon.lint_rules.main_owned_codescene_coverage as policy

profile(findings) := {
  [finding.verdict, finding.path, finding.msg] | some finding in findings
}

test_compliant_workflows_have_no_findings if {
  findings := policy.deny with input as data.fixtures.compliant
  count(findings) == 0
}

test_default_baseline_identity_matches if {
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

test_malformed_workflow_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.malformed
  profile(findings) == {
    ["indeterminate", ".github/workflows/ci.yml", "workflow shape cannot be evaluated safely"],
    ["noncompliant", ".github/workflows", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage"],
  }
}

test_reusable_workflow_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.reusable
  profile(findings) == {
    ["indeterminate", ".github/workflows/ci.yml", "workflow shape cannot be evaluated safely"],
    ["noncompliant", ".github/workflows", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage"],
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
