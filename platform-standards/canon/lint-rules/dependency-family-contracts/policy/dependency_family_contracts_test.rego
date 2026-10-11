# Fixture tests for DF-001 to DF-003 and DF-006, dependency family contracts.
package canon.lint_rules.dependency_family_contracts_test

import rego.v1

import data.canon.lint_rules.dependency_family_contracts as policy

ids(findings) := {finding.rule_id | some finding in findings}

profile(findings) := {[finding.rule_id, finding.verdict] | some finding in findings}

# One narrow group takes both PyO3 packages ahead of the catch-all.
test_compliant_family if {
  findings := policy.deny with input as data.fixtures.compliant_family
  count(findings) == 0
}

# No narrow group: the catch-all takes both (H1 for a split family).
test_split_family if {
  findings := policy.deny with input as data.fixtures.split_family
  profile(findings) == {["DF-001", "noncompliant"]}
}

# A narrow group exists but an earlier group takes pyo3 first.
test_shadowed_family if {
  findings := policy.deny with input as data.fixtures.shadowed_family
  profile(findings) == {["DF-003", "noncompliant"]}
}

# An optional member is still declared, so the family still splits.
test_optional_member if {
  findings := policy.deny with input as data.fixtures.optional_member
  ids(findings) == {"DF-001"}
}

# A workspace member is updated through its covered root, so the split is seen
# in the nested manifest, and the grouped variant is clean.
test_nested_manifest_split if {
  findings := policy.deny with input as data.fixtures.nested_manifest_split
  ids(findings) == {"DF-001"}
  some finding in findings
  finding.path == "rust/native/Cargo.toml"
}

test_nested_manifest_grouped if {
  findings := policy.deny with input as data.fixtures.nested_manifest_grouped
  count(findings) == 0
}

# A renamed bridge with no ignore rule is an unprotected tuple.
test_renamed_bridge_unprotected if {
  findings := policy.deny with input as data.fixtures.renamed_bridge_unprotected
  ids(findings) == {"DF-002"}
}

# An ignore rule on the published name protects it.
test_renamed_bridge_ignored_by_published_name if {
  findings := policy.deny with input as data.fixtures.renamed_bridge_ignored_by_published_name
  count(findings) == 0
}

# An ignore rule on the alias protects nothing: Dependabot matches the published name.
test_renamed_bridge_ignored_by_alias if {
  findings := policy.deny with input as data.fixtures.renamed_bridge_ignored_by_alias
  ids(findings) == {"DF-002"}
}

# An exact pin is not an upper bound.
test_maturin_exact_pin if {
  findings := policy.deny with input as data.fixtures.maturin_exact_pin
  ids(findings) == {"DF-002"}
}

test_maturin_bounded if {
  findings := policy.deny with input as data.fixtures.maturin_bounded
  count(findings) == 0
}

# A pure-Python repository with a copied wheel template does not declare PyO3,
# so no maturin/PyO3 relationship applies.
test_pure_python_template if {
  findings := policy.deny with input as data.fixtures.pure_python_template
  count(findings) == 0
}

# Type identity: one group, with the lock giving the resolved identities.
test_fluent_grouped if {
  findings := policy.deny with input as data.fixtures.fluent_grouped
  count(findings) == 0
}

test_fluent_split if {
  findings := policy.deny with input as data.fixtures.fluent_split
  ids(findings) == {"DF-001"}
}

# Type identity without a lock cannot be judged, never compliant.
test_fluent_without_lock if {
  findings := policy.deny with input as data.fixtures.fluent_without_lock
  profile(findings) == {["DF-006", "indeterminate"]}
}

# Unrelated duplicate crate versions are not a defect.
test_unrelated_duplicates if {
  findings := policy.deny with input as data.fixtures.unrelated_duplicates
  count(findings) == 0
}

# A reviewed exception silences the relationship it names.
test_split_with_exception if {
  findings := policy.deny with input as data.fixtures.split_with_exception
  count(findings) == 0
}

# An exception without a review trigger is a problem, and the finding stays.
test_split_with_incomplete_exception if {
  findings := policy.deny with input as data.fixtures.split_with_incomplete_exception
  profile(findings) == {["DF-001", "noncompliant"], ["DF-006", "indeterminate"]}
}

# No Dependabot configuration cannot be judged.
test_no_dependabot if {
  findings := policy.deny with input as data.fixtures.no_dependabot
  profile(findings) == {["DF-006", "indeterminate"]}
}

# An unparsable manifest is indeterminate, not compliant.
test_unparsable_manifest if {
  findings := policy.deny with input as data.fixtures.unparsable_manifest
  profile(findings) == {["DF-006", "indeterminate"]}
}

# A Cargo manifest no declared surface governs is reported, not passed over.
test_undeclared_manifest if {
  findings := policy.deny with input as data.fixtures.undeclared_manifest
  profile(findings) == {["DF-006", "indeterminate"]}
}

# A Python project outside the root is reported, not passed over.
test_nested_pyproject if {
  findings := policy.deny with input as data.fixtures.nested_pyproject
  profile(findings) == {["DF-006", "indeterminate"]}
}

# Nothing declared means no relationship applies.
test_no_declarations if {
  findings := policy.deny with input as data.fixtures.no_declarations
  count(findings) == 0
}

# Another package's envelope is refused rather than read.
test_wrong_kind if {
  findings := policy.deny with input as data.fixtures.wrong_kind
  profile(findings) == {["DF-006", "indeterminate"]}
}
