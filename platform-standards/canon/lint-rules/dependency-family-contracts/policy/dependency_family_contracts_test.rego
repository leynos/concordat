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

# A renamed bridge left to the catch-all is an unprotected type-identity family.
test_renamed_bridge_unprotected if {
  findings := policy.deny with input as data.fixtures.renamed_bridge_unprotected
  ids(findings) == {"DF-001"}
}

# A group naming the published package takes the bridge with PyO3.
test_renamed_bridge_grouped_by_published_name if {
  findings := policy.deny with input as data.fixtures.renamed_bridge_grouped_by_published_name
  count(findings) == 0
}

# A group naming only the alias takes nothing: Dependabot matches the published name.
test_renamed_bridge_grouped_by_alias if {
  findings := policy.deny with input as data.fixtures.renamed_bridge_grouped_by_alias
  ids(findings) == {"DF-001"}
}

# An ignore rule on a member protects an independently versioned tuple.
test_maturin_pyo3_ignored if {
  findings := policy.deny with input as data.fixtures.maturin_pyo3_ignored
  count(findings) == 0
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

# An undeclared manifest naming no related package draws no finding.
test_undeclared_unrelated_manifest if {
  findings := policy.deny with input as data.fixtures.undeclared_unrelated_manifest
  count(findings) == 0
}

# An undeclared manifest that cannot be decoded is indeterminate.
test_undeclared_unparsable_manifest if {
  findings := policy.deny with input as data.fixtures.undeclared_unparsable_manifest
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

# maturin and PyO3 join one multi-ecosystem group: they arrive in one pull
# request, so the tuple is protected without a bound or an ignore rule.
test_maturin_multi_group if {
  findings := policy.deny with input as data.fixtures.maturin_multi_group
  count(findings) == 0
}

# Only one member joins the group, so the other still arrives alone.
test_maturin_multi_group_one_member if {
  findings := policy.deny with input as data.fixtures.maturin_multi_group_one_member
  ids(findings) == {"DF-002"}
}

# Members in different groups arrive in different pull requests.
test_maturin_multi_group_split if {
  findings := policy.deny with input as data.fixtures.maturin_multi_group_split
  ids(findings) == {"DF-002"}
}

# A group the file never defines protects nothing.
test_maturin_multi_group_undefined if {
  findings := policy.deny with input as data.fixtures.maturin_multi_group_undefined
  ids(findings) == {"DF-002"}
}

# Patterns that miss a member leave it outside the group.
test_maturin_multi_group_patterns_miss if {
  findings := policy.deny with input as data.fixtures.maturin_multi_group_patterns_miss
  ids(findings) == {"DF-002"}
}

# A member of the root workspace is read through the governed root, so a split
# PyO3 family in the member is reported with the member's path.
test_workspace_member_split if {
  findings := policy.deny with input as data.fixtures.workspace_member_split
  ids(findings) == {"DF-001"}
  some finding in findings
  finding.path == "native/Cargo.toml"
}

test_workspace_member_grouped if {
  findings := policy.deny with input as data.fixtures.workspace_member_grouped
  count(findings) == 0
}

# The shape Dependabot needs: the group entry takes pyo3, its build crate and
# maturin into one pull request, and a plain entry ignores them and keeps the
# rest. One group takes the family together and protects the tuple.
test_maturin_two_entries if {
  findings := policy.deny with input as data.fixtures.maturin_two_entries
  count(findings) == 0
}

# A lone group entry takes only pyo3, so the family is split: its build crate
# has no group at all.
test_family_lone_group_entry if {
  findings := policy.deny with input as data.fixtures.family_lone_group_entry
  ids(findings) == {"DF-001"}
}
