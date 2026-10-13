package canon.rust

import rego.v1

canonical_clippy_lints := {
  "pedantic": {"level": "warn", "priority": -1},
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

canonical_rust_lints := {
  "unknown_lints": "deny",
  "renamed_and_removed_lints": "deny",
  "missing_docs": "deny",
}

canonical_rustdoc_lints := {
  "missing_crate_level_docs": "deny",
  "broken_intra_doc_links": "deny",
  "private_intra_doc_links": "deny",
  "bare_urls": "deny",
  "invalid_html_tags": "deny",
  "invalid_codeblock_attributes": "deny",
  "unescaped_backticks": "deny",
}

canonical_lints := {
  "clippy": canonical_clippy_lints,
  "rust": canonical_rust_lints,
  "rustdoc": canonical_rustdoc_lints,
}

workspace_manifest := {
  "workspace": {
    "members": ["examples/hello_world", "ortho_config"],
    "lints": canonical_lints,
  },
}

root_manifest := {
  "package": {"name": "demo"},
  "lints": canonical_lints,
}

workspace_without_lints := {
  "workspace": {
    "members": ["examples/hello_world"],
  },
  "lints": canonical_lints,
}

clippy_cfg := {
  "cognitive-complexity-threshold": 9,
  "too-many-arguments-threshold": 4,
  "too-many-lines-threshold": 70,
  "excessive-nesting-threshold": 4,
  "allow-expect-in-tests": true,
  "disallowed-methods": [
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
  ],
}

bad_clippy_cfg := object.union(clippy_cfg, {"too-many-arguments-threshold": 6})

test_workspace_manifest_passes if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": clippy_cfg,
  }
  count(result) == 0
}

test_workspace_requires_workspace_lints if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_without_lints,
    "clippy": clippy_cfg,
  }
  result[_] == "Cargo workspace must define [workspace.lints]"
}

test_root_manifest_passes if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": root_manifest,
    "clippy": clippy_cfg,
  }
  count(result) == 0
}

test_threshold_mismatch_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": bad_clippy_cfg,
  }
  result[_] == "clippy.toml too-many-arguments-threshold must equal 4"
}

test_missing_clippy_config_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
  }
  result[_] == "clippy.toml must exist when Cargo.toml is present"
}

test_missing_disallowed_methods_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.remove(clippy_cfg, ["disallowed-methods"]),
  }
  result[_] == "clippy.toml disallowed-methods must define the seven approved std::env entries"
}

test_missing_environment_method_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": [
        method |
        some method in clippy_cfg["disallowed-methods"]
        method.path != "std::env::var_os"
      ],
    }),
  }
  expected_message := sprintf(
    "clippy.toml disallowed-methods must include %s with reason %q",
    ["std::env::var_os", "inject an environment reader"],
  )
  result[_] == expected_message
}

test_altered_environment_method_reason_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": [
        object.union(method, {"reason": "allow direct environment access"}) |
        some method in clippy_cfg["disallowed-methods"]
      ],
    }),
  }
  expected_message := sprintf(
    "clippy.toml disallowed-methods must include %s with reason %q",
    ["std::env::var", "inject an environment reader"],
  )
  result[_] == expected_message
}

test_local_disallowed_method_extension_passes if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "std::env::current_exe"},
        "std::env::current_dir",
      ]),
    }),
  }
  count(result) == 0
}

test_canonical_method_optional_replacement_passes if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(
        [
          object.union(clippy_cfg["disallowed-methods"][0], {
            "replacement": "test_support::Environment::var",
            "allow-invalid": true,
          }),
        ],
        array.slice(clippy_cfg["disallowed-methods"], 1, count(clippy_cfg["disallowed-methods"])),
      ),
    }),
  }
  count(result) == 0
}

test_non_string_non_object_extension_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        17,
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_extension_with_empty_path_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "", "reason": "avoid ambient process lookup"},
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_empty_bare_string_extension_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [""]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_numeric_reason_on_extension_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "std::env::current_exe", "reason": 7},
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_null_reason_on_extension_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "std::env::current_exe", "reason": null},
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_numeric_replacement_on_extension_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "std::env::current_exe", "replacement": 7},
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_null_replacement_on_extension_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "std::env::current_exe", "replacement": null},
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_string_allow_invalid_on_extension_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "std::env::current_exe", "allow-invalid": "true"},
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_null_allow_invalid_on_extension_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "std::env::current_exe", "allow-invalid": null},
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_unknown_extension_key_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "std::env::current_exe", "local-only": true},
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods entries must match the Clippy 1.92 schema"
}

test_duplicate_environment_method_path_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        {"path": "std::env::var", "reason": "a stricter local reason"},
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods paths must be unique"
}

test_duplicate_bare_and_table_method_path_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": object.union(clippy_cfg, {
      "disallowed-methods": array.concat(clippy_cfg["disallowed-methods"], [
        "std::env::var",
      ]),
    }),
  }
  result[_] == "clippy.toml disallowed-methods paths must be unique"
}
