# BD-010: a recorded exception waives its clauses only when it is substantiated.
package canon.lint_rules.rust_build_defaults_standard_exceptions_test

import rego.v1

import data.canon.lint_rules.rust_build_defaults as policy

profile(findings) := {[f.rule_id, f.verdict] | some f in findings}

messages(findings, rule_id) := {f.msg | some f in findings; f.rule_id == rule_id}

# The baseline: with no exception the explicit fast route is just a repository
# that never adopted the standard.
test_without_an_exception_the_standard_is_unmet if {
	findings := policy.deny with input as data.fixtures.exception_undeclared
	profile(findings) == {["BD-002", "noncompliant"], ["BD-004", "noncompliant"], ["BD-008", "noncompliant"]}
}

# cuprum's shape: the route carries the frontend and the Linux linker, the
# record exists, and the waived clauses stop reporting.
test_a_substantiated_exception_waives_its_clauses if {
	findings := policy.deny with input as data.fixtures.exception_dev_fast
	count(findings) == 0
}

# The fast path itself must still meet the standard.
test_a_route_without_the_linker_waives_nothing if {
	findings := policy.deny with input as data.fixtures.exception_route_without_linker
	profile(findings) == {
		["BD-002", "noncompliant"], ["BD-004", "noncompliant"], ["BD-008", "noncompliant"],
		["BD-010", "noncompliant"],
	}
	messages(findings, "BD-010") == {"the recorded exception for BD-002, BD-004, BD-008 is not substantiated: the route tools/dev-fast/config.toml names no Linux rustflags source carrying -Clink-arg=-fuse-ld=mold"}
}

test_a_route_without_the_frontend_waives_nothing if {
	findings := policy.deny with input as data.fixtures.exception_route_without_threads
	messages(findings, "BD-010") == {"the recorded exception for BD-002, BD-004, BD-008 is not substantiated: the route tools/dev-fast/config.toml does not carry -Zthreads=8 in every rustflags source"}
	["BD-002", "noncompliant"] in profile(findings)
}

test_an_absent_route_waives_nothing if {
	findings := policy.deny with input as data.fixtures.exception_route_absent
	messages(findings, "BD-010") == {"the recorded exception for BD-002, BD-004, BD-008 is not substantiated: the route tools/dev-fast/config.toml does not exist"}
	["BD-004", "noncompliant"] in profile(findings)
}

test_a_missing_decision_record_waives_nothing if {
	findings := policy.deny with input as data.fixtures.exception_adr_absent
	messages(findings, "BD-010") == {"the recorded exception for BD-002, BD-004, BD-008 is not substantiated: the decision record docs/adr-fast.md does not exist"}
	["BD-008", "noncompliant"] in profile(findings)
}

test_an_empty_rationale_waives_nothing if {
	findings := policy.deny with input as data.fixtures.exception_empty_rationale
	messages(findings, "BD-010") == {"the recorded exception for BD-002, BD-004, BD-008 is not substantiated: the rationale is empty"}
	["BD-002", "noncompliant"] in profile(findings)
}

# An exception cannot excuse a clause that checks what a configuration says.
test_a_clause_outside_the_waivable_set_is_refused if {
	findings := policy.deny with input as data.fixtures.exception_unwaivable_clause
	messages(findings, "BD-010") == {"the recorded exception for BD-002, BD-003 is not substantiated: BD-003 cannot be waived"}
	["BD-002", "noncompliant"] in profile(findings)
}

# The reasons are separate findings, so everything to fix is visible at once.
test_every_problem_is_its_own_finding if {
	exception := object.union(data.fixtures.exception_dev_fast.standard_exceptions[0], {"adr_present": false, "rationale": ""})
	input_with_problems := object.union(data.fixtures.exception_dev_fast, {"standard_exceptions": [exception]})
	findings := policy.deny with input as input_with_problems
	count(messages(findings, "BD-010")) == 2
}

# BD-010 is not itself waivable.
test_an_exception_cannot_waive_bd_010 if {
	exception := object.union(data.fixtures.exception_dev_fast.standard_exceptions[0], {"clauses": ["BD-010"]})
	findings := policy.deny with input as object.union(data.fixtures.exception_dev_fast, {"standard_exceptions": [exception]})
	"BD-010" in {f.rule_id | some f in findings}
}
