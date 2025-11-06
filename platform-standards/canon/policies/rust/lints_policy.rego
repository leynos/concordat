package canon.rust.lints

import rego.v1

clippy_scalar_lints := {
  "allow_attributes": "deny",
  "allow_attributes_without_reason": "deny",
  "blanket_clippy_restriction_lints": "deny",
  "cognitive_complexity": "deny",
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
  actual := object.get(clippy, key, "")
  actual != required
  msg := sprintf("clippy lint %s must equal %q", [key, required])
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
