package canon.lint_rules.rust_prover_tooling_test

import data.canon.lint_rules.rust_prover_tooling as policy
import rego.v1

parameters := {
	"prover_tools_ref": "592558f7183b554fc749696246984c5d7300d029",
	"waivable_clauses": ["PT-001", "PT-002", "PT-004"],
}

findings(fixture) := fs if {
	fs := policy.deny with input as fixture with data.parameters as parameters
}

ids(fixture) := {f.rule_id | some f in findings(fixture)}

verdicts(fixture) := {f.verdict | some f in findings(fixture)}

test_a_repository_on_the_reference_pin_and_the_shared_action_is_compliant if {
	count(findings(data.fixtures.compliant)) == 0
}

test_a_step_that_only_runs_proofs_is_not_an_installer if {
	count(findings(data.fixtures.compliant_run_only)) == 0
}

test_a_repository_that_uses_no_prover_is_not_audited if {
	count(findings(data.fixtures.not_applicable)) == 0
}

test_a_repository_with_no_workflows_and_no_prover_is_not_audited if {
	count(findings(data.fixtures.not_applicable_no_workflows)) == 0
}

test_no_pin_is_noncompliant if {
	ids(data.fixtures.no_pin) == {"PT-001"}
}

test_two_pins_are_noncompliant if {
	ids(data.fixtures.second_pin) == {"PT-001"}
}

test_the_wrong_pin_is_noncompliant_and_named if {
	ids(data.fixtures.wrong_pin) == {"PT-001"}
	some f in findings(data.fixtures.wrong_pin)
	contains(f.msg, "9c4a27a86c678ceac1155404f45d95ee4a14a172")
}

test_a_ci_installer_step_is_noncompliant_and_named if {
	ids(data.fixtures.ci_installer) == {"PT-002"}
	some f in findings(data.fixtures.ci_installer)
	contains(f.msg, "verify/3")
	contains(f.msg, "make verus")
}

test_a_floating_action_ref_is_noncompliant if {
	ids(data.fixtures.shared_floating_tag) == {"PT-003"}
}

test_a_short_sha_is_noncompliant if {
	ids(data.fixtures.shared_short_sha) == {"PT-003"}
}

test_a_local_installer_is_noncompliant if {
	ids(data.fixtures.local_installer) == {"PT-004"}
}

test_a_test_of_installation_is_noncompliant if {
	ids(data.fixtures.install_test) == {"PT-005"}
}

test_an_exception_in_force_waives_its_clauses if {
	count(findings(data.fixtures.excepted)) == 0
}

test_an_exception_whose_pin_moved_waives_nothing if {
	ids(data.fixtures.excepted_pin_moved) == {"EX-001"}
	some f in findings(data.fixtures.excepted_pin_moved)
	contains(f.msg, "not the pinned 9c4a27a8")
}

test_an_exception_without_its_record_waives_nothing if {
	ids(data.fixtures.excepted_record_absent) == {"EX-001", "PT-001"}
}

test_an_exception_cannot_waive_pt_003 if {
	ids(data.fixtures.excepted_unwaivable) == {"EX-001", "PT-001", "PT-003"}
}

test_a_blank_rationale_waives_nothing if {
	ids(data.fixtures.excepted_blank_rationale) == {"EX-001", "PT-001"}
}

test_an_exception_waives_only_the_clauses_it_names if {
	ids(data.fixtures.excepted_does_not_waive_other_clauses) == {"PT-004"}
}

test_an_unreadable_workflow_is_indeterminate if {
	ids(data.fixtures.workflow_unreadable) == {"EN-001"}
	verdicts(data.fixtures.workflow_unreadable) == {"indeterminate"}
}

test_a_refused_makefile_is_indeterminate_where_a_workflow_runs_make if {
	ids(data.fixtures.makefile_refused) == {"EN-001"}
}

test_a_refused_makefile_still_hides_the_pin_where_no_workflow_runs_make if {
	ids(data.fixtures.makefile_refused_no_make_step) == {"EN-001"}
	some f in findings(data.fixtures.makefile_refused_no_make_step)
	contains(f.msg, "revisions its variables name")
}

test_a_refused_makefile_is_harmless_where_the_exception_waives_the_clauses if {
	count(findings(data.fixtures.makefile_refused_but_waived)) == 0
}

test_an_unreadable_workflow_makes_a_repository_audited if {
	ids(data.fixtures.unreadable_workflow_without_other_evidence) == {"EN-001"}
}

test_an_unreadable_file_makes_a_repository_audited if {
	ids(data.fixtures.unreadable_file_without_other_evidence) == {"EN-001"}
}

test_a_mutable_reference_is_not_the_pinned_revision if {
	ids(data.fixtures.mutable_reference) == {"PT-001"}
}

test_a_revision_with_a_suffix_is_not_the_pinned_revision if {
	ids(data.fixtures.suffixed_revision) == {"PT-001"}
}

test_a_make_outside_the_root_is_indeterminate if {
	ids(data.fixtures.make_unresolved) == {"EN-001"}
	some f in findings(data.fixtures.make_unresolved)
	contains(f.msg, "make -C sub verus")
}

test_a_make_outside_the_root_audits_a_repository_with_no_other_evidence if {
	ids(data.fixtures.make_unresolved_alone) == {"EN-001"}
}

test_a_make_outside_the_root_is_harmless_where_the_exception_waives_pt_002 if {
	count(findings(data.fixtures.make_unresolved_but_waived)) == 0
}

test_an_unreadable_file_is_indeterminate if {
	ids(data.fixtures.file_unreadable) == {"EN-001"}
}

test_a_foreign_envelope_is_indeterminate if {
	ids(data.fixtures.not_an_envelope) == {"EN-001"}
}
