# python-duplication-gate: NS-001 to NS-004.
#
# Input is a policy-input/python-duplication-gate envelope. The Python envelope
# reads pyproject.toml with a TOML parser and decides which Python files each
# scan root selects, matching `exclude` as gitignore patterns relative to the
# root, as the detector does. This policy decides what those facts mean.
#
# The shared df12-duplication command checks the shape of [tool.nose] and
# refuses an entirely empty scan; it cannot know repository policy. The rule
# holds a repository to three: the scan is not capped (NS-001), no test tree is
# scanned (NS-002), and every root selects something (NS-003). NS-004 is the
# fail-closed verdict for a file or table the rule cannot read. A repository
# with no [tool.nose] table does not use the gate and is not audited. No clause
# is waivable, so the policy reads no exception data.
package canon.lint_rules.python_duplication_gate

import rego.v1

finding(id, verdict, path, msg) := {
	"rule_id": id,
	"severity": "error",
	"verdict": verdict,
	"path": path,
	"line": 0,
	"msg": msg,
}

pyproject_path := "pyproject.toml"

envelope_ok if {
	input.schema_version == 1
	input.kind == "policy-input/python-duplication-gate"
	is_object(input.applicability)
	is_array(input.roots)
}

# The gate is in use where the table exists. A pyproject that could not be
# decoded may or may not have one, so it is judged below, never skipped.
uses_gate if {
	envelope_ok
	input.applicability.nose_table == true
}

# -- envelope --------------------------------------------------------------------

deny contains finding("EN-001", "indeterminate", "", msg) if {
	not envelope_ok
	msg := "policy input is not a policy-input/python-duplication-gate envelope at schema version 1"
}

# -- NS-004: the settings cannot be read --------------------------------------------

deny contains finding("NS-004", "indeterminate", pyproject_path, msg) if {
	envelope_ok
	input.pyproject != null
	input.pyproject.error != null
	msg := sprintf("pyproject.toml could not be read, so the duplication gate's scope is unknown: %s", [input.pyproject.error])
}

deny contains finding("NS-004", "indeterminate", pyproject_path, msg) if {
	uses_gate
	some problem in input.nose.problems
	msg := sprintf("the duplication gate's scope cannot be judged: %s", [problem])
}

# The envelope records roots only when no setting has a shape problem, and an
# unset `top` is only a finding then, so one broken setting is reported as
# NS-004 rather than as a pile of consequences. A positive `top` is a real
# integer whatever else is wrong, so NS-001 reports it regardless.
settings_readable if {
	uses_gate
	count(input.nose.problems) == 0
}

# -- NS-001: the scan is not capped -------------------------------------------------

deny contains finding("NS-001", "noncompliant", pyproject_path, msg) if {
	settings_readable
	input.nose.top_present == false
	msg := "[tool.nose].top is not set, so nose reports only its default 30 families; set top = 0 so every duplicate family is adjudicated"
}

deny contains finding("NS-001", "noncompliant", pyproject_path, msg) if {
	uses_gate
	input.nose.top_present == true
	input.nose.top != null
	input.nose.top != 0
	msg := sprintf("[tool.nose].top is %d, so only that many duplicate families are reported and lower-ranked duplication passes unadjudicated; set top = 0", [input.nose.top])
}

# -- NS-002: test trees are outside the scan ----------------------------------------

deny contains finding("NS-002", "noncompliant", root.root, msg) if {
	settings_readable
	some root in input.roots
	root.test_file_count > 0
	msg := sprintf(
		"scan root %q selects %d Python file(s) in a test tree (%s), such as %s; add an exclude pattern, because test scaffolding repeats on purpose",
		[root.root, root.test_file_count, concat("/, ", input.test_directory_names), root.test_files[0]],
	)
}

# -- NS-003: every root selects something -------------------------------------------

deny contains finding("NS-003", "noncompliant", root.root, msg) if {
	settings_readable
	some root in input.roots
	root.exists == false
	msg := sprintf("scan root %q does not exist", [root.root])
}

deny contains finding("NS-003", "noncompliant", root.root, msg) if {
	settings_readable
	some root in input.roots
	root.exists == true
	root.selected_count == 0
	msg := sprintf("scan root %q selects no Python file, so it scans nothing; fix the root or the exclude patterns", [root.root])
}
