# NC-006: CI wiring leaves nested cargo builds a warm cache.
#
# The Python envelope decoded each root workflow and recorded every place
# `CARGO_TARGET_DIR` is set (with its value, unevaluated) and, per job, whether
# `setup-rust` opts out of the wrapper export and whether `RUSTC_WRAPPER` is
# named anywhere in the workflow or job. A workflow that cannot be decoded is
# indeterminate rather than a pass. `setup-rust` exports the wrapper through
# `GITHUB_ENV` by default, so this rule does not ask for a job-level `env`.
package canon.lint_rules.nested_cargo_ci_wiring

import rego.v1

finding(id, verdict, path, msg) := {
	"rule_id": id,
	"severity": "error",
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": msg,
}

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/nested-cargo-ci-wiring"
	is_array(input.workflows)
}

default exemptions := []

exemptions := data.parameters.exemptions

temporary_patterns := data.parameters.temporary_patterns

repository_name := input.repository.name

# An exemption is a reviewed record: it names the repository, the workflow,
# why it needs the wiring the rule forbids, and what event sends it back for
# review. A blank rationale or review trigger asks nobody to revisit it, so it
# is not a record and suppresses nothing.
is_exempt(path) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.path == path
	is_string(exemption.rationale)
	trim_space(exemption.rationale) != ""
	is_string(exemption.review_trigger)
	trim_space(exemption.review_trigger) != ""
}

is_temporary(value) if {
	some pattern in temporary_patterns
	regex.match(pattern, value)
}

audited contains workflow if {
	envelope_ok
	some workflow in input.workflows
	not is_exempt(workflow.path)
}

deny contains finding("EN-001", "indeterminate", "", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/nested-cargo-ci-wiring envelope at schema version 1"
}

deny contains finding("EN-001", "indeterminate", workflow.path, msg) if {
	some workflow in audited
	workflow.error != null
	msg := sprintf("the workflow could not be read: %s", [workflow.error])
}

deny contains finding("NC-006", "noncompliant", workflow.path, msg) if {
	some workflow in audited
	workflow.error == null
	some setting in workflow.target_settings
	is_temporary(setting.value)
	msg := sprintf(
		"%s sets CARGO_TARGET_DIR to %q (%s), a per-run or temporary path, so every run and every nested cargo build starts with an empty target tree and sccache keys miss on the differing paths. Build into a stable directory such as the workspace target, or name the workflow in this rule's exemptions",
		[workflow.path, setting.value, setting.where],
	)
}

deny contains finding("NC-006", "noncompliant", workflow.path, msg) if {
	some workflow in audited
	workflow.error == null
	some job in workflow.jobs
	job.opts_out
	not job.wrapper_named
	msg := sprintf(
		"job %s of %s sets setup-rust's export-rustc-wrapper to false and names RUSTC_WRAPPER nowhere, so its child cargo compiles without sccache. Scope the wrapper to the commands that should use it (RUSTC_WRAPPER=\"$SCCACHE_PATH\" cargo ...), or leave the export on",
		[job.id, workflow.path],
	)
}
