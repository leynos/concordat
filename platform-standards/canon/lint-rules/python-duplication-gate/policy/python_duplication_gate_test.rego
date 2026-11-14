# Tests for python-duplication-gate. Each fixture in fixtures/data.json is the
# production envelope for a synthetic checkout (see fixtures/generate.py); the
# expected set is the exact rule and verdict pairs the policy must report, so a
# test fails on a missing finding and on an extra one.
package canon.lint_rules.python_duplication_gate_test

import rego.v1

import data.canon.lint_rules.python_duplication_gate as policy

reported(envelope) := {sprintf("%s/%s", [f.rule_id, f.verdict]) |
	some f in policy.deny with input as envelope
}

findings(envelope) := [f | some f in policy.deny with input as envelope]

# -- compliant and out of scope -----------------------------------------------------

test_compliant if reported(data.fixtures.compliant) == set()

test_compliant_anchored_excludes if reported(data.fixtures.compliant_anchored_excludes) == set()

test_compliant_file_root if reported(data.fixtures.compliant_file_root) == set()

test_compliant_test_tree_without_python if reported(data.fixtures.compliant_test_tree_without_python) == set()

test_compliant_hidden_test_trees if reported(data.fixtures.compliant_hidden_test_trees) == set()

test_compliant_negation_is_literal if reported(data.fixtures.compliant_negation_is_literal) == set()

test_compliant_file_root_beside_test_tree if reported(data.fixtures.compliant_file_root_beside_test_tree) == set()

test_no_pyproject_is_not_audited if reported(data.fixtures.no_pyproject) == set()

test_no_nose_table_is_not_audited if reported(data.fixtures.no_nose_table) == set()

# -- NS-001 ------------------------------------------------------------------------

test_top_absent if reported(data.fixtures.top_absent) == {"NS-001/noncompliant"}

test_top_positive if reported(data.fixtures.top_positive) == {"NS-001/noncompliant"}

test_top_positive_names_the_cap if {
	[f] := findings(data.fixtures.top_positive)
	contains(f.msg, "top is 30")
}

test_top_string_is_unreadable if reported(data.fixtures.top_string) == {"NS-004/indeterminate"}

test_top_positive_and_test_tree if reported(data.fixtures.top_positive_and_test_tree) == {"NS-001/noncompliant", "NS-002/noncompliant"}

# -- NS-002 ------------------------------------------------------------------------

test_test_tree_not_excluded if reported(data.fixtures.test_tree_not_excluded) == {"NS-002/noncompliant"}

test_test_tree_names_the_file if {
	[f] := findings(data.fixtures.test_tree_not_excluded)
	contains(f.msg, "pkg/tests/test_mod.py")
	f.path == "pkg"
}

test_test_tree_partly_excluded if reported(data.fixtures.test_tree_partly_excluded) == {"NS-002/noncompliant"}

test_test_tree_exclude_names_repository_path if reported(data.fixtures.test_tree_exclude_names_repository_path) == {"NS-002/noncompliant"}

test_test_tree_under_vendor_directory if reported(data.fixtures.test_tree_under_vendor_directory) == {"NS-002/noncompliant"}

test_test_stub_in_test_tree if reported(data.fixtures.test_stub_in_test_tree) == {"NS-002/noncompliant"}

test_test_tree_unittests_not_excluded if reported(data.fixtures.test_tree_unittests_not_excluded) == {"NS-002/noncompliant"}

test_root_is_test_tree if reported(data.fixtures.root_is_test_tree) == {"NS-002/noncompliant"}

test_root_inside_test_tree if reported(data.fixtures.root_inside_test_tree) == {"NS-002/noncompliant"}

# -- NS-003 ------------------------------------------------------------------------

test_root_is_test_tree_and_excluded if reported(data.fixtures.root_is_test_tree_and_excluded) == {"NS-003/noncompliant"}

test_root_absent if reported(data.fixtures.root_absent) == {"NS-003/noncompliant"}

test_root_absent_names_the_root if {
	[f] := findings(data.fixtures.root_absent)
	f.path == "missing"
}

test_root_selects_nothing if reported(data.fixtures.root_selects_nothing) == {"NS-003/noncompliant"}

test_root_excluded_entirely if reported(data.fixtures.root_excluded_entirely) == {"NS-003/noncompliant"}

test_root_excluded_by_negation_only if reported(data.fixtures.root_excluded_by_negation_only) == {"NS-003/noncompliant"}

test_root_is_not_python_file if reported(data.fixtures.root_is_not_python_file) == {"NS-003/noncompliant"}

# -- NS-004 ------------------------------------------------------------------------

test_pyproject_invalid if reported(data.fixtures.pyproject_invalid) == {"NS-004/indeterminate"}

test_nose_not_a_table if reported(data.fixtures.nose_not_a_table) == {"NS-004/indeterminate"}

test_roots_not_a_list if reported(data.fixtures.roots_not_a_list) == {"NS-004/indeterminate"}

test_roots_empty if reported(data.fixtures.roots_empty) == {"NS-004/indeterminate"}

test_roots_absolute if reported(data.fixtures.roots_absolute) == {"NS-004/indeterminate"}

test_roots_leave_repository if reported(data.fixtures.roots_leave_repository) == {"NS-004/indeterminate"}

test_exclude_not_a_list if reported(data.fixtures.exclude_not_a_list) == {"NS-004/indeterminate"}

test_exclude_invalid_pattern if reported(data.fixtures.exclude_invalid_pattern) == {"NS-004/indeterminate"}

# -- envelope ----------------------------------------------------------------------

test_unknown_kind_is_indeterminate if reported(object.union(data.fixtures.compliant, {"kind": "policy-input/other"})) == {"EN-001/indeterminate"}

test_unknown_schema_is_indeterminate if reported(object.union(data.fixtures.compliant, {"schema_version": 2})) == {"EN-001/indeterminate"}

# No clause is waivable: a recorded exception in the checkout's data changes nothing.
test_no_waiver_is_read if reported(object.union(data.fixtures.top_positive, {"exceptions": [{"clauses": ["NS-001"]}]})) == {"NS-001/noncompliant"}
