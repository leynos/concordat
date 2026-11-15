# PT-001 to PT-006: how a repository that uses a prover obtains and runs it.
#
# The Python envelope reads the facts with parsers: `makeutil` for Make
# prerequisites, `shlex` for the commands in `run` scripts and recipes, the
# `ast` for Python scripts and tree-sitter for Rust proof code. This policy
# decides. A repository is audited only where it uses a prover: proof code, a
# version pin, a `rust-prover-tools` revision, or a command that installs or
# runs one. A recorded exception waives PT-001, PT-002 and PT-004 while the pin
# it names is still the pin in the checkout and its decision record exists;
# PT-003, PT-005 and PT-006 are never waivable.
package canon.lint_rules.rust_prover_tooling

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
	input.kind == "policy-input/rust-prover-tooling"
	is_array(input.workflows)
	is_array(input.pins)
	is_array(input.exceptions)
}

prover_tools_ref := data.parameters.prover_tools_ref

waivable_clauses := data.parameters.waivable_clauses

min_pin_prefix := 8

pin_values := {pin.value | some pin in input.pins}

uses_prover if {
	envelope_ok
	count(input.version_files) > 0
}

uses_prover if {
	envelope_ok
	count(input.pins) > 0
}

uses_prover if {
	envelope_ok
	input.runs_prover
}

uses_prover if {
	envelope_ok
	count(input.local_installers) > 0
}

# A repository that holds proof code uses a prover, whatever else it names: the
# code is what PT-006 asks CI to run, and PT-001 applies to it from then on.
proof_code := object.get(input, "proof_code", [])

uses_prover if {
	envelope_ok
	count(proof_code) > 0
}

uses_prover if {
	envelope_ok
	some workflow in input.workflows
	count(workflow.installer_steps) + count(workflow.shared_uses) > 0
}

# Evidence the rule could not read is not evidence of absence: a failed read
# makes the repository audited for the indeterminate finding, so it is reported
# rather than hidden behind a lack of positive proof of use. The PT checks
# still apply only where there is positive proof of use.
audited if {
	uses_prover
}

audited if {
	envelope_ok
	count(input.unreadable) > 0
}

audited if {
	envelope_ok
	input.makefile.state == "refused"
}

audited if {
	envelope_ok
	some workflow in input.workflows
	workflow.error != null
}

audited if {
	envelope_ok
	some workflow in input.workflows
	count(workflow.unresolved_make) > 0
}

# An exception is a reviewed record: it names the pin the repository stays on,
# the decision record that explains it, why, and what sends it back for review.
# It holds only while that pin is the pin in the checkout, so moving the pin
# reopens the question rather than leaving a stale waiver behind. Each defect is
# keyed by the exception's index so that several can be reported at once.
defect contains [index, "it waives a clause that cannot be waived (only PT-001, PT-002 and PT-004 can)"] if {
	some index, e in input.exceptions
	some clause in e.clauses
	not clause in waivable_clauses
}

defect contains [index, "its rationale is blank"] if {
	some index, e in input.exceptions
	trim_space(e.rationale) == ""
}

defect contains [index, "its review trigger is blank"] if {
	some index, e in input.exceptions
	trim_space(e.review_trigger) == ""
}

defect contains [index, msg] if {
	some index, e in input.exceptions
	not e.record_present
	msg := sprintf("its decision record %s does not exist", [e.record])
}

defect contains [index, "its pin is shorter than eight digits"] if {
	some index, e in input.exceptions
	count(e.pin) < min_pin_prefix
}

defect contains [index, "the checkout names no rust-prover-tools revision"] if {
	some index, _ in input.exceptions
	count(pin_values) == 0
}

defect contains [index, msg] if {
	some index, e in input.exceptions
	some value in pin_values
	not startswith(value, e.pin)
	msg := sprintf("the checkout names %s, not the pinned %s", [value, e.pin])
}

in_force(index) if {
	not has_defect(index)
}

has_defect(index) if {
	some d in defect
	d[0] == index
}

waived(clause) if {
	some index, e in input.exceptions
	clause in e.clauses
	in_force(index)
}

deny contains finding("EN-001", "indeterminate", "", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/rust-prover-tooling envelope at schema version 1"
}

deny contains finding("EN-001", "indeterminate", workflow.path, msg) if {
	audited
	some workflow in input.workflows
	workflow.error != null
	msg := sprintf("the workflow could not be read: %s", [workflow.error])
}

# A refused Makefile hides the pins its variables name, so PT-001 cannot be
# judged; and it hides what a workflow's `make` steps install, so PT-002 cannot
# either. Each clause that is not waived reports it.
deny contains finding("EN-001", "indeterminate", "Makefile", msg) if {
	audited
	not waived("PT-001")
	input.makefile.state == "refused"
	msg := sprintf(
		"the Makefile could not be parsed (%s), so the rust-prover-tools revisions its variables name are unknown",
		[input.makefile.error],
	)
}

deny contains finding("EN-001", "indeterminate", workflow.path, msg) if {
	audited
	not waived("PT-002")
	input.makefile.state == "refused"
	some workflow in input.workflows
	workflow.error == null
	workflow.calls_make
	msg := sprintf(
		"%s runs make, and the Makefile could not be parsed (%s), so whether its targets install a prover is unknown",
		[workflow.path, input.makefile.error],
	)
}

deny contains finding("EN-001", "indeterminate", "", msg) if {
	audited
	some reason in input.unreadable
	msg := sprintf("a file the rule reads could not be read: %s", [reason])
}

# A `make` that builds another directory's Makefile (a working directory, `-C`
# or `-f`) is not the root Makefile the rule reads, so what it installs is
# unknown.
deny contains finding("EN-001", "indeterminate", workflow.path, msg) if {
	audited
	not waived("PT-002")
	some workflow in input.workflows
	workflow.error == null
	some step in workflow.unresolved_make
	msg := sprintf(
		"step %s of %s runs %q from another directory or with another Makefile, which the rule does not read, so whether it installs a prover is unknown",
		[step.where, workflow.path, step.command],
	)
}

deny contains finding("EX-001", "noncompliant", e.record, msg) if {
	uses_prover
	some d in defect
	e := input.exceptions[d[0]]
	msg := sprintf(
		"the recorded exception for %s is not in force: %s. It waives nothing until that is put right, or the repository meets the standard",
		[concat(", ", e.clauses), d[1]],
	)
}

deny contains finding("PT-001", "noncompliant", "", msg) if {
	uses_prover
	not waived("PT-001")
	count(pin_values) == 0
	msg := sprintf(
		"no rust-prover-tools revision is pinned. Pin %s in tools/rust-prover-tools/REF or in the Makefile's prover-tools source",
		[prover_tools_ref],
	)
}

deny contains finding("PT-001", "noncompliant", "", msg) if {
	uses_prover
	not waived("PT-001")
	count(pin_values) > 0
	pin_values != {prover_tools_ref}
	msg := sprintf(
		"rust-prover-tools is pinned to %s; the estate pins one commit, %s",
		[concat(", ", sort([v | some v in pin_values])), prover_tools_ref],
	)
}

deny contains finding("PT-002", "noncompliant", workflow.path, msg) if {
	uses_prover
	not waived("PT-002")
	some workflow in input.workflows
	workflow.error == null
	some step in workflow.installer_steps
	msg := sprintf(
		"step %s of %s installs a prover itself (%s) instead of through the shared install-prover-tools action. Use the action, and make the Make targets run proofs only",
		[step.where, workflow.path, step.command],
	)
}

deny contains finding("PT-003", "noncompliant", workflow.path, msg) if {
	uses_prover
	some workflow in input.workflows
	workflow.error == null
	some use in workflow.shared_uses
	not regex.match(`^[0-9a-f]{40}$`, use.ref)
	msg := sprintf(
		"step %s of %s uses install-prover-tools at %q, which is not a full commit SHA. Pin the action by SHA",
		[use.where, workflow.path, use.ref],
	)
}

deny contains finding("PT-004", "noncompliant", installer.path, msg) if {
	uses_prover
	not waived("PT-004")
	some installer in input.local_installers
	msg := sprintf(
		"%s is a local prover installer (%s). Remove it and install through the shared install-prover-tools action",
		[installer.path, installer.reason],
	)
}

# PT-006: proof code is run. A workflow step runs proofs when a command
# verifies them (`cargo kani`, `prover-tools verus run`, Verus on a proof file),
# a `make` target or script reaches one, or the workflow installs through the
# shared action and runs a prover-named target that does real work. Installing
# or version-checking a prover does not count. The clause is not waivable: the
# remedies are to run the proofs or to remove the code.
workflow_proof_runs(workflow) := object.get(workflow, "proof_runs", [])

# The provers the workflows run proofs with. A Kani harness is matched to a Kani
# run and a Verus block to a Verus run, so a repository whose only run is Verus
# does not pass for its Kani harnesses. A generator emits either, so any run
# will do for it.
proof_tools contains run.tool if {
	some workflow in input.workflows
	workflow.error == null
	some run in workflow_proof_runs(workflow)
}

# Proof code no run matches.
unmatched contains item if {
	some item in proof_code
	not run_matches(item)
}

run_matches(item) if {
	item.kind == "kani-harness"
	"kani" in proof_tools
}

run_matches(item) if {
	item.kind == "verus-block"
	"verus" in proof_tools
}

run_matches(item) if {
	item.kind == "proof-generator"
	count(proof_tools) > 0
}

proofs_run if {
	count(unmatched) == 0
}

# Whether a workflow runs proofs is knowable only when every workflow was read
# and each `make` step could be followed into the root Makefile.
proof_runs_unknowable contains reason if {
	some workflow in input.workflows
	workflow.error != null
	reason := sprintf("%s could not be read", [workflow.path])
}

proof_runs_unknowable contains "the Makefile could not be parsed" if {
	input.makefile.state == "refused"
}

proof_runs_unknowable contains reason if {
	some workflow in input.workflows
	some step in workflow.unresolved_make
	reason := sprintf("step %s of %s runs make against another directory or Makefile", [step.where, workflow.path])
}

deny contains finding("EN-001", "indeterminate", proof_code[0].path, msg) if {
	uses_prover
	count(proof_code) > 0
	not proofs_run
	some reason in proof_runs_unknowable
	msg := sprintf("whether CI runs the proofs is unknown: %s", [reason])
}

deny contains finding("PT-006", "noncompliant", first.path, msg) if {
	uses_prover
	count(proof_code) > 0
	not proofs_run
	count(proof_runs_unknowable) == 0
	first := sort([item | some item in unmatched])[0]
	msg := sprintf(
		"%s holds %s proof code, and no workflow step runs proofs for it. Proof code that nothing runs is a comment: run it in CI (installing through the shared install-prover-tools action), or remove it",
		[first.path, first.kind],
	)
}

deny contains finding("PT-005", "noncompliant", path, msg) if {
	uses_prover
	some path in input.install_tests
	msg := sprintf(
		"%s tests prover installation. The installer is owned and tested in rust-prover-tools; remove the test",
		[path],
	)
}
