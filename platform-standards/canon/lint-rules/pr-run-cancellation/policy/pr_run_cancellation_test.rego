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
	found := [f | some f in findings(data.fixtures.job_group_missing_pull_request); f.rule_id == "RC-002"]
	count(found) == 1
	some f in found
	contains(f.msg, "job bad of")
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

# A brace inside a quoted string does not close the expression.
test_a_format_expression_with_quoted_braces_is_compliant if {
	count(findings(data.fixtures.format_group_with_braces)) == 0
}

# A group unique to each run never supersedes anything, so the block does not
# cancel even though it sets cancel-in-progress.
test_a_run_specific_group_is_noncompliant if {
	profile(data.fixtures.run_id_group) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_quoted_run_id_literal_does_not_destabilise_the_group if {
	count(findings(data.fixtures.quoted_run_id_literal)) == 0
}

# The merge commit of a pull-request run changes with every push, so the next
# push's run never joins the previous group.
test_a_sha_group_is_noncompliant if {
	profile(data.fixtures.sha_group) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_head_sha_group_is_noncompliant if {
	profile(data.fixtures.head_sha_group) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A re-run's attempt number differs from the next push's first attempt, so a
# superseded re-run is left holding a slot.
test_a_run_attempt_group_does_not_cancel_a_superseded_rerun if {
	profile(data.fixtures.run_attempt_group) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A comparison of a run-varying reference renders a boolean that still changes
# between runs: `github.run_attempt == '1'` is true on a first attempt and
# false on a re-run, so the two attempts build different groups.
test_a_comparison_of_a_run_varying_reference_destabilises_the_group if {
	profile(data.fixtures.run_attempt_comparison_group) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# GitHub documents this shape for cancelling pull-request runs without
# cancelling push runs: the pull-request branch is selected, and the run-id
# fallback applies only to pushes, which the rule does not audit.
test_a_run_id_fallback_after_the_pull_request_branch_is_compliant if {
	count(findings(data.fixtures.head_ref_run_id_fallback)) == 0
}

test_a_run_id_before_the_pull_request_branch_does_not_cancel if {
	profile(data.fixtures.run_id_before_head_ref) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# Both branches of `github.run_id || github.sha` change between runs, so the
# first is always selected and the second only when the first is falsy. The
# group cannot be read as cancelling or as varying, and the unclassifiable
# branch has to win outright: letting the unstable branch also answer made Rego
# refuse the evaluation and the whole audit abort.
test_two_run_varying_fallback_branches_are_indeterminate if {
	profile(data.fixtures.two_run_varying_fallback_branches) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# The same unreadable group keeps its indeterminate verdict when it belongs to
# a job that runs: the job's concurrency can neither be confirmed nor excused.
test_an_enabled_job_with_an_unreadable_group_is_indeterminate if {
	profile(data.fixtures.enabled_job_unclassifiable_group) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# A block that provably never cancels is noncompliant whatever its group says.
# `cancel-in-progress: false` with a run-varying fallback leaves every
# superseded run running, so the unreadable group cannot excuse it.
test_a_non_cancelling_block_with_an_unreadable_group_is_noncompliant if {
	profile(data.fixtures.non_cancelling_unclassifiable_group) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A comparison renders as a boolean, so every pull request produces the same
# group even though the marker is evaluated.
test_a_compared_marker_does_not_name_the_pull_request if {
	profile(data.fixtures.compared_marker_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_reversed_comparison_does_not_name_the_pull_request if {
	profile(data.fixtures.reversed_comparison_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_parenthesized_comparison_does_not_name_the_pull_request if {
	profile(data.fixtures.parenthesized_comparison_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# Parentheses around the operand do not change the boolean the comparison
# produces.
test_a_parenthesized_operand_comparison_does_not_name_the_pull_request if {
	profile(data.fixtures.parenthesized_operand_comparison_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# An operator applied to a group replaces the value the group computed, just
# as it does for a call: `(github.head_ref || 'fallback') == 'release'`
# renders a boolean for every pull request.
test_a_comparison_outside_a_group_does_not_name_the_pull_request if {
	profile(data.fixtures.grouped_fallback_comparison) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# `&&` yields its right operand, so a grouped pull-request reference on the
# left selects the constant after it.
test_a_conjunction_outside_a_group_does_not_name_the_pull_request if {
	profile(data.fixtures.grouped_conjunction_constant) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# One evaluated occurrence is enough: the second occurrence is a comparison,
# but the first supplies the head ref the group carries.
test_a_compared_second_use_does_not_disqualify_the_first if {
	count(findings(data.fixtures.format_with_compared_second_use)) == 0
}

# `github['run_id']` resolves to the same property as `github.run_id`, so the
# group is per-run however the reference is spelled.
test_an_indexed_run_id_group_is_noncompliant if {
	profile(data.fixtures.indexed_run_id_group) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_an_indexed_pull_request_reference_is_compliant if {
	count(findings(data.fixtures.indexed_reference_group)) == 0
}

# `contains` returns a boolean rather than the ref, so the group is constant
# across pull requests.
test_a_marker_passed_to_a_boolean_function_does_not_name_the_pull_request if {
	profile(data.fixtures.contains_marker_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A call the rule does not know may or may not preserve the identity.
test_a_marker_passed_to_an_unknown_function_is_indeterminate if {
	profile(data.fixtures.unknown_function_group) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_an_unknown_function_on_a_job_is_reported_once_per_workflow if {
	found := findings(data.fixtures.unknown_function_jobs)
	count(found) == 1
	some f in found
	f.rule_id == "EN-001"
	contains(f.msg, "test")
}

# `join`'s separator only renders between elements, so whether the marker
# reaches the group depends on the array's length.
test_a_marker_in_a_join_separator_is_indeterminate if {
	profile(data.fixtures.join_separator_group) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_a_marker_in_a_join_array_names_the_pull_request if {
	count(findings(data.fixtures.join_array_group)) == 0
}

# Both occurrences are arguments of the same call, so the second one is just as
# much the compared value as the first.
test_a_repeated_marker_in_a_boolean_call_does_not_name_the_pull_request if {
	profile(data.fixtures.repeated_marker_in_boolean_call) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# The inner call carries the value, but the outer call discards it.
test_an_outer_boolean_call_defeats_an_inner_identity_call if {
	profile(data.fixtures.boolean_call_around_format) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_nested_identity_calls_remain_transparent if {
	count(findings(data.fixtures.nested_identity_calls_group)) == 0
}

# `fromJSON` parses its argument instead of passing it through, so a
# JSON-literal branch name such as `1` or `1e0` changes value and two pull
# requests could share the group. The identity cannot be settled from the text.
test_a_group_parsed_by_fromjson_is_indeterminate if {
	profile(data.fixtures.fromjson_group) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# A template joined with `&&` is not one literal, and GitHub selects the right
# operand, so the placeholders the rule read are not the ones rendered.
test_a_compound_template_is_indeterminate if {
	profile(data.fixtures.compound_format_template) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# An operator applied to the call replaces its result even when the marker's
# own argument is followed by another argument's comma.
test_a_comparison_of_the_call_result_does_not_name_the_pull_request if {
	profile(data.fixtures.compared_format_result) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_the_call_result_compared_from_the_left_does_not_name_the_pull_request if {
	profile(data.fixtures.format_compared_from_the_left) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_negated_call_does_not_name_the_pull_request if {
	profile(data.fixtures.negated_format_call) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_call_as_the_left_operand_of_a_conjunction_does_not_name_the_pull_request if {
	profile(data.fixtures.conjunctive_format_call) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A template that renders no replacement index drops its arguments, so the
# group is constant across pull requests.
test_a_format_template_that_omits_the_marker_does_not_name_the_pull_request if {
	profile(data.fixtures.format_omitting_the_marker) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# `{1}` renders the second replacement argument, not the marker's first one.
test_a_placeholder_for_another_argument_does_not_name_the_pull_request if {
	profile(data.fixtures.format_placeholder_for_another_argument) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A template the rule cannot read may or may not render the marker.
test_a_computed_format_template_is_indeterminate if {
	profile(data.fixtures.format_with_a_computed_template) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# `!` renders a boolean, so the group is the same for every pull request.
test_a_negated_marker_does_not_name_the_pull_request if {
	profile(data.fixtures.negated_marker_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_negated_argument_of_format_does_not_name_the_pull_request if {
	profile(data.fixtures.negated_argument_of_format) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# `&&` yields its right operand, so a marker as the left operand is not the
# group's value, whether or not a call carries it.
test_a_marker_as_the_left_operand_of_and_does_not_name_the_pull_request if {
	profile(data.fixtures.conjunction_left_marker_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_carried_marker_defeated_by_and_does_not_name_the_pull_request if {
	profile(data.fixtures.format_conjunction_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A fallback in an earlier argument does not reach a marker in a later one, so
# the group still carries the pull request.
test_a_fallback_in_a_previous_argument_leaves_a_later_marker_selected if {
	count(findings(data.fixtures.fallback_in_a_previous_argument)) == 0
}

# A fallback inside a call that has already closed cannot reach the argument
# the marker occupies.
test_a_fallback_in_a_closed_nested_call_leaves_the_marker_selected if {
	count(findings(data.fixtures.fallback_in_a_closed_nested_call)) == 0
}

# The guard names `push`, which the workflow's only declared event is not, so
# every pull-request run takes the run-id fallback. A group that changes on
# every run never shares a value across the runs it should cancel.
test_an_event_test_that_excludes_every_declared_event_selects_the_fallback if {
	profile(data.fixtures.and_guard_then_run_id_fallback) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_the_exact_marker_head_still_ignores_the_run_id_fallback if {
	count(findings(data.fixtures.head_ref_run_id_fallback)) == 0
}

# The comparison renders a boolean and the outer `format` renders that boolean,
# so no argument of either call carries the pull request's identity.
test_a_comparison_on_an_inner_enclosing_call_does_not_name_the_pull_request if {
	profile(data.fixtures.nested_enclosing_comparison_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# The guard is false on a pull-request run, so GitHub selects the constant
# fallback instead, and every pull request shares that group.
test_a_push_guard_makes_the_constant_fallback_the_group if {
	profile(data.fixtures.push_event_guard_then_constant_fallback) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_reversed_push_guard_makes_the_constant_fallback_the_group if {
	profile(data.fixtures.reversed_push_event_guard_then_constant_fallback) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# Without a fallback the expression itself renders a boolean, which every pull
# request shares.
test_a_push_guard_without_a_fallback_is_a_constant_group if {
	profile(data.fixtures.push_event_guard_without_fallback) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# The guard holds on the pull-request event and `github.head_ref` is truthy, so
# the ref fallback is never selected.
test_a_pull_request_guard_keeps_the_fallback_unused if {
	count(findings(data.fixtures.pull_request_event_guard_then_ref_fallback)) == 0
}

# A guard the rule cannot evaluate may or may not select the marker, so the
# group's identity is indeterminate rather than a pass or a defect.
test_a_guard_the_rule_cannot_evaluate_is_indeterminate if {
	profile(data.fixtures.unevaluable_guard_before_marker) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# A literal-false guard makes the conjunction falsy, so the marker after it is
# never the group's value and every pull request shares the group.
test_a_literal_false_guard_leaves_the_marker_unselected if {
	profile(data.fixtures.literal_false_guard_then_marker) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A guard the rule cannot prove truthy may skip its right operand, so whether
# the group names the pull request cannot be read from the text.
test_a_guard_that_cannot_be_proved_truthy_is_indeterminate if {
	profile(data.fixtures.unknown_value_guard_then_marker) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# `github.ref` is non-empty on a pull-request run, so it always selects the
# marker after it.
test_a_ref_guard_keeps_the_marker_selected if {
	count(findings(data.fixtures.ref_guard_then_marker)) == 0
}

# `github.ref` is always non-empty, so the conjunction takes the marker after it
# and the `ci` fallback can never be selected.
test_a_proven_truthy_conjunction_makes_the_fallback_unreachable if {
	count(findings(data.fixtures.proven_truthy_conjunction_before_fallback)) == 0
}

# The same shape behind a guard the rule cannot prove truthy may skip the
# marker, so the fallback stays reachable and the identity is indeterminate.
test_an_unsettled_guard_before_a_fallback_keeps_the_identity_unknown if {
	profile(data.fixtures.unsettled_guard_before_fallback_keeps_the_identity_unknown) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# The guard names an event the workflow does not declare, so no pull-request run
# reaches the run id it guards and the marker fallback is the group's value.
test_an_event_test_that_excludes_the_declared_event_keeps_the_marker_selected if {
	count(findings(data.fixtures.run_id_branch_the_declared_event_excludes)) == 0
}

# The guard names the declared event, so every pull-request run takes the run id
# it guards and the next push never joins the group.
test_an_event_test_that_selects_the_run_id_leaves_the_group_varying if {
	profile(data.fixtures.run_id_branch_the_declared_event_selects) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# The workflow also runs on `pull_request_target`, where the `pull_request`
# guard fails and the constant fallback is selected instead.
test_a_guard_is_judged_against_each_declared_pull_request_event if {
	profile(data.fixtures.pull_request_guard_with_both_triggers) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_bare_marker_names_the_pull_request_for_every_declared_event if {
	count(findings(data.fixtures.head_ref_group_with_both_triggers)) == 0
}

# GitHub applies the two levels as separate controls, so covering each declared
# event can be split between them: the workflow block cancels `pull_request`
# runs and the job block cancels `pull_request_target` runs.
test_each_level_may_cover_a_different_declared_event if {
	count(findings(data.fixtures.combined_levels_cover_each_event)) == 0
}

# Each declared event is judged on its own, so a group can take its identity for
# one event from one body and for the other event from a second body.
test_each_event_may_take_its_identity_from_a_different_body if {
	count(findings(data.fixtures.two_bodies_each_naming_one_pr_event)) == 0
}

# The run id sits in an argument the template never renders, so the group stays
# the same across pushes to one pull request and still names it.
test_a_discarded_run_value_does_not_make_the_group_vary if {
	count(findings(data.fixtures.discarded_run_value_in_format)) == 0
}

test_a_rendered_run_value_makes_the_group_vary if {
	profile(data.fixtures.rendered_run_value_in_format) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# The argument is rendered, but the literal-false guard in front of the run id
# makes it the constant `false`: every push to the pull request shares the
# group, so the superseded run is cancelled.
test_a_run_value_behind_a_false_argument_guard_still_cancels if {
	count(findings(data.fixtures.run_value_behind_a_false_argument_guard)) == 0
}

# The same argument behind a proven-truthy guard does render the run id, so the
# next push never joins the group.
test_a_run_value_behind_a_true_argument_guard_varies_the_group if {
	profile(data.fixtures.run_value_behind_a_true_argument_guard) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A run id behind a constant-first fallback inside its own argument is never
# reached; the group is the constant `ci`, which every pull request shares.
test_a_run_value_behind_a_constant_argument_fallback_leaves_a_constant_group if {
	profile(data.fixtures.run_value_behind_a_constant_argument_fallback) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# On a pull request the guard fails and the unknown call is the selected value,
# so whether it still names the pull request cannot be read from the text.
test_an_unknown_call_in_a_reachable_fallback_is_indeterminate if {
	profile(data.fixtures.unknown_call_in_reachable_fallback) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# The unknown `fromJSON` result belongs to `pull_request_target`, where the
# block's event test stops it cancelling. Judging identity on every declared
# event let that unknown value excuse the constant `ci-false` group the
# cancelling `pull_request` run actually uses.
test_identity_is_judged_on_the_events_that_cancel if {
	profile(data.fixtures.identity_unknown_on_an_event_the_block_does_not_cancel) == {
		["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"],
		["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"],
	}
}

test_a_job_group_is_judged_on_the_events_the_job_cancels if {
	profile(data.fixtures.job_identity_unknown_on_an_event_the_job_does_not_cancel) == {
		["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"],
		["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"],
	}
}

# The block cancels both declared events. On `pull_request` the event guard
# fails and the group is the definite constant `ci-false`; on
# `pull_request_target` the `fromJSON` result cannot be read. The unreadable
# event keeps its own indeterminate finding without suppressing the definite
# defect the other event proves.
test_an_unreadable_event_does_not_suppress_a_definite_identity_failure if {
	profile(data.fixtures.identity_unknown_for_one_event) == {
		["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"],
		["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"],
	}
}

# A bare marker is always truthy, so the fallback after it is never selected.
test_an_unknown_call_in_an_unreachable_fallback_is_compliant if {
	count(findings(data.fixtures.head_ref_before_unknown_fallback)) == 0
}

# The literal `false` in the middle of the chain stops it, so the marker after
# it never becomes the group's value.
test_a_falsy_guard_later_in_the_chain_leaves_the_marker_unselected if {
	profile(data.fixtures.conjunction_chain_then_marker) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A `||` inside a call argument is that argument's own fallback, so the run id
# beside it is part of the group however the branches are ordered. Whether it
# is selected for a pull request cannot be read from the text, so the evidence
# is indeterminate rather than a pass or a defect.
test_a_run_value_beside_a_nested_fallback_is_indeterminate if {
	profile(data.fixtures.nested_format_fallback_group) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_a_nested_fallback_on_a_job_is_reported_once_per_workflow if {
	found := findings(data.fixtures.nested_format_fallback_jobs)
	count(found) == 1
	some f in found
	f.rule_id == "EN-001"
	contains(f.msg, "test")
}

test_a_nested_fallback_is_fine_when_nothing_beside_it_varies if {
	count(findings(data.fixtures.nested_fallback_without_run_value)) == 0
}

# The nested fallback is only reached when the constant is falsy, which a pull
# request never supplies, so the group is constant.
test_a_marker_only_in_a_nested_fallback_does_not_name_the_pull_request if {
	profile(data.fixtures.marker_only_in_nested_fallback) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_run_value_in_an_unselected_fallback_is_compliant if {
	count(findings(data.fixtures.nested_run_value_in_unselected_fallback)) == 0
}

# A `||` whose own left branch is provably falsy does not cut the marker off:
# GitHub moves on to the next branch, so the group still names the pull request.
test_a_provably_falsy_branch_does_not_cut_the_marker_off if {
	count(findings(data.fixtures.falsy_branch_before_marker_in_argument)) == 0
}

# An empty literal is falsy too, and it renders nothing, so the marker after it
# is the group's value. Erasing every literal to the same text would read this
# fallback as a constant that the marker never reaches.
test_an_empty_literal_branch_lets_the_marker_through if {
	count(findings(data.fixtures.empty_literal_branch_before_marker)) == 0
}

# `github.head_ref_name` is a different property; GitHub resolves an unknown
# property to an empty string.
test_a_property_with_a_marker_prefix_does_not_name_the_pull_request if {
	profile(data.fixtures.marker_prefix_property_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# Expression whitespace may include newlines, on either side of the operator.
test_a_multiline_comparison_does_not_name_the_pull_request if {
	profile(data.fixtures.multiline_comparison_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_reversed_multiline_comparison_does_not_name_the_pull_request if {
	profile(data.fixtures.reversed_multiline_comparison_group) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# The constant first branch is the one GitHub selects, so a marker that appears
# only in the fallback is part of a constant group.
test_a_marker_only_in_a_constant_first_fallback_is_noncompliant if {
	profile(data.fixtures.constant_first_fallback) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A guard in an enclosing argument decides whether the inner call is evaluated,
# so a literal `false` there leaves the marker unrendered however deeply it is
# nested.
test_an_enclosing_argument_guard_with_a_literal_false_leaves_the_marker_unrendered if {
	profile(data.fixtures.outer_falsy_guard_around_inner_format) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# `github.ref` is non-empty on a pull-request run, so the enclosing guard always
# selects the inner call and the marker it renders.
test_an_enclosing_argument_guard_with_a_truthy_value_keeps_the_marker if {
	count(findings(data.fixtures.outer_truthy_guard_around_inner_format)) == 0
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

# A `cancel-in-progress` guard is evaluated on the run GitHub sends, so a guard
# that selects only `pull_request` never cancels a `pull_request_target` run of
# the same workflow.
test_an_event_guard_that_excludes_a_declared_event_does_not_cancel if {
	profile(data.fixtures.target_only_with_pull_request_guard) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_an_event_guard_covering_every_declared_event_cancels if {
	count(findings(data.fixtures.two_event_guard_with_both_triggers)) == 0
}

test_a_guard_on_an_undeclared_event_does_not_cancel if {
	profile(data.fixtures.push_guard_on_pull_request_workflow) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# `&&` short-circuits, so an event guard that excludes the declared event makes
# the whole expression false whatever follows it. The superseded run it would
# have cancelled is left alone.
test_a_provably_false_expression_does_not_cancel if {
	profile(data.fixtures.provably_false_cancel_expression) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A `||` chain is false only when every branch is, and both event tests here
# exclude the only declared event.
test_a_disjunction_that_excludes_every_declared_event_does_not_cancel if {
	profile(data.fixtures.provably_false_cancel_disjunction) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# A bare event test is a `||` branch in its own right, so it is false on an
# event it does not name. `github.event_name == 'push' || false` renders
# `false` on a `pull_request` run and never cancels it.
test_a_bare_event_test_beside_a_falsy_branch_does_not_cancel if {
	profile(data.fixtures.bare_event_test_or_false) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# The same test beside a truthy branch may still render true, so it keeps the
# conservative reading and is not reported.
test_a_bare_event_test_beside_a_truthy_branch_cancels if {
	count(findings(data.fixtures.bare_event_test_or_true)) == 0
}

# GitHub compares strings case-insensitively, so `!= 'PULL_REQUEST'` is false
# on a `pull_request` run however the name is spelled.
test_an_event_test_with_an_uppercase_name_does_not_cancel if {
	profile(data.fixtures.uppercase_event_test) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_a_guard_that_holds_for_the_declared_event_cancels if {
	count(findings(data.fixtures.truthy_guard_cancel_expression)) == 0
}

# An expression the rule cannot prove false keeps the conservative reading: it
# is treated as cancelling rather than reported as a defect the text does not
# establish. The package README records this limitation.
test_an_unsettled_expression_keeps_the_conservative_reading if {
	count(findings(data.fixtures.unsettled_cancel_expression_stays_conservative)) == 0
}

test_an_event_test_with_the_event_name_first_cancels if {
	count(findings(data.fixtures.reversed_event_test_cancels)) == 0
}

test_a_job_event_guard_is_judged_against_the_declared_events if {
	profile(data.fixtures.job_push_guard_does_not_cancel) == {["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

# `github.event_name == 'pull_request' && false && github.head_ref` renders
# `false`, so the marker behind the falsy guard never reaches the group.
test_a_falsy_conjunction_behind_an_event_guard_names_nothing if {
	profile(data.fixtures.event_guard_then_falsy_conjunction) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_an_unreadable_conjunction_behind_an_event_guard_is_indeterminate if {
	profile(data.fixtures.event_guard_then_unreadable_conjunction) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

# The branch-level conjunction checks split only at the top level, so an
# argument of its own is judged by the same rules.
test_a_falsy_conjunction_inside_a_call_argument_names_nothing if {
	profile(data.fixtures.falsy_conjunction_in_argument) == {["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"]}
}

test_an_unreadable_conjunction_inside_a_call_argument_is_indeterminate if {
	profile(data.fixtures.unreadable_conjunction_in_argument) == {["EN-001", "warning", "indeterminate", ".github/workflows/ci.yml"]}
}

test_a_truthy_guard_inside_a_call_argument_still_names_the_pull_request if {
	count(findings(data.fixtures.truthy_guard_in_argument)) == 0
}

# A block that cancels only one declared event still applies to that event's
# runs, so a constant group is reported even though RC-001 also fires.
test_a_conditional_workflow_block_names_the_pull_request_on_the_events_it_cancels if {
	profile(data.fixtures.conditional_cancel_with_constant_group) == {
		["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"],
		["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"],
	}
}

test_a_conditional_job_block_names_the_pull_request_on_the_events_it_cancels if {
	profile(data.fixtures.job_conditional_cancel_with_constant_group) == {
		["RC-001", "warning", "noncompliant", ".github/workflows/ci.yml"],
		["RC-002", "warning", "noncompliant", ".github/workflows/ci.yml"],
	}
}

test_a_disabled_job_needs_no_concurrency if {
	count(findings(data.fixtures.disabled_job_only)) == 0
}

test_a_job_disabled_by_a_literal_false_expression_needs_no_concurrency if {
	count(findings(data.fixtures.literal_false_if)) == 0
}

# A disabled job never runs, so its shared group cannot cancel another pull
# request's run even though the group is a constant.
test_a_disabled_job_with_a_constant_group_is_compliant if {
	count(findings(data.fixtures.disabled_job_constant_group)) == 0
}

# A group the rule cannot classify stops it judging the blocks that do run, so
# an unreadable group on a disabled job must not excuse the enabled job that has
# no concurrency block at all.
test_a_disabled_job_with_an_unreadable_group_does_not_suppress_rc_001 if {
	found := findings(data.fixtures.disabled_job_unclassifiable_group)
	count(found) == 1
	some f in found
	f.rule_id == "RC-001"
	contains(f.msg, "job(s) test")
}

# The identity of a disabled job's group matters no more than its stability:
# the job never runs, so an unreadable marker there names no run to separate.
test_a_disabled_job_with_an_unreadable_identity_is_ignored if {
	count(findings(data.fixtures.disabled_job_unknown_identity_group)) == 0
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