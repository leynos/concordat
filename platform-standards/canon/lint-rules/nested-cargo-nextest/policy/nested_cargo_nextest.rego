# NC-004, NC-005: tests that spawn a compiler run in a serialized, bounded
# nextest group.
#
# The Python envelope scanned the Rust test sources for compiler spawns
# (conservatively: compiling cargo subcommands and rustc, in test code only) and
# read `.config/nextest.toml`. A repository with no non-exempt spawner is
# compliant. A configuration that cannot be read is indeterminate rather than a
# pass.
#
# NC-004, NC-005 and the indeterminate findings that report evidence the reader
# could not establish carry the configured severity, which defaults to
# `warning` so the package ships as a survey: a warning is reported and does not
# block. An envelope defect is always an error.
package canon.lint_rules.nested_cargo_nextest

import rego.v1

finding(id, severity, verdict, path, msg) := {
	"rule_id": id,
	"severity": severity,
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": msg,
}

config_path := ".config/nextest.toml"

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/nested-cargo-nextest"
	is_object(input.nextest)
	input.nextest.state in {"present", "absent", "unreadable"}
	is_array(input.spawners)
	is_array(input.problems)
}

default severity := "warning"

severity := data.parameters.severity

default exemptions := []

exemptions := data.parameters.exemptions

repository_name := input.repository.name

# An exemption is a reviewed record: it names the repository, the test source,
# why it is bounded some other way, and what event sends it back for review. A
# blank rationale or review trigger asks nobody to revisit it, so it is not a
# record and suppresses nothing.
is_exempt(path) if {
	some exemption in exemptions
	exemption.repository == repository_name
	exemption.path == path
	is_string(exemption.rationale)
	trim_space(exemption.rationale) != ""
	is_string(exemption.review_trigger)
	trim_space(exemption.review_trigger) != ""
}

active contains spawner if {
	envelope_ok
	some spawner in input.spawners
	not is_exempt(spawner.path)
}

sample := concat(", ", [s.path | some s in active])

deny contains finding("EN-001", "error", "indeterminate", "", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/nested-cargo-nextest envelope at schema version 1"
}

deny contains finding("EN-001", severity, "indeterminate", config_path, msg) if {
	envelope_ok
	input.nextest.state == "unreadable"
	msg := sprintf("the nextest configuration cannot be read: %s", [input.nextest.error])
}

deny contains finding("EN-001", severity, "indeterminate", "", msg) if {
	envelope_ok
	some problem in input.problems
	msg := sprintf("a nested-cargo fact could not be established: %s", [problem])
}

deny contains finding("NC-004", severity, "noncompliant", config_path, msg) if {
	envelope_ok
	count(active) > 0
	input.nextest.state == "absent"
	msg := sprintf("tests spawn cargo or rustc (%s) but the repository has no .config/nextest.toml, so a cold build is bounded by nothing but the job. Add a serial test group, a slow-timeout and a global-timeout", [sample])
}

deny contains finding("NC-004", severity, "noncompliant", config_path, msg) if {
	envelope_ok
	count(active) > 0
	input.nextest.state == "present"
	count(input.nextest.serial_groups) == 0
	msg := sprintf("tests spawn cargo or rustc (%s) but no test group has max-threads = 1, so their builds compete with each other", [sample])
}

deny contains finding("NC-004", severity, "noncompliant", config_path, msg) if {
	envelope_ok
	count(active) > 0
	input.nextest.state == "present"
	count(input.nextest.serial_groups) > 0
	not input.nextest.grouped_override
	msg := sprintf("tests spawn cargo or rustc (%s) but no profile override places tests in the serial group (%s)", [sample, concat(", ", input.nextest.serial_groups)])
}

deny contains finding("NC-004", severity, "noncompliant", config_path, msg) if {
	envelope_ok
	count(active) > 0
	input.nextest.state == "present"
	not input.nextest.slow_timeout
	msg := sprintf("tests spawn cargo or rustc (%s) but no profile or override sets slow-timeout, so a stuck build is reported and never terminated", [sample])
}

deny contains finding("NC-004", severity, "noncompliant", config_path, msg) if {
	envelope_ok
	count(active) > 0
	input.nextest.state == "present"
	not input.nextest.global_timeout
	msg := sprintf("tests spawn cargo or rustc (%s) but no profile sets global-timeout, so a hung run is bounded only by the job", [sample])
}

# A top-level test binary that spawns a compiler must be named by a grouped
# override, or the serial group never contains it.
deny contains finding("NC-005", severity, "noncompliant", spawner.path, msg) if {
	envelope_ok
	some spawner in active
	input.nextest.state == "present"
	count(input.nextest.serial_groups) > 0
	spawner.named == false
	msg := sprintf("%s spawns %s but no override in a serial test group names its binary or its tests, so its builds run beside other tests. Add a filter such as binary(%s) to a grouped override", [spawner.path, spawner.kind, spawner.binary])
}
