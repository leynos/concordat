package canon.lint_rules.nested_cargo_offline_tests_test

import data.canon.lint_rules.nested_cargo_offline_tests as policy
import rego.v1

exemptions := [{
	"repository": "leynos/example",
	"path": "installer/src/builder.rs",
	"rationale": "the installer smoke test must install from crates.io",
	"review_trigger": "review when the installer stops downloading a published crate",
}]

entry_point_exemption := [{
	"repository": "leynos/example",
	"path": "Makefile",
	"rationale": "the vendored toolchain lane owns its own fetch",
	"review_trigger": "review when the lane is retired",
}]

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as {"exemptions": []}
}

profile(fixture) := {[f.rule_id, f.verdict, f.path] | some f in findings(fixture)}

severities(fixture) := {[f.rule_id, f.severity] | some f in findings(fixture)}

test_an_offline_spawn_is_compliant if {
	count(findings(data.fixtures.compliant)) == 0
}

test_a_command_local_offline_variable_is_compliant if {
	count(findings(data.fixtures.online_cargo_net_offline)) == 0
}

test_a_prefetch_before_the_tests_is_compliant if {
	count(findings(data.fixtures.prefetch_before)) == 0
}

test_an_online_spawn_is_noncompliant if {
	profile(data.fixtures.online_spawn) == {["NC-007", "noncompliant", "tests/build_discovery_bdd.rs"]}
}

test_an_absent_entry_point_is_noncompliant if {
	profile(data.fixtures.absent_prefetch) == {["NC-008", "noncompliant", "Makefile"]}
}

test_trybuild_evidence_alone_makes_an_entry_point_count if {
	profile(data.fixtures.absent_with_trybuild) == {["NC-008", "noncompliant", ".github/workflows/ci.yml"]}
}

# A checkout that spawns nothing and uses no trybuild has nothing for a
# prefetch to protect, so its cold entry point is not a finding.
test_an_entry_point_without_evidence_is_not_judged if {
	count(findings(data.fixtures.no_spawn_or_trybuild)) == 0
}

test_an_unresolved_spawn_is_indeterminate if {
	profile(data.fixtures.unresolved_spawn) == {["EN-001", "indeterminate", "tests/forwarded_args.rs"]}
}

test_an_uncertain_entry_point_is_indeterminate if {
	profile(data.fixtures.uncertain_prefetch) == {
		["NC-007", "noncompliant", "tests/build_suite_bdd.rs"],
		["EN-001", "indeterminate", "Makefile"],
	}
}

test_unreadable_evidence_is_indeterminate if {
	profile(data.fixtures.unreadable_evidence) == {["EN-001", "indeterminate", ""]}
}

# The default is survey mode: a warning finding is reported but does not block.
test_findings_carry_warning_severity_by_default if {
	severities(data.fixtures.online_spawn) == {["NC-007", "warning"]}
}

test_the_severity_parameter_reaches_the_finding if {
	fs := policy.deny with input as data.fixtures.online_spawn with data.parameters as {"exemptions": [], "severity": "error"}
	{[f.rule_id, f.severity] | some f in fs} == {["NC-007", "error"]}
}

# An envelope defect is the rule's own failure, not a repository's, so it stays
# an error however the rule is configured.
test_a_missing_envelope_is_an_error_not_a_warning if {
	fs := policy.deny with input as {} with data.parameters as {"exemptions": [], "severity": "warning"}
	{[f.rule_id, f.severity, f.verdict] | some f in fs} == {["EN-001", "error", "indeterminate"]}
}

test_a_foreign_envelope_kind_is_an_error_not_a_warning if {
	wrong := object.union(data.fixtures.compliant, {"kind": "policy-input/nested-cargo-fixture-lockfiles"})
	fs := policy.deny with input as wrong with data.parameters as {"exemptions": [], "severity": "warning"}
	{[f.rule_id, f.severity] | some f in fs} == {["EN-001", "error"]}
}

test_a_named_exemption_suppresses_the_spawn_finding if {
	fs := policy.deny with input as data.fixtures.exempt_spawn with data.parameters as {"exemptions": exemptions}
	profile := {[f.rule_id, f.path] | some f in fs}
	profile == {["NC-007", "tests/build_discovery_bdd.rs"], ["NC-008", "Makefile"]}
}

test_a_named_exemption_suppresses_the_entry_point_finding if {
	fs := policy.deny with input as data.fixtures.exempt_entry_point with data.parameters as {"exemptions": entry_point_exemption}
	count(fs) == 0
}

# An exemption names a repository: the same file elsewhere is audited.
test_an_exemption_does_not_travel if {
	fs := policy.deny with input as data.fixtures.named_elsewhere with data.parameters as {"exemptions": exemptions}
	{f.rule_id | some f in fs} == {"NC-007"}
}

# An exemption with no rationale or no review trigger asks nobody to revisit
# it, so it is not a record and must not suppress anything.
test_an_empty_rationale_is_not_an_exemption if {
	empty := [{"repository": "leynos/example", "path": "installer/src/builder.rs", "rationale": "", "review_trigger": "t"}]
	fs := policy.deny with input as data.fixtures.named_elsewhere with data.parameters as {"exemptions": empty}
	{f.rule_id | some f in fs} == {"NC-007"}
}

test_an_empty_review_trigger_is_not_an_exemption if {
	empty := [{"repository": "leynos/example", "path": "installer/src/builder.rs", "rationale": "r", "review_trigger": "  "}]
	fs := policy.deny with input as data.fixtures.exempt_spawn with data.parameters as {"exemptions": empty}
	{f.rule_id | some f in fs} == {"NC-007", "NC-008"}
}

# Suppression covers NC-007 and NC-008 only: evidence the reader could not
# establish stays reported.
test_an_exemption_does_not_suppress_indeterminate_evidence if {
	named := [{"repository": "leynos/example", "path": "tests/forwarded_args.rs", "rationale": "r", "review_trigger": "t"}]
	fs := policy.deny with input as data.fixtures.unresolved_spawn with data.parameters as {"exemptions": named}
	{f.rule_id | some f in fs} == {"EN-001"}
}

# A path-only match would hide a finding elsewhere; matching is exact.
test_an_exemption_covers_only_its_file if {
	other := [{"repository": "leynos/example", "path": "installer/src/other.rs", "rationale": "r", "review_trigger": "t"}]
	fs := policy.deny with input as data.fixtures.named_elsewhere with data.parameters as {"exemptions": other}
	{f.rule_id | some f in fs} == {"NC-007"}
}

test_a_noncompliant_finding_names_the_command_and_the_remedy if {
	some f in findings(data.fixtures.online_spawn)
	f.rule_id == "NC-007"
	contains(f.msg, "tests/build_discovery_bdd.rs:80")
	contains(f.msg, "--frozen")
}

test_a_prefetch_finding_names_the_entry_point_and_the_remedy if {
	some f in findings(data.fixtures.absent_prefetch)
	f.rule_id == "NC-008"
	contains(f.msg, "Makefile:27")
	contains(f.msg, "cargo fetch --locked")
}
