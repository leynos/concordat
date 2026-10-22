# NC-001, NC-002: a nested-build fixture resolves a subset of the workspace
# graph, and a fixture with dependencies is locked.
#
# The Python envelope has read the root lock, every fixture lock below the
# root (a workspace member's lock is never read by Cargo and is left out), and
# every fixture manifest with dependencies. A repository with no root lock has
# no workspace graph to align to: the envelope then lists nothing and the
# repository is compliant. Anything the envelope could not read is a problem
# fact, which this policy answers as indeterminate rather than as a pass.
package canon.lint_rules.nested_cargo_fixture_lockfiles

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
	input.kind == "policy-input/nested-cargo-fixture-lockfiles"
	is_object(input.root_lock)
	input.root_lock.state in {"present", "absent", "unreadable"}
	is_array(input.nested_locks)
	is_array(input.unlocked_manifests)
	is_array(input.problems)
}

default exemptions := []

exemptions := data.parameters.exemptions

repository_name := input.repository.name

is_exempt(directory) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.path == directory
}

deny contains finding("EN-001", "indeterminate", "", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/nested-cargo-fixture-lockfiles envelope at schema version 1"
}

deny contains finding("EN-001", "indeterminate", "Cargo.lock", msg) if {
	envelope_ok
	input.root_lock.state == "unreadable"
	msg := sprintf("the workspace Cargo.lock cannot be read: %s", [input.root_lock.error])
}

deny contains finding("EN-001", "indeterminate", "", msg) if {
	envelope_ok
	some problem in input.problems
	msg := sprintf("a nested-cargo fact could not be established: %s", [problem])
}

sample(lock) := concat(", ", lock.foreign_packages)

deny contains finding("NC-001", "noncompliant", lock.path, msg) if {
	envelope_ok
	some lock in input.nested_locks
	not is_exempt(lock.directory)
	msg := sprintf(
		"%s resolves %d registry package(s) the workspace Cargo.lock does not (%s), so a nested cargo build of this fixture compiles units the workspace build never did. Re-seed it from the workspace lock and re-resolve it, or name the fixture in this rule's exemptions with the lane that owns its lock",
		[lock.path, lock.foreign_count, sample(lock)],
	)
}

deny contains finding("NC-002", "noncompliant", manifest.path, msg) if {
	envelope_ok
	some manifest in input.unlocked_manifests
	not is_exempt(manifest.directory)
	msg := sprintf(
		"%s declares dependencies and has no Cargo.lock beside it, so Cargo resolves it afresh on every nested build. Commit a lock seeded from the workspace lock, or name the fixture in this rule's exemptions",
		[manifest.path],
	)
}
