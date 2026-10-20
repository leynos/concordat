package canon.lint_rules.rust_dev_build_footprint_test

import data.canon.lint_rules.rust_dev_build_footprint as policy
import rego.v1

small := data.fixtures.nightly.surfaces[0]

envelope(surface) := {
	"schema_version": 1, "kind": "policy-input/rust-dev-build-footprint",
	"surfaces": [surface],
}

test_small_dev_build_passes if {
	findings := policy.deny with input as envelope(small)
	count(findings) == 0
}

test_stable_requires_no_unstable_flag if {
	findings := policy.deny with input as data.fixtures.stable
	count(findings) == 0
}

test_enabled_settings_are_reported if {
	surface := object.union(small, {"profiles": [{"source": "profile.dev", "debug": "enabled", "incremental": "enabled"}]})
	findings := policy.deny with input as envelope(surface)
	{f.rule_id | some f in findings} == {"DF-001", "DF-002"}
}

test_bad_values_are_indeterminate if {
	surface := object.union(small, {"profiles": [{"source": "profile.dev", "debug": "invalid", "incremental": "disabled"}]})
	findings := policy.deny with input as envelope(surface)
	{[f.rule_id, f.verdict] | some f in findings} == {["DF-002", "indeterminate"]}
}

test_missing_envelope_is_not_a_pass if {
	findings := policy.deny with input as {}
	{f.rule_id | some f in findings} == {"DF-000"}
}

test_missing_profile_facts_are_not_a_pass if {
	surface := object.union(small, {"profiles": []})
	findings := policy.deny with input as envelope(surface)
	{f.rule_id | some f in findings} == {"DF-000"}
}

test_unset_base_defaults_are_not_a_pass if {
	surface := object.union(small, {"profiles": [{"source": "profile.dev", "debug": "unset", "incremental": "unset"}]})
	findings := policy.deny with input as envelope(surface)
	{f.rule_id | some f in findings} == {"DF-000"}
}

test_unknown_support_is_not_a_pass if {
	surface := object.union(small, {"metadata_support": "unknown"})
	findings := policy.deny with input as envelope(surface)
	{[f.rule_id, f.verdict] | some f in findings} == {["DF-003", "indeterminate"]}
}
