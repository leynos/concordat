# CV-005: Keep CodeScene coverage publication on main.
#
# The Python envelope has already decoded each workflow document. This policy
# deliberately does not interpret shell expressions or reusable workflows:
# facts that cannot establish the required topology are indeterminate rather
# than a clean result.
package canon.lint_rules.main_owned_codescene_coverage

import rego.v1

finding(verdict, path, message) := {
  "rule_id": "CV-005",
  "severity": "error",
  "verdict": verdict,
  "path": path,
  "line": 0,
  "msg": message,
}

workflows := object.get(input, "workflows", [])

envelope_ok if {
  input.schema_version == 1
  input.kind == "policy-input/main-owned-codescene-coverage"
  is_array(workflows)
}

workflow_path(workflow) := object.get(workflow, "path", ".github/workflows")

workflow_parsed(workflow) := parsed if {
  is_object(workflow)
  parsed := object.get(workflow, "parsed", null)
  is_object(parsed)
}

workflow_on(workflow) := on if {
  parsed := workflow_parsed(workflow)
  on := object.get(parsed, "on", null)
}

workflow_jobs(workflow) := jobs if {
  parsed := workflow_parsed(workflow)
  jobs := object.get(parsed, "jobs", null)
  is_object(jobs)
}

has_pr_trigger(workflow) if {
  on := workflow_on(workflow)
  is_string(on)
  on == "pull_request"
}

has_pr_trigger(workflow) if {
  on := workflow_on(workflow)
  is_array(on)
  "pull_request" in on
}

has_pr_trigger(workflow) if {
  on := workflow_on(workflow)
  is_object(on)
  "pull_request" in object.keys(on)
}

main_only_trigger(workflow) if {
  on := workflow_on(workflow)
  is_object(on)
  object.keys(on) == {"push"}
  push := on.push
  is_object(push)
  branches := object.get(push, "branches", null)
  is_array(branches)
  count(branches) == 1
  branches[0] == "main"
}

unsupported_workflow(workflow) if {
  not is_object(workflow)
}

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
  parsed := workflow_parsed(workflow)
  not "on" in object.keys(parsed)
}

unsupported_workflow(workflow) if {
  workflow_on(workflow)
  not has_pr_trigger(workflow)
  not main_only_trigger(workflow)
  on := workflow_on(workflow)
  not is_string(on)
  not is_array(on)
  not is_object(on)
}

unsupported_workflow(workflow) if {
  parsed := workflow_parsed(workflow)
  "jobs" in object.keys(parsed)
  not is_object(parsed.jobs)
}

unsupported_workflow(workflow) if {
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job := jobs[name]
  not is_object(job)
}

unsupported_workflow(workflow) if {
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job := jobs[name]
  is_object(job)
  "uses" in object.keys(job)
}

unsupported_workflow(workflow) if {
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job := jobs[name]
  is_object(job)
  "steps" in object.keys(job)
  not is_array(job.steps)
}

unsupported_workflow(workflow) if {
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job := jobs[name]
  is_object(job)
  is_array(object.get(job, "steps", null))
  some step in job.steps
  not is_object(step)
}

unsupported_workflow(workflow) if {
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job := jobs[name]
  is_object(job)
  is_array(object.get(job, "steps", null))
  some step in job.steps
  is_object(step)
  "uses" in object.keys(step)
  not is_string(step.uses)
}

unsupported_workflow(workflow) if {
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job := jobs[name]
  is_object(job)
  is_array(object.get(job, "steps", null))
  some step in job.steps
  is_object(step)
  "with" in object.keys(step)
  not is_object(step["with"])
}

workflow_steps(workflow) := {step |
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job := jobs[name]
  is_object(job)
  is_array(object.get(job, "steps", null))
  step := job.steps[_]
  is_object(step)
}

step_uses(step, action) if {
  uses := object.get(step, "uses", "")
  is_string(uses)
  contains(lower(uses), action)
}

is_codescene_step(step) if step_uses(step, "codescene")

is_coverage_step(step) if step_uses(step, "generate-coverage")

has_ratcheting_coverage(workflow) if {
  some step in workflow_steps(workflow)
  is_coverage_step(step)
  inputs := object.get(step, "with", {})
  value := object.get(inputs, "with-ratchet", null)
  value == true
}

has_ratcheting_coverage(workflow) if {
  some step in workflow_steps(workflow)
  is_coverage_step(step)
  inputs := object.get(step, "with", {})
  object.get(inputs, "with-ratchet", null) == "true"
}

has_codescene_step(workflow) if {
  some step in workflow_steps(workflow)
  is_codescene_step(step)
}

has_explicit_upload(workflow) if {
  some step in workflow_steps(workflow)
  is_codescene_step(step)
  inputs := object.get(step, "with", {})
  object.get(inputs, "mode", null) == "upload"
}

has_token(environment) if {
  is_object(environment)
  "CS_ACCESS_TOKEN" in object.keys(environment)
}

workflow_exposes_token(workflow) if {
  parsed := workflow_parsed(workflow)
  has_token(object.get(parsed, "env", {}))
}

workflow_exposes_token(workflow) if {
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job := jobs[name]
  is_object(job)
  has_token(object.get(job, "env", {}))
}

workflow_exposes_token(workflow) if {
  some step in workflow_steps(workflow)
  has_token(object.get(step, "env", {}))
}

workflow_exposes_token(workflow) if {
  some step in workflow_steps(workflow)
  is_codescene_step(step)
  inputs := object.get(step, "with", {})
  "access-token" in object.keys(inputs)
}

main_coverage_publisher(workflow) if {
  main_only_trigger(workflow)
  has_ratcheting_coverage(workflow)
  has_explicit_upload(workflow)
}

main_coverage_publisher_exists if {
  some workflow in workflows
  main_coverage_publisher(workflow)
}

deny contains f if {
  not envelope_ok
  f := finding("indeterminate", ".github/workflows", "workflow envelope has an unknown shape")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  unsupported_workflow(workflow)
  f := finding("indeterminate", workflow_path(workflow), "workflow shape cannot be evaluated safely")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  has_pr_trigger(workflow)
  not unsupported_workflow(workflow)
  has_codescene_step(workflow)
  f := finding("noncompliant", workflow_path(workflow), "pull-request workflow invokes CodeScene")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  has_pr_trigger(workflow)
  not unsupported_workflow(workflow)
  workflow_exposes_token(workflow)
  f := finding("noncompliant", workflow_path(workflow), "pull-request workflow receives CS_ACCESS_TOKEN")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  has_pr_trigger(workflow)
  not unsupported_workflow(workflow)
  not has_ratcheting_coverage(workflow)
  f := finding("noncompliant", workflow_path(workflow), "pull-request workflow lacks ratcheting coverage generation")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  has_codescene_step(workflow)
  not unsupported_workflow(workflow)
  not has_pr_trigger(workflow)
  not main_only_trigger(workflow)
  f := finding("noncompliant", workflow_path(workflow), "CodeScene publication is not restricted to a main-only workflow")
}

deny contains f if {
  envelope_ok
  not main_coverage_publisher_exists
  f := finding("noncompliant", ".github/workflows", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage")
}
