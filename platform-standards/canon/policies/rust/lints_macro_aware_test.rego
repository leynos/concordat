package canon.rust

import rego.v1

# The cognitive-complexity requirement is met either by Clippy's deny or by a
# Whitaker macro-aware opt-in in dylint.toml with Clippy's threshold removed.

macro_aware_dylint := {
  "whitaker": {"experimental": ["macro_aware_cognitive_complexity"]},
  "macro_aware_cognitive_complexity": {"max_complexity": 9},
}

without_clippy_cognitive_lint := object.remove(canonical_clippy_lints, ["cognitive_complexity"])

without_clippy_threshold := object.remove(clippy_cfg, ["cognitive-complexity-threshold"])

manifest_without_clippy_cognitive_lint := {
  "workspace": {
    "members": workspace_manifest.workspace.members,
    "lints": {
      "clippy": without_clippy_cognitive_lint,
      "rust": canonical_rust_lints,
      "rustdoc": canonical_rustdoc_lints,
    },
  },
}

macro_aware_result(dylint, clippy) := result if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": manifest_without_clippy_cognitive_lint,
    "clippy": clippy,
    "dylint": dylint,
  }
}

test_macro_aware_opt_in_passes_without_clippy_cognitive_gate if {
  result := macro_aware_result(macro_aware_dylint, without_clippy_threshold)
  count(result) == 0
}

test_macro_aware_opt_in_at_lower_threshold_passes if {
  dylint := object.union(macro_aware_dylint, {
    "macro_aware_cognitive_complexity": {"max_complexity": 7},
  })
  result := macro_aware_result(dylint, without_clippy_threshold)
  count(result) == 0
}

test_clippy_gate_alone_still_passes if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": workspace_manifest,
    "clippy": clippy_cfg,
  }
  count(result) == 0
}

test_neither_gate_is_rejected if {
  result := data.canon.rust.lints.deny with input as {
    "cargo": manifest_without_clippy_cognitive_lint,
    "clippy": without_clippy_threshold,
  }
  result[_] == "clippy lint cognitive_complexity must equal \"deny\""
  result[_] == "clippy.toml cognitive-complexity-threshold must equal 9"
}

test_dylint_without_opt_in_is_rejected if {
  dylint := {"whitaker": {"experimental": ["some_other_lint"]}}
  result := macro_aware_result(dylint, without_clippy_threshold)
  result[_] == "clippy lint cognitive_complexity must equal \"deny\""
}

test_opt_in_above_nine_is_rejected if {
  dylint := object.union(macro_aware_dylint, {
    "macro_aware_cognitive_complexity": {"max_complexity": 12},
  })
  result := macro_aware_result(dylint, without_clippy_threshold)
  result[_] == "dylint.toml macro_aware_cognitive_complexity max_complexity must be an integer from 1 to 9"
  result[_] == "clippy lint cognitive_complexity must equal \"deny\""
}

test_opt_in_without_max_complexity_is_rejected if {
  dylint := {"whitaker": {"experimental": ["macro_aware_cognitive_complexity"]}}
  result := macro_aware_result(dylint, without_clippy_threshold)
  result[_] == "dylint.toml macro_aware_cognitive_complexity max_complexity must be an integer from 1 to 9"
}

test_opt_in_with_fractional_max_complexity_is_rejected if {
  dylint := object.union(macro_aware_dylint, {
    "macro_aware_cognitive_complexity": {"max_complexity": 8.5},
  })
  result := macro_aware_result(dylint, without_clippy_threshold)
  result[_] == "dylint.toml macro_aware_cognitive_complexity max_complexity must be an integer from 1 to 9"
}

test_opt_in_with_leftover_clippy_threshold_is_rejected if {
  result := macro_aware_result(macro_aware_dylint, clippy_cfg)
  result[_] == "clippy.toml must drop cognitive-complexity-threshold when the macro-aware lint is opted in"
}

test_malformed_dylint_tables_do_not_opt_in if {
  dylint := {"whitaker": "experimental", "macro_aware_cognitive_complexity": 9}
  result := macro_aware_result(dylint, without_clippy_threshold)
  result[_] == "clippy lint cognitive_complexity must equal \"deny\""
}

test_opt_in_with_unknown_field_is_rejected if {
  dylint := object.union(macro_aware_dylint, {
    "macro_aware_cognitive_complexity": {"max_complexity": 9, "extra": true},
  })
  result := macro_aware_result(dylint, without_clippy_threshold)
  result[_] == "dylint.toml macro_aware_cognitive_complexity accepts only max_complexity"
  result[_] == "clippy lint cognitive_complexity must equal \"deny\""
}

test_opt_in_at_lowest_threshold_passes if {
  dylint := object.union(macro_aware_dylint, {
    "macro_aware_cognitive_complexity": {"max_complexity": 1},
  })
  result := macro_aware_result(dylint, without_clippy_threshold)
  count(result) == 0
}

test_opt_in_with_zero_threshold_is_rejected if {
  dylint := object.union(macro_aware_dylint, {
    "macro_aware_cognitive_complexity": {"max_complexity": 0},
  })
  result := macro_aware_result(dylint, without_clippy_threshold)
  result[_] == "dylint.toml macro_aware_cognitive_complexity max_complexity must be an integer from 1 to 9"
}

test_opt_in_alongside_other_experimental_lints_passes if {
  dylint := object.union(macro_aware_dylint, {
    "whitaker": {"experimental": ["other_lint", 7, "macro_aware_cognitive_complexity"]},
  })
  result := macro_aware_result(dylint, without_clippy_threshold)
  count(result) == 0
}

test_experimental_that_is_not_a_list_does_not_opt_in if {
  dylint := object.union(macro_aware_dylint, {
    "whitaker": {"experimental": "macro_aware_cognitive_complexity"},
  })
  result := macro_aware_result(dylint, without_clippy_threshold)
  result[_] == "clippy lint cognitive_complexity must equal \"deny\""
}
