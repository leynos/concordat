package canon.rust.lints

import rego.v1

clippy_scalar_lints := {
  "allow_attributes": "deny",
  "allow_attributes_without_reason": "deny",
  "blanket_clippy_restriction_lints": "deny",
  "cognitive_complexity": "deny",
  "disallowed_methods": "deny",
  "needless_pass_by_value": "deny",
  "implicit_hasher": "deny",
  "dbg_macro": "deny",
  "print_stdout": "deny",
  "print_stderr": "deny",
  "unwrap_used": "deny",
  "expect_used": "deny",
  "indexing_slicing": "deny",
  "string_slice": "deny",
  "integer_division": "deny",
  "integer_division_remainder_used": "deny",
  "panic_in_result_fn": "deny",
  "unreachable": "deny",
  "host_endian_bytes": "deny",
  "little_endian_bytes": "deny",
  "big_endian_bytes": "deny",
  "let_underscore_must_use": "deny",
  "or_fun_call": "deny",
  "option_if_let_else": "deny",
  "self_named_module_files": "deny",
  "shadow_reuse": "deny",
  "shadow_same": "deny",
  "shadow_unrelated": "deny",
  "str_to_string": "deny",
  "string_lit_as_bytes": "deny",
  "try_err": "deny",
  "unneeded_field_pattern": "deny",
  "use_self": "deny",
  "float_arithmetic": "deny",
  "cast_possible_truncation": "deny",
  "cast_possible_wrap": "deny",
  "cast_precision_loss": "deny",
  "lossy_float_literal": "deny",
  "missing_const_for_fn": "deny",
  "must_use_candidate": "deny",
  "unused_async": "deny",
  "missing_panics_doc": "deny",
  "error_impl_error": "deny",
  "result_large_err": "deny",
}

rust_required_lints := {
  "unknown_lints": "deny",
  "renamed_and_removed_lints": "deny",
  "missing_docs": "deny",
}

rustdoc_required_lints := {
  "missing_crate_level_docs": "deny",
  "broken_intra_doc_links": "deny",
  "private_intra_doc_links": "deny",
  "bare_urls": "deny",
  "invalid_html_tags": "deny",
  "invalid_codeblock_attributes": "deny",
  "unescaped_backticks": "deny",
}

clippy_thresholds := {
  "cognitive-complexity-threshold": 9,
  "too-many-arguments-threshold": 4,
  "too-many-lines-threshold": 70,
  "excessive-nesting-threshold": 4,
  "allow-expect-in-tests": true,
}

enforcing_lint_level(level) if {
  level == "deny"
}

enforcing_lint_level(level) if {
  level == "forbid"
}

# Cargo 1.92 declares this field as i8, so enforce its numeric domain. OPA
# evaluates normalized JSON numbers here, so validate integrality, not spelling.
# Schema source: https://github.com/rust-lang/cargo/blob/344c4567c634a25837e3c3476aac08af84cf9203/crates/cargo-util-schemas/src/manifest/mod.rs
integral_numeric_priority(priority) if {
  is_number(priority)
  floor(priority) == priority
  priority >= -128
  priority <= 127
}

valid_disallowed_methods_lint_setting(setting) if {
  is_string(setting)
  enforcing_lint_level(setting)
}

valid_disallowed_methods_lint_setting(setting) if {
  is_object(setting)
  enforcing_lint_level(object.get(setting, "level", null))
  count(object.keys(setting)) == 1
}

valid_disallowed_methods_lint_setting(setting) if {
  is_object(setting)
  enforcing_lint_level(object.get(setting, "level", null))
  integral_numeric_priority(object.get(setting, "priority", null))
  count(object.keys(setting)) == 2
}

clippy_disallowed_methods := [
  {"path": "std::env::var", "reason": "inject an environment reader"},
  {"path": "std::env::var_os", "reason": "inject an environment reader"},
  {"path": "std::env::vars", "reason": "inject an environment reader"},
  {"path": "std::env::vars_os", "reason": "inject an environment reader"},
  {"path": "std::env::set_var", "reason": "use a stub environment in tests"},
  {"path": "std::env::remove_var", "reason": "use a stub environment in tests"},
  {
    "path": "std::env::set_current_dir",
    "reason": "inject a base-directory seam; confine CWD changes to Command::current_dir",
  },
]

contains_disallowed_method(methods, expected) if {
  some method in methods
  is_object(method)
  method.path == expected.path
  method.reason == expected.reason
}

disallowed_method_object_fields := {"path", "reason", "replacement", "allow-invalid"}

optional_string_field_is_valid(method, field) if {
  not field in object.keys(method)
}

optional_string_field_is_valid(method, field) if {
  field in object.keys(method)
  value := object.get(method, field, null)
  is_string(value)
}

optional_boolean_field_is_valid(method, field) if {
  not field in object.keys(method)
}

optional_boolean_field_is_valid(method, field) if {
  field in object.keys(method)
  value := object.get(method, field, null)
  is_boolean(value)
}

valid_disallowed_method(method) if {
  is_string(method)
  method != ""
}

valid_disallowed_method(method) if {
  is_object(method)
  path := object.get(method, "path", null)
  is_string(path)
  path != ""
  every field in object.keys(method) {
    field in disallowed_method_object_fields
  }
  optional_string_field_is_valid(method, "reason")
  optional_string_field_is_valid(method, "replacement")
  optional_boolean_field_is_valid(method, "allow-invalid")
}

cargo_manifest := object.get(input, "cargo", null)
workspace_block := object.get(cargo_manifest, "workspace", null)
workspace_lints := object.get(workspace_block, "lints", null)
root_lints := object.get(cargo_manifest, "lints", null)
clippy_config := object.get(input, "clippy", null)

lint_scope := workspace_lints if {
  workspace_block != null
  workspace_lints != null
}

lint_scope := root_lints if {
  workspace_block == null
  root_lints != null
}

lint_scope_defined if {
  lint_scope
}

deny contains "Cargo workspace must define [workspace.lints]" if {
  cargo_manifest != null
  workspace_block != null
  workspace_lints == null
}

deny contains "Cargo manifest must define [lints] when no workspace table exists" if {
  cargo_manifest != null
  workspace_block == null
  root_lints == null
}

deny contains "Cargo lint table must define clippy lints" if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  object.get(scope, "clippy", null) == null
}

deny contains "Cargo lint table must define rust lints" if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  object.get(scope, "rust", null) == null
}

deny contains "Cargo lint table must define rustdoc lints" if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  object.get(scope, "rustdoc", null) == null
}

deny contains msg if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  clippy := scope["clippy"]
  required := clippy_scalar_lints[key]
  key != "disallowed_methods"
  actual := object.get(clippy, key, "")
  actual != required
  msg := sprintf("clippy lint %s must equal %q", [key, required])
}

deny contains "clippy lint disallowed_methods must use deny or forbid" if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  clippy := scope["clippy"]
  actual := object.get(clippy, "disallowed_methods", null)
  not valid_disallowed_methods_lint_setting(actual)
}

deny contains msg if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  clippy := scope["clippy"]
  not clippy["pedantic"]
  msg := "clippy pedantic lint must set level=\"warn\" and priority=-1"
}

deny contains msg if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  clippy := scope["clippy"]
  pedantic := clippy["pedantic"]
  object.get(pedantic, "level", "") != "warn"
  msg := "clippy pedantic lint must set level=\"warn\" and priority=-1"
}

deny contains msg if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  clippy := scope["clippy"]
  pedantic := clippy["pedantic"]
  object.get(pedantic, "priority", 0) != -1
  msg := "clippy pedantic lint must set level=\"warn\" and priority=-1"
}

deny contains msg if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  rust := scope["rust"]
  required := rust_required_lints[key]
  actual := object.get(rust, key, "")
  actual != required
  msg := sprintf("rust lint %s must equal %q", [key, required])
}

deny contains msg if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  rustdoc := scope["rustdoc"]
  required := rustdoc_required_lints[key]
  actual := object.get(rustdoc, key, "")
  actual != required
  msg := sprintf("rustdoc lint %s must equal %q", [key, required])
}

deny contains "clippy.toml must exist when Cargo.toml is present" if {
  cargo_manifest != null
  clippy_config == null
}

deny contains msg if {
  cargo_manifest != null
  clippy_config != null
  expected := clippy_thresholds[key]
  actual := object.get(clippy_config, key, null)
  actual != expected
  msg := sprintf("clippy.toml %s must equal %v", [key, expected])
}

deny contains "clippy.toml disallowed-methods must define the seven approved std::env entries" if {
  cargo_manifest != null
  clippy_config != null
  actual := object.get(clippy_config, "disallowed-methods", null)
  actual == null
}

deny contains "clippy.toml disallowed-methods must be an array" if {
  cargo_manifest != null
  clippy_config != null
  actual := object.get(clippy_config, "disallowed-methods", null)
  actual != null
  not is_array(actual)
}

deny contains msg if {
  cargo_manifest != null
  clippy_config != null
  actual := object.get(clippy_config, "disallowed-methods", null)
  is_array(actual)
  expected := clippy_disallowed_methods[_]
  not contains_disallowed_method(actual, expected)
  msg := sprintf(
    "clippy.toml disallowed-methods must include %s with reason %q",
    [expected.path, expected.reason],
  )
}

deny contains "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema" if {
  cargo_manifest != null
  clippy_config != null
  actual := object.get(clippy_config, "disallowed-methods", null)
  is_array(actual)
  some method in actual
  not valid_disallowed_method(method)
}

deny contains "clippy.toml disallowed-methods paths must be unique" if {
  cargo_manifest != null
  clippy_config != null
  actual := object.get(clippy_config, "disallowed-methods", null)
  is_array(actual)
  bare_method_paths := [path |
    some path in actual
    is_string(path)
    path != ""
  ]
  table_method_paths := [path |
    some method in actual
    is_object(method)
    path := object.get(method, "path", null)
    is_string(path)
    path != ""
  ]
  actual_paths := array.concat(bare_method_paths, table_method_paths)
  unique_paths := {path | some path in actual_paths}
  count(actual_paths) != count(unique_paths)
}
