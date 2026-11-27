package canon.lint_rules.whitaker_exclusions_test

import data.canon.lint_rules.whitaker_exclusions as policy
import rego.v1

default_parameters := {"severity": "warning", "crate_exclusions": []}

with_repository(fixture) := object.union(fixture, {"repository": {"name": "leynos/example", "path": "."}})

findings_with(fixture, parameters) := fs if {
	fs := policy.deny with input as with_repository(fixture) with data.parameters as parameters
}

findings(fixture) := findings_with(fixture, default_parameters)

ids(fixture) := {f.rule_id | some f in findings(fixture)}

messages_of(fixture, id) := [f.msg | some f in findings(fixture); f.rule_id == id]

named(crate) := {
	"repository": "leynos/example",
	"crate": crate,
	"reason": "the crate stages ambient files",
	"review_trigger": "review when the loader takes a Dir",
}

with_named(fixture, list) := findings_with(fixture, object.union(default_parameters, {"crate_exclusions": list}))

# -- nothing to judge --------------------------------------------------------------

test_a_checkout_without_a_dylint_config_is_clean if {
	count(findings(data.fixtures.no_dylint_config)) == 0
}

test_a_config_without_exclusions_is_clean if {
	count(findings(data.fixtures.config_without_exclusions)) == 0
}

# -- WX-001 -------------------------------------------------------------------------

test_a_reason_above_the_key_is_enough if {
	count(findings(data.fixtures.designed_reason_above_key)) == 0
}

test_a_reason_above_the_table_header_is_enough if {
	count(findings(data.fixtures.designed_reason_above_table)) == 0
}

test_a_reason_above_a_root_level_key_is_enough if {
	count(findings(data.fixtures.designed_root_level_key_with_reason)) == 0
}

test_a_path_exclusion_without_a_reason_is_wx_001 if {
	ids(data.fixtures.designed_paths_without_reason) == {"WX-001"}
	[msg] := messages_of(data.fixtures.designed_paths_without_reason, "WX-001")
	contains(msg, "[no_std_fs_operations] excluded_paths in dylint.toml")
	contains(msg, "comment block directly above the key")
}

test_the_finding_carries_the_line_of_the_key if {
	some f in findings(data.fixtures.designed_paths_without_reason)
	f.line == 2
	f.path == "dylint.toml"
}

test_a_blank_line_ends_the_reason if {
	ids(data.fixtures.designed_blank_line_ends_the_reason) == {"WX-001"}
}

test_a_bare_comment_marker_is_no_reason if {
	ids(data.fixtures.designed_bare_hash_is_no_reason) == {"WX-001"}
}

test_any_excluded_key_needs_a_reason if {
	[msg] := messages_of(data.fixtures.designed_other_exclusion_key_without_reason, "WX-001")
	contains(msg, "excluded_modules")
}

test_a_grown_workspace_path_exclusion_without_a_reason_is_wx_001 if {
	ids(data.fixtures.grown_paths_without_reason) == {"WX-001"}
}

test_a_grown_workspace_with_a_reason_is_clean if {
	count(findings(data.fixtures.grown_reason_above_key)) == 0
}

test_an_extension_path_exclusion_without_a_reason_is_wx_001 if {
	some f in findings(data.fixtures.extension_paths_without_reason)
	f.rule_id == "WX-001"
	f.path == "rust_extension/dylint.toml"
}

# -- WX-002 -------------------------------------------------------------------------

test_excluding_a_whole_governed_crate_is_wx_002 if {
	ids(data.fixtures.designed_governed_crate_excluded) == {"WX-002"}
	[msg] := messages_of(data.fixtures.designed_governed_crate_excluded, "WX-002")
	contains(msg, "excludes 1 whole crate that this repository governs")
	contains(msg, "Whitaker lint: a.")
	contains(msg, "Prefer `excluded_paths`")
}

test_naming_the_crate_with_a_reason_and_trigger_allows_it if {
	count(with_named(data.fixtures.designed_governed_crate_excluded, [named("a")])) == 0
}

test_a_blank_reason_does_not_allow_it if {
	blank := object.union(named("a"), {"reason": " "})
	ids_for := {f.rule_id | some f in with_named(data.fixtures.designed_governed_crate_excluded, [blank])}
	ids_for == {"WX-002"}
}

test_a_blank_trigger_does_not_allow_it if {
	blank := object.union(named("a"), {"review_trigger": ""})
	count(with_named(data.fixtures.designed_governed_crate_excluded, [blank])) == 1
}

test_naming_another_repositorys_crate_allows_nothing if {
	other := object.union(named("a"), {"repository": "leynos/other"})
	count(with_named(data.fixtures.designed_governed_crate_excluded, [other])) == 1
}

test_a_hyphenated_package_is_matched_by_its_rustc_name if {
	count(findings(data.fixtures.designed_governed_crate_excluded_by_rustc_name)) == 1
	count(with_named(data.fixtures.designed_governed_crate_excluded_by_rustc_name, [named("my-crate")])) == 0
}

test_excluding_the_root_package_of_a_grown_workspace_is_wx_002 if {
	ids(data.fixtures.grown_governed_crate_excluded) == {"WX-002"}
}

test_the_femtologging_shape_is_wx_002_for_each_whole_crate_until_named if {
	[msg] := messages_of(data.fixtures.extension_femtologging_shape, "WX-002")
	contains(msg, "excludes 3 whole crates")
	contains(msg, "_femtologging_rs, file_handler_tests, heavy")
	partial := with_named(data.fixtures.extension_femtologging_shape, [named("_femtologging_rs"), named("file_handler_tests")])
	[remaining] := [f.msg | some f in partial]
	contains(remaining, "excludes 1 whole crate that")
	contains(remaining, ": heavy.")
	all := [named("_femtologging_rs"), named("file_handler_tests"), named("heavy")]
	count(with_named(data.fixtures.extension_femtologging_shape, all)) == 0
}

test_a_test_target_is_named_after_its_file if {
	[msg] := messages_of(data.fixtures.designed_test_crate_by_file_name, "WX-002")
	contains(msg, "handler_tests")
}

test_a_binary_target_is_a_governed_crate if {
	ids(data.fixtures.designed_binary_crate_excluded) == {"WX-002"}
}

test_fixture_and_vendored_crates_need_no_entry if {
	count(findings(data.fixtures.designed_fixture_and_vendored_crates_excluded)) == 0
}

# -- WX-003 -------------------------------------------------------------------------

test_an_exclusion_naming_nothing_is_stale if {
	ids(data.fixtures.designed_stale_crate_exclusion) == {"WX-003"}
	[msg] := messages_of(data.fixtures.designed_stale_crate_exclusion, "WX-003")
	contains(msg, "ghost")
	contains(msg, "hyphens are underscores")
}

test_a_registry_dependency_is_not_a_crate_of_the_repository if {
	ids(data.fixtures.designed_registry_dependency_is_not_a_crate) == {"WX-003"}
}

test_a_named_crate_exclusion_does_not_hide_a_stale_one if {
	ids_for := {f.rule_id | some f in with_named(data.fixtures.designed_stale_crate_exclusion, [named("ghost")])}
	ids_for == {"WX-003"}
}

# -- indeterminate, never guessed -------------------------------------------------------

test_an_unreadable_dylint_config_is_en_001 if {
	ids(data.fixtures.unreadable_dylint_config) == {"EN-001"}
	some f in findings(data.fixtures.unreadable_dylint_config)
	f.verdict == "indeterminate"
}

test_an_unreadable_manifest_makes_a_missing_crate_indeterminate_not_stale if {
	ids(data.fixtures.unreadable_manifest_blocks_stale) == {"EN-001"}
}

test_a_key_the_text_scan_cannot_find_is_en_001 if {
	some f in findings(data.fixtures.dotted_key_is_not_located)
	f.rule_id == "EN-001"
	contains(f.msg, "could not find")
	not "WX-001" in ids(data.fixtures.dotted_key_is_not_located)
}

test_an_exclusion_that_is_not_a_list_of_strings_is_en_001 if {
	some f in findings(data.fixtures.exclusion_that_is_not_a_list)
	f.rule_id == "EN-001"
	contains(f.msg, "not a list of strings")
}

test_a_document_of_another_kind_is_an_envelope_error if {
	fs := policy.deny with input as {"schema_version": 1, "kind": "policy-input/other"}
		with data.parameters as default_parameters
	some f in fs
	f.rule_id == "EN-001"
	f.severity == "error"
}

# -- severity -------------------------------------------------------------------------

test_findings_default_to_warning if {
	every f in findings(data.fixtures.designed_paths_without_reason) {
		f.severity == "warning"
	}
}

test_the_severity_parameter_promotes_findings_to_error if {
	fs := findings_with(data.fixtures.designed_paths_without_reason, object.union(default_parameters, {"severity": "error"}))
	every f in fs {
		f.severity == "error"
	}
}
