package canon.lint_rules.pr_run_cancellation_test

import data.canon.lint_rules.pr_run_cancellation as policy
import rego.v1

empty_parameters := {
	"severity": "warning",
	"exemptions": [],
	"repository_exemptions": [],
}

parameters(listed) := {
	"severity": "warning",
	"exemptions": listed,
	"repository_exemptions": [],
}

repository_parameters(listed) := {
	"severity": "warning",
	"exemptions": [],
	"repository_exemptions": listed,
}

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as empty_parameters
}

findings_with(fixture, supplied) := fs if {
	fs := policy.deny with input as fixture with data.parameters as supplied
}

profile(fixture) := {[f.rule_id, f.severity, f.verdict, f.path] | some f in findings(fixture)}

profile_with(fixture, supplied) := {[f.rule_id, f.severity, f.verdict, f.path] | some f in findings_with(fixture, supplied)}

exemption := [{
	"repository": "leynos/example",
	"path": ".github/workflows/ci.yml",
	"rationale": "the lane serialises every push on purpose",
	"review_trigger": "review when the lane gains a merge queue",
}]

blank_rationale := [{
	"repository": "leynos/example",
	"path": ".github/workflows/ci.yml",
	"rationale": "   ",
	"review_trigger": "review when the lane gains a merge queue",
}]

blank_review_trigger := [{
	"repository": "leynos/example",
	"path": ".github/workflows/ci.yml",
	"rationale": "the lane serialises every push on purpose",
	"review_trigger": "",
}]

blank_repository := [{
	"repository": " ",
	"path": ".github/workflows/ci.yml",
	"rationale": "the lane serialises every push on purpose",
	"review_trigger": "review when the lane gains a merge queue",
}]

other_repository := [{
	"repository": "leynos/other",
	"path": ".github/workflows/ci.yml",
	"rationale": "the lane serialises every push on purpose",
	"review_trigger": "review when the lane gains a merge queue",
}]

repository_exemption := [{
	"repository": "leynos/example",
	"rationale": "this repository has no pull-request automation",
	"review_trigger": "review when pull-request automation is added",
}]

test_the_reference_shape_is_compliant if {
	count(findings(data.fixtures.compliant_reference)) == 0
}

# A workflow-level block covers every job, including the one disabled by the
# pull request filter, so neither job needs a block of its own.
test_a_cancelling_block_on_every_job_is_compliant if {
	count(findings(data.fixtures.compliant_jobs)) == 0
}

# A reusable-workflow caller is judged by its own concurrency block, exactly as
# written, rather than by the jobs inside the called workflow.
test_a_reusable_caller_job_is_judged_by_its_own_concurrency if {
	count(findings(data.fixtures.compliant_reusable_caller)) == 0
}

test_a_group_naming_the_head_ref_is_compliant_regardless_of_case if {
	count(findings(data.fixtures.compliant_head_ref_group)) == 0
}

test_a_pr_workflow_without_concurrency_is_noncompliant if {
	profile(data.fixtures.missing_workflow_concurrency) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_rc001_names_every_uncovered_job if {
	some f in findings(data.fixtures.missing_workflow_concurrency)
	contains(f.msg, "lint, test")
}

test_a_non_cancelling_workflow_block_is_noncompliant if {
	profile(data.fixtures.non_cancelling_workflow_concurrency) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_workflow_group_without_the_pull_request_is_noncompliant if {
	profile(data.fixtures.missing_group_pull_request) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# The covered jobs pass; the one job whose group would cancel another pull
# request's run is reported once.
test_a_job_group_without_the_pull_request_is_noncompliant if {
	profile(data.fixtures.job_group_missing_pull_request) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# GitHub applies workflow-level and job-level concurrency independently, so a
# compliant workflow group cannot excuse a job group shared across pull
# requests.
test_a_job_group_is_checked_even_when_the_workflow_group_names_the_pull_request if {
	profile(data.fixtures.workflow_and_job_group_missing_pull_request) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_compliant_group_at_both_levels_is_compliant if {
	count(findings(data.fixtures.compliant_workflow_and_job)) == 0
}

# The marker only refers to the pull request when it sits inside an
# expression; as literal text it is part of a constant group.
test_a_literal_marker_outside_an_expression_is_noncompliant if {
	profile(data.fixtures.literal_marker_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_marker_outside_the_expression_is_noncompliant if {
	profile(data.fixtures.marker_outside_expression) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A marker inside a quoted string literal is not evaluated, so the group is
# constant however the expression is shaped.
test_a_quoted_marker_literal_is_noncompliant if {
	profile(data.fixtures.quoted_marker_literal) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_quoted_literal_beside_an_evaluated_marker_is_compliant if {
	count(findings(data.fixtures.quoted_marker_beside_reference)) == 0
}

test_a_push_only_workflow_is_not_audited if {
	count(findings(data.fixtures.push_only)) == 0
}

test_a_schedule_only_workflow_is_not_audited if {
	count(findings(data.fixtures.schedule_only)) == 0
}

test_a_workflow_call_only_workflow_is_not_audited if {
	count(findings(data.fixtures.workflow_call_only)) == 0
}

test_pull_request_triggered_through_a_list_is_audited if {
	profile(data.fixtures.trigger_list_form) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_pull_request_target_triggered_through_a_mapping_is_audited if {
	profile(data.fixtures.trigger_target_mapping_form) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_pull_request_triggered_by_a_scalar_is_audited if {
	profile(data.fixtures.trigger_scalar_form) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# An unquoted `on` decodes as the boolean True, so the document carries the key
# "true" and the trigger would be invisible to a reader that knows only "on".
test_the_unquoted_on_key_is_read if {
	profile(data.fixtures.trigger_unquoted_boolean_key) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_malformed_workflow_is_indeterminate if {
	profile(data.fixtures.malformed_yaml) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_a_document_that_is_not_a_mapping_is_indeterminate if {
	profile(data.fixtures.document_not_mapping) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_a_missing_trigger_is_indeterminate if {
	profile(data.fixtures.missing_trigger) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_an_invalid_trigger_shape_is_indeterminate if {
	profile(data.fixtures.invalid_trigger) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_an_empty_trigger_list_is_indeterminate if {
	profile(data.fixtures.empty_trigger) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_missing_jobs_are_indeterminate if {
	profile(data.fixtures.invalid_jobs_missing) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_an_empty_jobs_mapping_is_indeterminate if {
	profile(data.fixtures.invalid_jobs_empty) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_a_job_that_is_not_a_mapping_is_indeterminate if {
	profile(data.fixtures.invalid_job_entry) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_an_unreadable_workflow_concurrency_is_indeterminate if {
	profile(data.fixtures.invalid_workflow_concurrency) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_a_workflow_concurrency_without_a_group_is_indeterminate if {
	profile(data.fixtures.invalid_workflow_concurrency_group) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_an_unreadable_job_concurrency_is_indeterminate if {
	profile(data.fixtures.invalid_job_concurrency) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# One workflow's unreadable job blocks are one problem with one fix, reported
# once and naming every job the reader could not classify.
test_unreadable_job_concurrency_is_one_finding_per_workflow if {
	found := findings(data.fixtures.invalid_job_concurrency_two_jobs)
	count(found) == 1
	some f in found
	contains(f.msg, "alpha, beta")
}

test_an_unreadable_cancel_in_progress_type_is_indeterminate if {
	profile(data.fixtures.invalid_cancel_in_progress) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# The string shorthand sets no cancel-in-progress value, so GitHub queues the
# superseded run rather than cancelling it.
test_a_string_shorthand_does_not_cancel if {
	profile(data.fixtures.string_shorthand) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_literal_false_expression_does_not_cancel if {
	profile(data.fixtures.literal_false_expression) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# Only `true` and an expression can cancel; a value GitHub does not accept as
# either is not read as cancelling.
test_an_unrecognized_cancel_value_does_not_cancel if {
	profile(data.fixtures.unrecognized_cancel_value) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_trimmed_case_insensitive_true_cancels if {
	count(findings(data.fixtures.case_insensitive_true)) == 0
}

test_a_disabled_job_needs_no_concurrency if {
	count(findings(data.fixtures.disabled_job_only)) == 0
}

test_a_job_disabled_by_a_literal_false_expression_needs_no_concurrency if {
	count(findings(data.fixtures.literal_false_if)) == 0
}

# Variants of the reference shape are derived by patching it, so a change to
# the reference fixture reaches every variant.
test_the_reference_shape_stops_cancelling_when_patched_to_false if {
	fixture := json.patch(data.fixtures.compliant_reference, [{
		"op": "replace",
		"path": "/workflows/0/parsed/concurrency/cancel-in-progress",
		"value": false,
	}])
	profile(fixture) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_the_reference_group_stops_naming_the_pull_request_when_patched if {
	fixture := json.patch(data.fixtures.compliant_reference, [{
		"op": "replace",
		"path": "/workflows/0/parsed/concurrency/group",
		"value": "ci",
	}])
	profile(fixture) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_an_unknown_schema_version_is_an_error if {
	profile(data.fixtures.unknown_schema_version) == {["EN-001", "error", "indeterminate", ".github/workflows"]}
}

test_a_foreign_envelope_kind_is_an_error if {
	profile(data.fixtures.wrong_kind) == {["EN-001", "error", "indeterminate", ".github/workflows"]}
}

test_a_repository_field_that_is_not_a_mapping_is_an_error if {
	profile(data.fixtures.repository_not_object) == {["EN-001", "error", "indeterminate", ".github/workflows"]}
}

test_a_workflow_entry_that_is_not_a_mapping_is_indeterminate if {
	profile(data.fixtures.workflow_entry_not_object) == {["EN-001", "warning", "indeterminate", ".github/workflows"]}
}

# A workflow fact without a path is still audited; only the location in the
# report falls back to the workflow directory.
test_a_workflow_without_a_path_is_still_audited if {
	profile(data.fixtures.workflow_without_path) == {["RC-001", "warning", "noncompliant", ".github/workflows"]}
}

test_an_exemption_suppresses_rc001 if {
	count(findings_with(data.fixtures.missing_workflow_concurrency, parameters(exemption))) == 0
}

test_an_exemption_suppresses_rc002 if {
	count(findings_with(data.fixtures.missing_group_pull_request, parameters(exemption))) == 0
}

test_a_repository_exemption_suppresses_rc001 if {
	count(findings_with(data.fixtures.missing_workflow_concurrency, repository_parameters(repository_exemption))) == 0
}

test_an_exemption_with_a_blank_rationale_suppresses_nothing if {
	profile_with(data.fixtures.missing_workflow_concurrency, parameters(blank_rationale)) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_an_exemption_with_a_blank_review_trigger_suppresses_nothing if {
	profile_with(data.fixtures.missing_workflow_concurrency, parameters(blank_review_trigger)) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_an_exemption_with_a_blank_repository_suppresses_nothing if {
	profile_with(data.fixtures.missing_workflow_concurrency, parameters(blank_repository)) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_an_exemption_for_another_repository_suppresses_nothing if {
	profile_with(data.fixtures.missing_workflow_concurrency, parameters(other_repository)) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# An exemption is an agreement about noncompliance. Evidence the reader could
# not establish is not covered by it.
test_an_exemption_does_not_suppress_indeterminate_evidence if {
	profile_with(data.fixtures.invalid_jobs_missing, parameters(exemption)) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_a_repository_exemption_does_not_suppress_indeterminate_evidence if {
	profile_with(data.fixtures.invalid_workflow_concurrency, repository_parameters(repository_exemption)) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_the_severity_parameter_raises_noncompliance_and_indeterminate_findings if {
	raised := {"severity": "error", "exemptions": [], "repository_exemptions": []}
	profile_with(data.fixtures.missing_workflow_concurrency, raised) == {["RC-001", "error", "noncompliant", ".github/workflows/ci.yml"]}
	profile_with(data.fixtures.malformed_yaml, raised) == {["EN-001", "error", "indeterminate", ".github/workflows/ci.yml"]}
}

test_an_envelope_defect_is_an_error_whatever_the_configured_severity if {
	raised := {"severity": "warning", "exemptions": [], "repository_exemptions": []}
	profile_with(data.fixtures.unknown_schema_version, raised) == {["EN-001", "error", "indeterminate", ".github/workflows"]}
}