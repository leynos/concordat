# NC-003: a standalone manifest with registry dependencies is covered by a
# cargo entry of the Dependabot configuration.
#
# The Python envelope has found each manifest below the root that no workspace
# owns and that declares a registry dependency, and recorded whether a cargo
# `updates` entry covers its directory (`covered` is null when the
# configuration could not be decoded). A repository with no such manifest is
# compliant. A configuration that cannot be decoded is indeterminate rather
# than a pass.
#
# NC-003 and the indeterminate findings that report evidence the reader could
# not establish carry the configured severity, which defaults to `warning` so
# the package ships as a survey: a warning is reported and does not block. An
# envelope defect is always an error.
package canon.lint_rules.nested_cargo_fixture_manifests

import rego.v1

finding(id, severity, verdict, path, msg) := {
	"rule_id": id,
	"severity": severity,
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": msg,
}

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/nested-cargo-fixture-manifests"
	is_object(input.dependabot)
	input.dependabot.state in {"present", "absent", "unreadable"}
	is_array(input.manifests)
	is_array(input.problems)
}

default severity := "warning"

severity := data.parameters.severity

default exemptions := []

exemptions := data.parameters.exemptions

repository_name := input.repository.name

# An exemption is a reviewed record: it names the repository, the directory,
# why the manifest is left alone, and what event sends it back for review. A
# blank rationale or review trigger asks nobody to revisit it, so it is not a
# record and suppresses nothing.
is_exempt(directory) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.path == directory
	is_string(exemption.rationale)
	trim_space(exemption.rationale) != ""
	is_string(exemption.review_trigger)
	trim_space(exemption.review_trigger) != ""
}

deny contains finding("EN-001", "error", "indeterminate", "", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/nested-cargo-fixture-manifests envelope at schema version 1"
}

deny contains finding("EN-001", severity, "indeterminate", path, msg) if {
	envelope_ok
	input.dependabot.state == "unreadable"
	path := object.get(input.dependabot, "path", ".github/dependabot.yml")
	msg := sprintf("the Dependabot configuration cannot be read: %s", [input.dependabot.error])
}

deny contains finding("EN-001", severity, "indeterminate", "", msg) if {
	envelope_ok
	some problem in input.problems
	msg := sprintf("a nested-cargo fact could not be established: %s", [problem])
}

deny contains finding("NC-003", severity, "noncompliant", manifest.path, msg) if {
	envelope_ok
	some manifest in input.manifests
	manifest.covered == false
	not is_exempt(manifest.directory)
	msg := sprintf(
		"%s declares registry dependencies and no cargo entry of the Dependabot configuration covers /%s, so nothing updates them and the fixture drifts from the workspace. Add the directory (or a glob reaching it) to a cargo `updates` entry, or name it in this rule's exemptions",
		[manifest.path, manifest.directory],
	)
}
