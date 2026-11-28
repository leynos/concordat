package canon.rust.lints

import rego.v1

# The lint levels are the estate baseline that `rust-lint-tables` (LT-002)
# enforces, read from `data.baseline` (`rust-lint-tables/data/baseline.json`,
# derived from `baseline.toml`), so the two policies cannot disagree. Run this
# policy with `--data platform-standards/canon/lint-rules/rust-lint-tables/data/baseline.json`.
# Without that data it denies instead of passing silently.
default baseline := {}

baseline := data.baseline

baseline_loaded if count(baseline) > 0

deny contains "baseline data is not loaded; pass --data .../rust-lint-tables/data/baseline.json" if {
  not baseline_loaded
}

# `pedantic` is a group with its own level and priority, checked below.
clippy_scalar_lints := {key: spec.level |
  some key, spec in object.get(baseline, "clippy", {})
  key != "pedantic"
}

rust_required_lints := {key: spec.level | some key, spec in object.get(baseline, "rust", {})}

rustdoc_required_lints := {key: spec.level | some key, spec in object.get(baseline, "rustdoc", {})}

# A lint meets its baseline level when it equals it. A baseline level weaker than
# `deny` (`unexpected_cfgs` at `warn`) also accepts a stricter one.
level_rank := {"allow": 0, "warn": 1, "deny": 2, "forbid": 3}

meets_level(actual, required) if actual == required

meets_level(actual, required) if {
  required != "deny"
  level_rank[actual] > level_rank[required]
}

clippy_thresholds := {
  "cognitive-complexity-threshold": object.get(object.get(baseline, "cognitive", {}), "threshold", 9),
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

# Cognitive complexity is gated either by Clippy (deny plus a threshold of 9)
# or by Whitaker's macro-aware lint opted in through dylint.toml with Clippy's
# threshold removed, so only one gate owns the budget.
macro_aware_lint := object.get(object.get(baseline, "cognitive", {}), "macro_aware_lint", "macro_aware_cognitive_complexity")

macro_aware_max_complexity_ceiling := object.get(object.get(baseline, "cognitive", {}), "macro_aware_ceiling", 9)

dylint_config := object.get(input, "dylint", {})

dylint_experimental_lints := experimental if {
  is_object(dylint_config)
  whitaker := object.get(dylint_config, "whitaker", {})
  is_object(whitaker)
  experimental := object.get(whitaker, "experimental", null)
  is_array(experimental)
  # Whitaker reads the list as Vec<String> and ends the compilation otherwise,
  # so a list with any non-string entry is not a working opt-in.
  every entry in experimental {
    is_string(entry)
  }
}

macro_aware_opted_in if {
  macro_aware_lint in dylint_experimental_lints
}

macro_aware_max_complexity := max_complexity if {
  is_object(dylint_config)
  table := object.get(dylint_config, macro_aware_lint, {})
  is_object(table)
  max_complexity := object.get(table, "max_complexity", null)
}

macro_aware_max_complexity_valid if {
  is_number(macro_aware_max_complexity)
  floor(macro_aware_max_complexity) == macro_aware_max_complexity
  macro_aware_max_complexity >= 1
  macro_aware_max_complexity <= macro_aware_max_complexity_ceiling
}

# Whitaker deserializes the table with `deny_unknown_fields` and falls back to
# its default limit of 25 when that fails, so an extra key would leave the gate
# far weaker than the declared max_complexity. The `max_complexity` TOML type
# (a float such as 9.0 is also rejected by Whitaker) is invisible here, because
# normalized JSON numbers do not keep it: the decoder that builds `dylint` must
# refuse non-integer TOML numbers.
macro_aware_known_fields := {"max_complexity"}

macro_aware_table_has_only_known_fields if {
  is_object(dylint_config)
  table := object.get(dylint_config, macro_aware_lint, {})
  is_object(table)
  count({field | some field, _ in table; not field in macro_aware_known_fields}) == 0
}

clippy_threshold_removed if {
  object.get(object.get(input, "clippy", {}), "cognitive-complexity-threshold", null) == null
}

macro_aware_gate_active if {
  macro_aware_opted_in
  macro_aware_max_complexity_valid
  macro_aware_table_has_only_known_fields
  clippy_threshold_removed
}

macro_aware_replaces_clippy_lint(key) if {
  key == "cognitive_complexity"
  macro_aware_gate_active
}

macro_aware_replaces_clippy_threshold(key) if {
  key == "cognitive-complexity-threshold"
  macro_aware_gate_active
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
  not macro_aware_replaces_clippy_lint(key)
  actual := object.get(clippy, key, "")
  not meets_level(actual, required)
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
  not meets_level(actual, required)
  msg := sprintf("rust lint %s must equal %q", [key, required])
}

deny contains msg if {
  cargo_manifest != null
  lint_scope_defined
  scope := lint_scope
  rustdoc := scope["rustdoc"]
  required := rustdoc_required_lints[key]
  actual := object.get(rustdoc, key, "")
  not meets_level(actual, required)
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
  not macro_aware_replaces_clippy_threshold(key)
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

deny contains msg if {
  macro_aware_opted_in
  not macro_aware_max_complexity_valid
  msg := sprintf(
    "dylint.toml %s max_complexity must be an integer from 1 to %d",
    [macro_aware_lint, macro_aware_max_complexity_ceiling],
  )
}

deny contains msg if {
  macro_aware_opted_in
  not macro_aware_table_has_only_known_fields
  msg := sprintf("dylint.toml %s accepts only max_complexity", [macro_aware_lint])
}

deny contains msg if {
  macro_aware_opted_in
  not clippy_threshold_removed
  msg := "clippy.toml must drop cognitive-complexity-threshold when the macro-aware lint is opted in"
}
