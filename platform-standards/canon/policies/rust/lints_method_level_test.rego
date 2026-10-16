package canon.rust

import rego.v1

# Keep Cargo lint-level contract cases separate from the broader policy tests.

cargo_manifest_with_clippy_lints(clippy_lints) := {
  "workspace": {
    "members": workspace_manifest.workspace.members,
    "lints": {
      "clippy": clippy_lints,
      "rust": canonical_rust_lints,
      "rustdoc": canonical_rustdoc_lints,
    },
  },
}

rust_lint_result(clippy_lints) := result if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": cargo_manifest_with_clippy_lints(clippy_lints),
    "clippy": clippy_cfg,
  }
}

test_disallowed_methods_deny_scalar_passes if {
  result := rust_lint_result(canonical_clippy_lints)
  count(result) == 0
}

test_disallowed_methods_forbid_scalar_passes if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": "forbid",
  }))
  count(result) == 0
}

test_disallowed_methods_deny_table_with_integer_priority_passes if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": -1},
  }))
  count(result) == 0
}

test_disallowed_methods_min_i8_priority_passes if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": -128},
  }))
  count(result) == 0
}

test_disallowed_methods_max_i8_priority_passes if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": 127},
  }))
  count(result) == 0
}

test_disallowed_methods_min_i8_float_priority_passes if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": -128.0},
  }))
  count(result) == 0
}

test_disallowed_methods_max_i8_float_priority_passes if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": 127.0},
  }))
  count(result) == 0
}

test_disallowed_methods_decimal_zero_priority_passes if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": 0.0},
  }))
  count(result) == 0
}

test_disallowed_methods_exponent_priority_passes if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": 1e0},
  }))
  count(result) == 0
}

test_disallowed_methods_forbid_table_without_priority_passes if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "forbid"},
  }))
  count(result) == 0
}

test_disallowed_methods_forbid_table_with_null_priority_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "forbid", "priority": null},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_warn_scalar_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": "warn",
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_allow_scalar_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": "allow",
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_missing_disallowed_methods_lint_is_rejected if {
  clippy_lints := object.remove(canonical_clippy_lints, ["disallowed_methods"])
  cargo_manifest := cargo_manifest_with_clippy_lints(clippy_lints)
  not "disallowed_methods" in cargo_manifest.workspace.lints.clippy
  result := rust_lint_result(clippy_lints)
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_warn_table_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "warn", "priority": 0},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_allow_table_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "allow", "priority": 0},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_table_without_level_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"priority": 0},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_table_with_noninteger_priority_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": 0.5},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_fractional_priority_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": 1.5},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_below_i8_priority_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": -129},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_above_i8_priority_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": 128},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_huge_priority_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {
      "level": "deny",
      "priority": 10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000,
    },
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_boolean_priority_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": true},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_string_priority_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {"level": "deny", "priority": "1"},
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_disallowed_methods_table_with_unknown_key_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": {
      "level": "deny",
      "priority": 0,
      "reason": "unknown Cargo lint setting",
    },
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}

test_malformed_disallowed_methods_lint_value_is_rejected if {
  result := rust_lint_result(object.union(canonical_clippy_lints, {
    "disallowed_methods": 1,
  }))
  result[_] == "clippy lint disallowed_methods must use deny or forbid"
}
