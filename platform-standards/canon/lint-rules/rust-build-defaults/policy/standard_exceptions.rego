# BD-010: a recorded exception to the build standard.
#
# A repository may keep the fast flags out of Cargo's auto-discovered
# configuration on purpose: cuprum's wheels build in containers without mold,
# so its linker, parallel frontend and backend live in an explicit route that
# only the `make dev-*` targets pass to Cargo. The exception is declared as data
# in `.concordat`, never as prose, and it waives a clause only when it is
# substantiated, so that the fast path still meets the standard it sidesteps.
package canon.lint_rules.rust_build_defaults

import rego.v1

# The clauses an exception can waive: the ones that ask for the fast flags and
# the backend to be defaults. BD-003, BD-005 to BD-007 and BD-010 check what a
# configuration says or what a build does, and no exception excuses those.
default waivable_clauses := ["BD-001", "BD-002", "BD-004", "BD-008", "BD-009"]

waivable_clauses := data.parameters.waivable_clauses

standard_exceptions := object.get(input, "standard_exceptions", [])

exception_route_carries_the_fast_flags(exception) if {
	exception.route_state == "ok"
	exception.route_threads
	exception.route_linker
}

exception_is_substantiated(exception) if {
	exception_route_carries_the_fast_flags(exception)
	exception.adr_present
	trim_space(exception.rationale) != ""
	trim_space(exception.review_trigger) != ""
	count(exception.clauses) > 0
	every clause in exception.clauses {
		clause in waivable_clauses
	}
}

clause_waived(rule_id) if {
	some exception in standard_exceptions
	rule_id in exception.clauses
	exception_is_substantiated(exception)
}

# Each reason is a separate finding so a repository sees everything to fix.
exception_problems contains [index, text] if {
	some index, exception in standard_exceptions
	exception.route_state == "absent"
	text := sprintf("the route %s does not exist", [exception.route])
}

exception_problems contains [index, text] if {
	some index, exception in standard_exceptions
	exception.route_state == "unreadable"
	text := sprintf("the route %s cannot be read as a Cargo configuration", [exception.route])
}

exception_problems contains [index, text] if {
	some index, exception in standard_exceptions
	exception.route_state == "ok"
	not exception.route_threads
	text := sprintf("the route %s does not carry %s in every rustflags source", [exception.route, threads_flag])
}

exception_problems contains [index, text] if {
	some index, exception in standard_exceptions
	exception.route_state == "ok"
	not exception.route_linker
	text := sprintf("the route %s names no Linux rustflags source carrying %s", [exception.route, linker_flag])
}

exception_problems contains [index, text] if {
	some index, exception in standard_exceptions
	not exception.adr_present
	text := sprintf("the decision record %s does not exist", [exception.adr])
}

exception_problems contains [index, "the rationale is empty"] if {
	some index, exception in standard_exceptions
	trim_space(exception.rationale) == ""
}

exception_problems contains [index, "the review trigger is empty"] if {
	some index, exception in standard_exceptions
	trim_space(exception.review_trigger) == ""
}

exception_problems contains [index, "it waives no clause"] if {
	some index, exception in standard_exceptions
	count(exception.clauses) == 0
}

exception_problems contains [index, text] if {
	some index, exception in standard_exceptions
	some clause in exception.clauses
	not clause in waivable_clauses
	text := sprintf("%s cannot be waived", [clause])
}

deny contains f if {
	applicable
	some [index, problem] in exception_problems
	exception := standard_exceptions[index]
	f := finding(
		"BD-010", "noncompliant", ".concordat",
		sprintf("the recorded exception for %s is not substantiated: %s", [concat(", ", exception.clauses), problem]),
	)
}
