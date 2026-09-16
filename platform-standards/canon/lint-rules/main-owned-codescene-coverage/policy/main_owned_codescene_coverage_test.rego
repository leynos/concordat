# Fixture tests for CV-005 main-owned CodeScene coverage publication.
package canon.lint_rules.main_owned_codescene_coverage_test

import rego.v1

import data.canon.lint_rules.main_owned_codescene_coverage as policy

profile(findings) := {[finding.verdict, finding.msg] | some finding in findings}

test_compliant_workflows_have_no_findings if {
  findings := policy.deny with input as data.fixtures.compliant
  count(findings) == 0
}

test_pr_check_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.pr_check
  profile(findings) == {
    ["noncompliant", "pull-request workflow invokes CodeScene"],
  }
}

test_pr_upload_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.pr_upload
  profile(findings) == {
    ["noncompliant", "pull-request workflow invokes CodeScene"],
    ["noncompliant", "pull-request workflow receives CS_ACCESS_TOKEN"],
  }
}

test_missing_main_upload_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.missing_main_upload
  profile(findings) == {
    ["noncompliant", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage"],
  }
}

test_missing_pr_ratchet_is_noncompliant if {
  findings := policy.deny with input as data.fixtures.missing_pr_ratchet
  profile(findings) == {
    ["noncompliant", "pull-request workflow lacks ratcheting coverage generation"],
  }
}

test_malformed_workflow_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.malformed
  profile(findings) == {
    ["indeterminate", "workflow shape cannot be evaluated safely"],
    ["noncompliant", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage"],
  }
}

test_reusable_workflow_is_indeterminate if {
  findings := policy.deny with input as data.fixtures.reusable
  profile(findings) == {
    ["indeterminate", "workflow shape cannot be evaluated safely"],
    ["noncompliant", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage"],
  }
}
