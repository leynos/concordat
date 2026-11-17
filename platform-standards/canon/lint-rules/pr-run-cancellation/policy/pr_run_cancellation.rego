# RC-001, RC-002: pull-request workflows cancel superseded runs.
#
# A pull request that receives several pushes starts a run per push against
# the same ref. Without concurrency cancellation the earlier runs keep the
# runners busy on a commit nobody will merge, and the newest run queues behind
# them. RC-001 requires a cancelling concurrency block: at workflow level, or
# on every job that is not disabled with `if: false`. RC-002 requires every
# cancelling block to name the pull request in its group, so two pull requests
# do not cancel each other's runs.
#
# A workflow this policy cannot decode, whose trigger it cannot classify, or
# whose concurrency shape it cannot read is indeterminate rather than a pass.
# An exemption suppresses RC-001 and RC-002 only: evidence the reader could
# not establish stays reported.
#
# The ticket this package implements calls these clauses PR-001 and PR-002.
# The canon already uses the PR prefix for the priority checks, so the
# findings here are RC-001 and RC-002; the package README records the mapping.
package canon.lint_rules.pr_run_cancellation

import rego.v1

ENVELOPE_KIND := "policy-input/pr-run-cancellation"

API_EVENTS := {"pull_request", "pull_request_target"}

PR_NUMBER_MARKERS := {"github.event.pull_request.number", "github.head_ref"}

finding(rule_id, severity, verdict, path, line, message) := {
	"rule_id": rule_id,
	"severity": severity,
	"verdict": verdict,
	"path": path,
	"line": line,
	"msg": message,
}

default severity := "warning"

severity := data.parameters.severity

default exemptions := []

exemptions := data.parameters.exemptions

default repository_exemptions := []

repository_exemptions := data.parameters.repository_exemptions

default repository_name := null

repository_name := name if {
	is_object(input.repository)
	name := object.get(input.repository, "name", null)
}

repository_identity_usable if {
	is_string(repository_name)
	trim_space(repository_name) != ""
}

envelope_ok if {
	input.schema_version == 1
	input.kind == ENVELOPE_KIND
	is_object(input.repository)
	is_array(input.workflows)
}

# --------------------------------------------------------------------------
# Workflow shape
# --------------------------------------------------------------------------

workflow_path(workflow) := path if {
	is_object(workflow)
	path := object.get(workflow, "path", "")
	is_string(path)
	path != ""
}

workflow_path(workflow) := ".github/workflows" if not is_object(workflow)

workflow_path(workflow) := ".github/workflows" if {
	is_object(workflow)
	not is_string(object.get(workflow, "path", null))
}

workflow_path(workflow) := ".github/workflows" if {
	is_object(workflow)
	object.get(workflow, "path", "") == ""
}

workflow_parsed(workflow) := parsed if {
	is_object(workflow)
	parsed := object.get(workflow, "parsed", null)
	is_object(parsed)
}

# GitHub spells the trigger key `on`. A YAML 1.1 loader resolves that
# unquoted key to the boolean `True`, which JSON renders as the key "true",
# so a reader that knows only the string key finds no triggers at all and
# every trigger-derived clause below passes over an empty set. Read both.
workflow_trigger_key(workflow) := "on" if {
	parsed := workflow_parsed(workflow)
	"on" in object.keys(parsed)
}

workflow_trigger_key(workflow) := "true" if {
	parsed := workflow_parsed(workflow)
	not "on" in object.keys(parsed)
	"true" in object.keys(parsed)
}

workflow_on(workflow) := on if {
	parsed := workflow_parsed(workflow)
	key := workflow_trigger_key(workflow)
	on := parsed[key]
}

valid_trigger(workflow) if {
	on := workflow_on(workflow)
	is_string(on)
	trim_space(on) != ""
}

valid_trigger(workflow) if {
	on := workflow_on(workflow)
	is_array(on)
	count(on) > 0
	every event in on {
		is_string(event)
	}
}

# Only the event names matter to this rule, so a mapping is classifiable
# whenever its keys are. The per-event settings are the workflow's business
# and may take any shape GitHub accepts.
valid_trigger(workflow) if {
	on := workflow_on(workflow)
	is_object(on)
	count(on) > 0
	every event in object.keys(on) {
		is_string(event)
	}
}

has_pr_trigger(workflow) if {
	on := workflow_on(workflow)
	is_string(on)
	on in API_EVENTS
}

has_pr_trigger(workflow) if {
	on := workflow_on(workflow)
	is_array(on)
	some event in API_EVENTS
	event in on
}

has_pr_trigger(workflow) if {
	on := workflow_on(workflow)
	is_object(on)
	some event in API_EVENTS
	event in object.keys(on)
}

unsupported_workflow(workflow) if not is_object(workflow)

unsupported_workflow(workflow) if {
	is_object(workflow)
	object.get(workflow, "error", null) != null
}

unsupported_workflow(workflow) if {
	is_object(workflow)
	object.get(workflow, "error", null) == null
	not is_object(object.get(workflow, "parsed", null))
}

unsupported_workflow(workflow) if {
	workflow_parsed(workflow)
	not workflow_trigger_key(workflow)
}

unsupported_workflow(workflow) if {
	workflow_parsed(workflow)
	workflow_trigger_key(workflow)
	not valid_trigger(workflow)
}

# A workflow the policy can classify as pull-request-triggered, with a
# decodable document, is the only one whose jobs and concurrency it judges.
evaluable_workflow(workflow) if {
	not unsupported_workflow(workflow)
	has_pr_trigger(workflow)
}

workflow_jobs(workflow) := jobs if {
	parsed := workflow_parsed(workflow)
	jobs := object.get(parsed, "jobs", null)
	is_object(jobs)
}

valid_jobs(workflow) if {
	jobs := workflow_jobs(workflow)
	count(jobs) > 0
	every name in object.keys(jobs) {
		is_object(jobs[name])
	}
}

# --------------------------------------------------------------------------
# Concurrency shape and cancellation
# --------------------------------------------------------------------------

workflow_has_concurrency(workflow) if {
	parsed := workflow_parsed(workflow)
	"concurrency" in object.keys(parsed)
}

workflow_concurrency(workflow) := value if {
	parsed := workflow_parsed(workflow)
	"concurrency" in object.keys(parsed)
	value := parsed.concurrency
}

job_has_concurrency(job) if {
	is_object(job)
	"concurrency" in object.keys(job)
}

job_concurrency(job) := value if {
	job_has_concurrency(job)
	value := job.concurrency
}

valid_cancel_in_progress(value) if is_boolean(value)

valid_cancel_in_progress(value) if is_string(value)

cancel_in_progress_shape_ok(value) if {
	not "cancel-in-progress" in object.keys(value)
}

cancel_in_progress_shape_ok(value) if {
	"cancel-in-progress" in object.keys(value)
	valid_cancel_in_progress(value["cancel-in-progress"])
}

valid_concurrency(value) if {
	is_string(value)
	trim_space(value) != ""
}

valid_concurrency(value) if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	trim_space(group) != ""
	cancel_in_progress_shape_ok(value)
}

is_expression(value) if {
	is_string(value)
	trimmed := trim_space(value)
	startswith(trimmed, "${{")
	endswith(trimmed, "}}")
}

# A trimmed literal-false expression is the one expression that provably does
# not cancel. Anything else written as an expression may evaluate true on the
# run it matters for, so it is treated as cancelling.
literal_false_expression(value) if {
	is_expression(value)
	trimmed := trim_space(value)
	inner := trim_space(trim_suffix(trim_prefix(trimmed, "${{"), "}}"))
	lower(inner) == "false"
}

cancels_in_progress(value) if value == true

cancels_in_progress(value) if {
	is_string(value)
	lower(trim_space(value)) == "true"
}

cancels_in_progress(value) if {
	is_expression(value)
	not literal_false_expression(value)
}

concurrency_cancels(value) if {
	is_object(value)
	"cancel-in-progress" in object.keys(value)
	cancels_in_progress(value["cancel-in-progress"])
}

workflow_cancels(workflow) if {
	workflow_has_concurrency(workflow)
	concurrency_cancels(workflow_concurrency(workflow))
}

job_cancels(job) if {
	job_has_concurrency(job)
	concurrency_cancels(job_concurrency(job))
}

# The marker only names the pull request when GitHub evaluates it. A group
# that spells the marker as literal text, such as `ci-github.head_ref`, is a
# constant, so every pull request shares it and cancels the others. Match the
# marker only inside the unquoted part of a `${{ ... }}` expression; inside
# quotes it is a string literal, as in `${{ 'github.head_ref' }}`.
#
# GitHub expressions quote strings with single quotes, so splitting an
# expression on `'` puts the evaluated text at even indices and the literals
# at odd ones.
group_names_pull_request(value) if {
	is_object(value)
	group := object.get(value, "group", null)
	is_string(group)
	some marker in PR_NUMBER_MARKERS
	some match in regex.find_all_string_submatch_n(`\$\{\{([^}]*)\}\}`, group, -1)
	body := match[1]
	unquoted := concat("", [part |
		some index, part in split(body, "'")
		index % 2 == 0
	])
	contains(lower(unquoted), marker)
}

job_disabled(job) if {
	is_object(job)
	object.get(job, "if", null) == false
}

job_disabled(job) if {
	is_object(job)
	condition := object.get(job, "if", null)
	is_string(condition)
	lower(trim_space(condition)) == "false"
}

job_disabled(job) if {
	is_object(job)
	literal_false_expression(object.get(job, "if", null))
}

invalid_workflow_concurrency(workflow) if {
	workflow_has_concurrency(workflow)
	not valid_concurrency(workflow_concurrency(workflow))
}

invalid_job_concurrency(job) if {
	job_has_concurrency(job)
	not valid_concurrency(job_concurrency(job))
}

# RC-001 and RC-002 are only stated when every concurrency block in the
# workflow can be classified. Reporting noncompliance from a shape the policy
# could not read would turn an unknown into a defect.
concurrency_evaluable(workflow) if {
	valid_jobs(workflow)
	not invalid_workflow_concurrency(workflow)
	jobs := workflow_jobs(workflow)
	every name in object.keys(jobs) {
		not invalid_job_concurrency(jobs[name])
	}
}

uncovered_jobs(workflow) := {name |
	some name in object.keys(workflow_jobs(workflow))
	job := workflow_jobs(workflow)[name]
	not job_disabled(job)
	not workflow_cancels(workflow)
	not job_cancels(job)
} if {
	concurrency_evaluable(workflow)
}

# --------------------------------------------------------------------------
# Exemptions
# --------------------------------------------------------------------------

# An exemption is a reviewed record: it names the repository, the exact
# workflow, why it may leave superseded runs running, and what event sends it
# back for review. A blank field asks nobody to revisit it, so it is not a
# record and suppresses nothing.
is_exempt(path) if {
	repository_identity_usable
	some exemption in exemptions
	is_object(exemption)
	repository := object.get(exemption, "repository", null)
	is_string(repository)
	trim_space(repository) != ""
	repository == repository_name
	exempted_path := object.get(exemption, "path", null)
	is_string(exempted_path)
	exempted_path == path
	rationale := object.get(exemption, "rationale", null)
	is_string(rationale)
	trim_space(rationale) != ""
	review_trigger := object.get(exemption, "review_trigger", null)
	is_string(review_trigger)
	trim_space(review_trigger) != ""
}

is_repository_exempt if {
	repository_identity_usable
	some exemption in repository_exemptions
	is_object(exemption)
	repository := object.get(exemption, "repository", null)
	is_string(repository)
	trim_space(repository) != ""
	repository == repository_name
	rationale := object.get(exemption, "rationale", null)
	is_string(rationale)
	trim_space(rationale) != ""
	review_trigger := object.get(exemption, "review_trigger", null)
	is_string(review_trigger)
	trim_space(review_trigger) != ""
}

rc_audited(workflow) if {
	not is_repository_exempt
	not is_exempt(workflow_path(workflow))
}

# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

deny contains finding("EN-001", "error", "indeterminate", ".github/workflows", 0, msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/pr-run-cancellation envelope at schema version 1"
}

deny contains finding("EN-001", severity, "indeterminate", ".github/workflows", 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	not is_object(workflow)
	msg := "a workflow entry is not an object"
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	is_object(workflow)
	error := object.get(workflow, "error", null)
	error != null
	msg := sprintf("the workflow could not be read: %v", [error])
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	is_object(workflow)
	object.get(workflow, "error", null) == null
	not is_object(object.get(workflow, "parsed", null))
	msg := "the workflow document is not a mapping"
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	workflow_parsed(workflow)
	not workflow_trigger_key(workflow)
	msg := "the workflow declares no triggers"
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	workflow_parsed(workflow)
	workflow_trigger_key(workflow)
	not valid_trigger(workflow)
	msg := "the workflow's triggers have a shape this rule cannot classify"
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	not valid_jobs(workflow)
	msg := "the workflow does not declare a non-empty jobs mapping of job objects, so its pull-request jobs cannot be evaluated"
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	invalid_workflow_concurrency(workflow)
	msg := "the workflow's concurrency block is not a non-empty string or an object with a string group and a boolean or string cancel-in-progress"
}

deny contains finding("EN-001", severity, "indeterminate", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	jobs := workflow_jobs(workflow)
	invalid := {name |
		some name in object.keys(jobs)
		invalid_job_concurrency(jobs[name])
	}
	count(invalid) > 0
	msg := sprintf("job(s) %s have a concurrency block that is not a non-empty string or an object with a string group and a boolean or string cancel-in-progress", [concat(", ", sort(invalid))])
}

deny contains finding("RC-001", severity, "noncompliant", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	rc_audited(workflow)
	names := uncovered_jobs(workflow)
	count(names) > 0
	msg := sprintf(
		"workflow %s runs on pull requests but neither it nor job(s) %s cancel superseded runs. Add a cancelling concurrency block at workflow level, or to every job that is not disabled with `if: false`",
		[workflow_path(workflow), concat(", ", sort(names))],
	)
}

deny contains finding("RC-002", severity, "noncompliant", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	rc_audited(workflow)
	workflow_cancels(workflow)
	not group_names_pull_request(workflow_concurrency(workflow))
	msg := sprintf(
		"workflow %s cancels superseded runs, but its concurrency group does not name the pull request. Include github.event.pull_request.number or github.head_ref so runs for different pull requests do not cancel one another",
		[workflow_path(workflow)],
	)
}

# GitHub applies workflow-level and job-level concurrency as separate controls,
# so a workflow group that names the pull request does not stop a job group
# from cancelling another pull request's run. Every cancelling job block is
# therefore checked on its own.
deny contains finding("RC-002", severity, "noncompliant", workflow_path(workflow), 0, msg) if {
	envelope_ok
	some workflow in input.workflows
	evaluable_workflow(workflow)
	concurrency_evaluable(workflow)
	rc_audited(workflow)
	jobs := workflow_jobs(workflow)
	some name in object.keys(jobs)
	job := jobs[name]
	job_cancels(job)
	not group_names_pull_request(job_concurrency(job))
	msg := sprintf(
		"job %s of %s cancels superseded runs, but its concurrency group does not name the pull request. Include github.event.pull_request.number or github.head_ref so runs for different pull requests do not cancel one another",
		[name, workflow_path(workflow)],
	)
}