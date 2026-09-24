# CV-005: Keep CodeScene coverage publication on main.
#
# The Python envelope has already decoded each workflow document. This policy
# recognizes literal direct `cs-coverage` commands in shell scripts, including
# simple environment prefixes, but it does not interpret shell expressions,
# and it cannot read the steps a job delegates to a reusable workflow. Facts
# that cannot establish the required topology are indeterminate rather than a
# clean result, and the indeterminacy is reported as narrowly as the evidence
# allows: one job where a job is delegated, one file where a file cannot be
# decoded.
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

workflow_path(workflow) := path if {
  is_object(workflow)
  path := object.get(workflow, "path", ".github/workflows")
}

workflow_path(workflow) := ".github/workflows" if {
  not is_object(workflow)
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

main_trigger_events(events) if events == {"push"}

main_trigger_events(events) if events == {"push", "workflow_dispatch"}

main_only_trigger(workflow) if {
  on := workflow_on(workflow)
  is_object(on)
  main_trigger_events(object.keys(on))
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
  workflow_parsed(workflow)
  not workflow_trigger_key(workflow)
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

# An unreadable workflow is only relevant when it could own coverage: a
# pull-request or main-only trigger, or decoded CodeScene or coverage facts.
# Scheduled support workflows such as Dependabot automation cannot affect this
# contract and are deliberately left outside the fail-closed boundary.
unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  has_pr_trigger(workflow)
}

unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  main_only_trigger(workflow)
}

unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  has_coverage_step(workflow)
}

unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  has_codescene_step(workflow)
}

unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  workflow_exposes_token(workflow)
}

# A malformed or triggerless document cannot be classified safely, unlike a
# well-formed workflow this contract does not reach, so it remains
# indeterminate.
unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  not is_object(workflow)
}

unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  is_object(workflow)
  object.get(workflow, "error", null) != null
}

unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  is_object(workflow)
  object.get(workflow, "error", null) == null
  not is_object(object.get(workflow, "parsed", null))
}

unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  workflow_parsed(workflow)
  not workflow_trigger_key(workflow)
}

unsupported_coverage_candidate(workflow) if {
  unsupported_workflow(workflow)
  workflow_on(workflow)
  not has_pr_trigger(workflow)
  not main_only_trigger(workflow)
  on := workflow_on(workflow)
  not is_string(on)
  not is_array(on)
  not is_object(on)
}

# A reusable job delegates its steps to another workflow, which a local audit
# cannot read. That hides one job, not the document: the workflow's other jobs
# stay readable and are evaluated. The delegated job is reported on its own so
# an operator knows exactly what was not seen.
reusable_jobs(workflow) := {name |
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job := jobs[name]
  is_object(job)
  "uses" in object.keys(job)
}

coverage_relevant_workflow(workflow) if has_pr_trigger(workflow)

coverage_relevant_workflow(workflow) if main_only_trigger(workflow)

coverage_relevant_workflow(workflow) if has_coverage_step(workflow)

coverage_relevant_workflow(workflow) if has_codescene_step(workflow)

coverage_relevant_workflow(workflow) if workflow_exposes_token(workflow)

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

shell_assignment_prefix := `[A-Za-z_][A-Za-z0-9_]*=("[^"\r\n]*"|'[^'\r\n]*'|[^[:space:];|&()<>"'\\]*)[[:space:]]+`

codescene_cli_prefix := sprintf(
  `(?:%s)*(?:env[[:space:]]+)?(?:%s)*`,
  [shell_assignment_prefix, shell_assignment_prefix],
)

codescene_cli_command_pattern := sprintf(
  `(?m)(^|[\r\n;&|()])[[:space:]]*%scs-coverage[[:space:]]+(check|upload)([[:space:]]|$)`,
  [codescene_cli_prefix],
)

is_codescene_cli_step(step) if {
  run := object.get(step, "run", "")
  is_string(run)
  regex.match(codescene_cli_command_pattern, run)
}

is_codescene_step(step) if step_uses(step, "codescene")

is_codescene_step(step) if is_codescene_cli_step(step)

is_coverage_step(step) if step_uses(step, "generate-coverage")

default_rust_baseline := ".coverage-baseline.rust"

default_python_baseline := ".coverage-baseline.python"

# Omitted inputs have the shared action's defaults, so compare the effective
# pair rather than whether either workflow spells both paths explicitly.
coverage_identity(step) := {
  "rust": object.get(inputs, "baseline-rust-file", default_rust_baseline),
  "python": object.get(inputs, "baseline-python-file", default_python_baseline),
} if {
  inputs := object.get(step, "with", {})
  is_object(inputs)
}

has_coverage_step(workflow) if {
  some step in workflow_steps(workflow)
  is_coverage_step(step)
}

has_ratcheting_coverage(workflow) if {
  some step in workflow_steps(workflow)
  is_coverage_step(step)
  ratcheting_coverage_step(step)
}

ratcheting_coverage_step(step) if {
  inputs := object.get(step, "with", {})
  value := object.get(inputs, "with-ratchet", null)
  value == true
}

ratcheting_coverage_step(step) if {
  inputs := object.get(step, "with", {})
  object.get(inputs, "with-ratchet", null) == "true"
}

has_unratcheted_coverage(workflow) if {
  some step in workflow_steps(workflow)
  is_coverage_step(step)
  not ratcheting_coverage_step(step)
}

has_codescene_step(workflow) if {
  some step in workflow_steps(workflow)
  is_codescene_step(step)
}

codescene_cli_upload_pattern := sprintf(
  `(?m)(^|[\r\n;&|()])[[:space:]]*%scs-coverage[[:space:]]+upload([[:space:]]|$)`,
  [codescene_cli_prefix],
)

is_codescene_action_step(step) if step_uses(step, "codescene")

# The uploader defaults `mode` to "upload", so a step that omits the input
# does upload. Reading the effective value rather than the spelling keeps the
# rule from reporting correct wiring as broken, which would only teach people
# to edit a working workflow to satisfy the audit.
codescene_action_mode(step) := mode if {
  inputs := object.get(step, "with", {})
  is_object(inputs)
  mode := object.get(inputs, "mode", "upload")
}

is_upload_step(step) if {
  is_codescene_action_step(step)
  codescene_action_mode(step) == "upload"
}

is_upload_step(step) if {
  run := object.get(step, "run", "")
  is_string(run)
  regex.match(codescene_cli_upload_pattern, run)
}

has_explicit_upload(workflow) if {
  some step in workflow_steps(workflow)
  is_upload_step(step)
}

has_token_reference(value) if {
  some _, descendant in walk(value)
  is_string(descendant)
  contains(descendant, "CS_ACCESS_TOKEN")
}

has_token(environment) if {
  is_object(environment)
  "CS_ACCESS_TOKEN" in object.keys(environment)
}

has_token(environment) if {
  is_object(environment)
  some key in object.keys(environment)
  has_token_reference(environment[key])
}

has_token_input(inputs) if {
  is_object(inputs)
  some key in object.keys(inputs)
  has_token_reference(inputs[key])
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
  inputs := object.get(step, "with", {})
  has_token_input(inputs)
}

workflow_exposes_token(workflow) if {
  some step in workflow_steps(workflow)
  is_codescene_step(step)
  inputs := object.get(step, "with", {})
  is_object(inputs)
  "access-token" in object.keys(inputs)
}

main_coverage_publisher(workflow) if {
  main_only_trigger(workflow)
  has_ratcheting_coverage(workflow)
  has_explicit_upload(workflow)
}

qualifying_main_coverage_publisher(workflow) if {
  main_coverage_publisher(workflow)
  not unsupported_workflow(workflow)
}

coverage_identity_published(step) if {
  some workflow in workflows
  qualifying_main_coverage_publisher(workflow)
  some publisher_step in workflow_steps(workflow)
  is_coverage_step(publisher_step)
  ratcheting_coverage_step(publisher_step)
  coverage_identity(publisher_step) == coverage_identity(step)
}

has_unmatched_pr_coverage_identity(workflow) if {
  some step in workflow_steps(workflow)
  is_coverage_step(step)
  not coverage_identity_published(step)
}

main_coverage_publisher_exists if {
  some workflow in workflows
  main_coverage_publisher(workflow)
}

# --------------------------------------------------------------------------
# Clause 1: a pull-request coverage step keeps its report local.
#
# `generate-coverage` defaults `publish-artefact` to "true", so a lane that
# omits the input uploads the report as a workflow artefact. The clause is
# about the effective value, not about whether the caller spelt the input.
# --------------------------------------------------------------------------

falsey(value) if value == false

falsey(value) if {
  is_string(value)
  lower(value) == "false"
}

local_artefact_step(step) if {
  inputs := object.get(step, "with", {})
  is_object(inputs)
  falsey(object.get(inputs, "publish-artefact", true))
}

has_publishing_coverage(workflow) if {
  some step in workflow_steps(workflow)
  is_coverage_step(step)
  not local_artefact_step(step)
}

# --------------------------------------------------------------------------
# Clause 2: the single main publisher is guarded and serialized.
# --------------------------------------------------------------------------

# Each pattern must match one whole conjunct, not a substring of the
# condition: `x && github.ref == 'refs/heads/main' || y` contains the
# comparison while making it optional.
main_ref_guard_patterns := [
  `^[(]*[[:space:]]*github\.ref[[:space:]]*==[[:space:]]*['"]refs/heads/main['"][[:space:]]*[)]*$`,
  `^[(]*[[:space:]]*['"]refs/heads/main['"][[:space:]]*==[[:space:]]*github\.ref[[:space:]]*[)]*$`,
]

step_condition(step) := condition if {
  condition := object.get(step, "if", "")
  is_string(condition)
}

# The expression inside an optional `${{ }}` wrapper, which GitHub accepts
# around a step condition or leaves out.
guard_expression(condition) := expression if {
  trimmed := trim_space(condition)
  wrapped_guard(trimmed)
  expression := trim_space(trim_suffix(trim_prefix(trimmed, "${{"), "}}"))
}

guard_expression(condition) := trim_space(condition) if {
  trimmed := trim_space(condition)
  not wrapped_guard(trimmed)
}

wrapped_guard(trimmed) if {
  startswith(trimmed, "${{")
  endswith(trimmed, "}}")
}

# A disjunction anywhere outside a quoted string makes every conjunct beside
# it optional, so a condition carrying one guards nothing. Quoted literals are
# blanked first so that `'a||b'` is not mistaken for an operator.
guard_has_disjunction(condition) if {
  unquoted := regex.replace(condition, `'[^']*'|"[^"]*"`, "''")
  contains(unquoted, "||")
}

guard_conjuncts(condition) := [trim_space(part) |
  some part in split(guard_expression(condition), "&&")
]

step_guarded_on_main_ref(step) if {
  condition := step_condition(step)
  not guard_has_disjunction(condition)
  some conjunct in guard_conjuncts(condition)
  some pattern in main_ref_guard_patterns
  regex.match(pattern, conjunct)
}

step_guarded_on_token(step) if {
  condition := step_condition(step)
  not guard_has_disjunction(condition)
  contains(condition, "CS_ACCESS_TOKEN")
}

# This direct output shape is safe without binding the secret in job or step
# environment. The producer must precede this upload in the same job; an
# output from a different job or an arbitrary shell expression proves nothing.
token_availability_run := `echo "available=${{ secrets.CS_ACCESS_TOKEN != '' }}" >> "$GITHUB_OUTPUT"`

upload_guarded_on_token(workflow, job_name, upload_index) if {
  jobs := workflow_jobs(workflow)
  step := jobs[job_name].steps[upload_index]
  step_guarded_on_token(step)
}

upload_guarded_on_token(workflow, job_name, upload_index) if {
  jobs := workflow_jobs(workflow)
  steps := jobs[job_name].steps
  upload := steps[upload_index]
  inputs := object.get(upload, "with", {})
  is_object(inputs)
  object.get(inputs, "access-token", "") == "${{ secrets.CS_ACCESS_TOKEN }}"
  condition := step_condition(upload)
  not guard_has_disjunction(condition)
  some producer_index
  producer := steps[producer_index]
  is_object(producer)
  producer_index < upload_index
  producer_id := object.get(producer, "id", "")
  is_string(producer_id)
  regex.match(`^[A-Za-z_][A-Za-z0-9_-]*$`, producer_id)
  run := producer.run
  is_string(run)
  trim_space(run) == token_availability_run
  some conjunct in guard_conjuncts(condition)
  conjunct == sprintf("steps.%s.outputs.available == 'true'", [producer_id])
}

workflow_concurrency(workflow) := value if {
  parsed := workflow_parsed(workflow)
  value := object.get(parsed, "concurrency", null)
}

has_concurrency_block(workflow) if {
  is_object(workflow_concurrency(workflow))
}

has_concurrency_block(workflow) if {
  value := workflow_concurrency(workflow)
  is_string(value)
  trim_space(value) != ""
}

# A cancelled publisher abandons both its upload and the ratchet baseline it
# was writing; a queued one publishes later and the later push's baseline
# wins. Only an absent or literally false `cancel-in-progress` queues: an
# expression may evaluate true on the very push it matters for.
publisher_cancels_in_progress(workflow) if {
  value := workflow_concurrency(workflow)
  is_object(value)
  "cancel-in-progress" in object.keys(value)
  not falsey(value["cancel-in-progress"])
}

publisher_paths := {path |
  some workflow in workflows
  main_coverage_publisher(workflow)
  path := workflow_path(workflow)
}

# --------------------------------------------------------------------------
# Clause 3: the removed installer digest is gone from every workflow.
# --------------------------------------------------------------------------

codescene_digest_variable := "CODESCENE_CLI_SHA256"

codescene_installer_script := "install-cs-coverage-tool.sh"

passes_installer_checksum(workflow) if {
  some step in workflow_steps(workflow)
  inputs := object.get(step, "with", {})
  is_object(inputs)
  "installer-checksum" in object.keys(inputs)
}

references_digest_variable(workflow) if {
  parsed := workflow_parsed(workflow)
  walk(parsed, [_, value])
  is_string(value)
  contains(value, codescene_digest_variable)
}

references_digest_variable(workflow) if {
  parsed := workflow_parsed(workflow)
  walk(parsed, [path, _])
  some element in path
  is_string(element)
  contains(element, codescene_digest_variable)
}

# Narrow by construction: a step that both fetches the CodeScene installer
# script and computes a digest of it. Recognizing less is a loud failure on
# the next adoption; recognizing more is a silent one nobody would chase.
refreshes_installer_digest(workflow) if {
  some step in workflow_steps(workflow)
  run := object.get(step, "run", "")
  is_string(run)
  contains(run, codescene_installer_script)
  contains(lower(run), "sha256")
}

# --------------------------------------------------------------------------
# A delegated job is reported by name, on the workflows where coverage could
# live. A scheduled support workflow that calls a shared automation workflow
# cannot affect this contract and is left alone.
# --------------------------------------------------------------------------

deny contains f if {
  envelope_ok
  some workflow in workflows
  not unsupported_workflow(workflow)
  coverage_relevant_workflow(workflow)
  some name in reusable_jobs(workflow)
  f := finding("indeterminate", workflow_path(workflow), sprintf("job %v delegates to a reusable workflow this audit cannot read", [name]))
}

# Clause 4: a platform that ratchets on pull requests ratchets on the trunk.
#
# `generate-coverage` keys the ratchet baseline by `runner.os` and saves it
# only on a push to main, so a Windows or macOS pull-request ratchet whose
# platform never runs on the trunk compares against an empty baseline
# forever.
# --------------------------------------------------------------------------

runner_labels(job) := [label |
  runs_on := object.get(job, "runs-on", null)
  is_string(runs_on)
  label := runs_on
]

runner_labels(job) := labels if {
  runs_on := object.get(job, "runs-on", null)
  is_array(runs_on)
  count(runs_on) > 0
  every entry in runs_on {
    is_string(entry)
  }
  labels := runs_on
}

label_platform(text) := "Windows" if {
  contains(text, "windows")
}

label_platform(text) := "macOS" if {
  not contains(text, "windows")
  contains(text, "macos")
}

label_platform(text) := "Linux" if {
  not contains(text, "windows")
  not contains(text, "macos")
  some marker in ["ubuntu", "ubicloud", "linux"]
  contains(text, marker)
}

runner_is_expression(job) if {
  some label in runner_labels(job)
  contains(label, "${{")
}

# A label list is a conjunction: the runner must carry every label, so the
# joined text is one runner's description and is classified once.
job_platform(job) := platform if {
  not runner_is_expression(job)
  labels := runner_labels(job)
  count(labels) > 0
  platform := label_platform(lower(concat(" ", labels)))
}

# A label written as an expression is a choice between the literals it can
# select, most often a fork fallback between two Linux labels. It is
# classified only when every literal it could select classifies, and all of
# them agree; that concludes nothing the document does not already say. A
# mixed expression, or one naming a label this rule does not recognize, stays
# indeterminate rather than being guessed.
expression_literals(text) := {literal |
  some raw in regex.find_n(`'[^']*'`, text, -1)
  literal := lower(trim(raw, "'"))
}

expression_values(job) := values if {
  runner_is_expression(job)
  labels := runner_labels(job)
  values := {value |
    some label in labels
    some value in expression_literals(label)
  }
}

job_platform(job) := platform if {
  values := expression_values(job)
  count(values) > 0
  classified := {value | some value in values; label_platform(value)}
  count(classified) == count(values)
  platforms := {candidate | some value in values; candidate := label_platform(value)}
  count(platforms) == 1
  some platform in platforms
}

job_has_ratcheting_coverage(job) if {
  is_object(job)
  steps := object.get(job, "steps", null)
  is_array(steps)
  some step in steps
  is_object(step)
  is_coverage_step(step)
  ratcheting_coverage_step(step)
}

ratcheting_coverage_jobs(workflow) := {name |
  jobs := workflow_jobs(workflow)
  some name in object.keys(jobs)
  job_has_ratcheting_coverage(jobs[name])
}

pr_ratchet_workflow(workflow) if {
  has_pr_trigger(workflow)
  not unsupported_workflow(workflow)
}

trunk_ratchet_workflow(workflow) if {
  main_only_trigger(workflow)
  not unsupported_workflow(workflow)
}

pr_ratcheted_platforms := {platform |
  some workflow in workflows
  pr_ratchet_workflow(workflow)
  some name in ratcheting_coverage_jobs(workflow)
  platform := job_platform(workflow_jobs(workflow)[name])
}

trunk_ratcheted_platforms := {platform |
  some workflow in workflows
  trunk_ratchet_workflow(workflow)
  some name in ratcheting_coverage_jobs(workflow)
  platform := job_platform(workflow_jobs(workflow)[name])
}

ratchet_scope_workflow(workflow) if pr_ratchet_workflow(workflow)

ratchet_scope_workflow(workflow) if trunk_ratchet_workflow(workflow)

unclassified_ratchet_jobs(workflow) := {name |
  ratchet_scope_workflow(workflow)
  some name in ratcheting_coverage_jobs(workflow)
  not job_platform(workflow_jobs(workflow)[name])
}

# --------------------------------------------------------------------------
# A repository with no coverage lane at all is not a CV-005 subject: there is
# nothing for a publisher to publish. A repository that generates coverage
# anywhere is.
# --------------------------------------------------------------------------

repository_has_coverage_lane if {
  some workflow in workflows
  has_coverage_step(workflow)
}

repository_has_coverage_lane if {
  some workflow in workflows
  has_codescene_step(workflow)
}

deny contains f if {
  not envelope_ok
  f := finding("indeterminate", ".github/workflows", "workflow envelope has an unknown shape")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  unsupported_coverage_candidate(workflow)
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
  has_coverage_step(workflow)
  has_unratcheted_coverage(workflow)
  f := finding("noncompliant", workflow_path(workflow), "pull-request workflow lacks ratcheting coverage generation")
}

deny contains f if {
  envelope_ok
  main_coverage_publisher_exists
  some workflow in workflows
  has_pr_trigger(workflow)
  not unsupported_workflow(workflow)
  has_unmatched_pr_coverage_identity(workflow)
  f := finding("noncompliant", workflow_path(workflow), "pull-request coverage baseline has no matching main publisher")
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
  repository_has_coverage_lane
  not main_coverage_publisher_exists
  f := finding("noncompliant", ".github/workflows", "no main-only workflow writes the ratchet baseline and explicitly uploads CodeScene coverage")
}

# Clause 1: a pull-request coverage report stays local to its run.
deny contains f if {
  envelope_ok
  some workflow in workflows
  has_pr_trigger(workflow)
  not unsupported_workflow(workflow)
  has_publishing_coverage(workflow)
  f := finding("noncompliant", workflow_path(workflow), "pull-request coverage does not set publish-artefact false")
}

# Clause 2: the publisher's upload step is guarded on the trunk ref, because
# a workflow_dispatch selects a ref the push filter says nothing about.
deny contains f if {
  envelope_ok
  some workflow in workflows
  qualifying_main_coverage_publisher(workflow)
  some step in workflow_steps(workflow)
  is_upload_step(step)
  not step_guarded_on_main_ref(step)
  f := finding("noncompliant", workflow_path(workflow), "CodeScene upload step is not guarded on github.ref == 'refs/heads/main'")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  qualifying_main_coverage_publisher(workflow)
  jobs := workflow_jobs(workflow)
  some job_name in object.keys(jobs)
  job := jobs[job_name]
  is_object(job)
  steps := object.get(job, "steps", [])
  is_array(steps)
  some upload_index
  step := steps[upload_index]
  is_object(step)
  is_upload_step(step)
  not upload_guarded_on_token(workflow, job_name, upload_index)
  f := finding("noncompliant", workflow_path(workflow), "CodeScene upload step is not guarded on the CS_ACCESS_TOKEN credential")
}

# Clause 2: two overlapping pushes to main must not race to write the
# baseline every pull request is then measured against.
deny contains f if {
  envelope_ok
  some workflow in workflows
  qualifying_main_coverage_publisher(workflow)
  not has_concurrency_block(workflow)
  f := finding("noncompliant", workflow_path(workflow), "main coverage publisher has no concurrency block")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  qualifying_main_coverage_publisher(workflow)
  publisher_cancels_in_progress(workflow)
  f := finding("noncompliant", workflow_path(workflow), "main coverage publisher cancels in progress rather than queueing")
}

deny contains f if {
  envelope_ok
  count(publisher_paths) > 1
  f := finding("noncompliant", ".github/workflows", sprintf("more than one main-only workflow uploads CodeScene coverage: %v", [concat(", ", sort(publisher_paths))]))
}

# Clause 3: the installer digest the uploader no longer reads is gone.
deny contains f if {
  envelope_ok
  some workflow in workflows
  not unsupported_workflow(workflow)
  passes_installer_checksum(workflow)
  f := finding("noncompliant", workflow_path(workflow), "workflow passes the removed installer-checksum input")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  not unsupported_workflow(workflow)
  references_digest_variable(workflow)
  f := finding("noncompliant", workflow_path(workflow), "workflow references the CODESCENE_CLI_SHA256 variable")
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  not unsupported_workflow(workflow)
  refreshes_installer_digest(workflow)
  f := finding("noncompliant", workflow_path(workflow), "workflow refreshes the CodeScene installer digest")
}

# Clause 4: a platform ratcheting on pull requests ratchets on the trunk.
deny contains f if {
  envelope_ok
  some platform in pr_ratcheted_platforms
  not platform in trunk_ratcheted_platforms
  f := finding("noncompliant", ".github/workflows", sprintf("pull requests ratchet coverage on %v with no %v lane on the trunk push", [platform, platform]))
}

deny contains f if {
  envelope_ok
  some workflow in workflows
  some name in unclassified_ratchet_jobs(workflow)
  f := finding("indeterminate", workflow_path(workflow), sprintf("coverage job %v has no recognized runner platform", [name]))
}
