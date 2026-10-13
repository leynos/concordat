"""Independent fixtures and generators for Rust lint policy properties."""

from __future__ import annotations

import copy
import math
import typing as typ

from hypothesis import strategies as st

from tests.unit.rust_policy_batch_support import invoke_root_rust_policy_batch

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_POLICY_INPUT_COUNT: typ.Final = 6
_CARGO_PRIORITY_MIN: typ.Final = -128
_CARGO_PRIORITY_MAX: typ.Final = 127
_REQUIRED_METHODS: typ.Final = (
    {"path": "std::env::var", "reason": "inject an environment reader"},
    {"path": "std::env::var_os", "reason": "inject an environment reader"},
    {"path": "std::env::vars", "reason": "inject an environment reader"},
    {"path": "std::env::vars_os", "reason": "inject an environment reader"},
    {"path": "std::env::set_var", "reason": "use a stub environment in tests"},
    {"path": "std::env::remove_var", "reason": "use a stub environment in tests"},
    {
        "path": "std::env::set_current_dir",
        "reason": (
            "inject a base-directory seam; confine CWD changes to Command::current_dir"
        ),
    },
)
_ALLOWED_METHOD_FIELDS: typ.Final = frozenset({
    "path",
    "reason",
    "replacement",
    "allow-invalid",
})

# The Cargo lint maps and Clippy thresholds reproduce canonical_lints and
# clippy_cfg in lints_policy_test.rego. The disallowed_methods Cargo lint is
# set to deny so the property exercises the intended consumer envelope.
_CANONICAL_CARGO: typ.Final = {
    "workspace": {
        "members": ["examples/hello_world", "ortho_config"],
        "lints": {
            "clippy": {
                "pedantic": {"level": "warn", "priority": -1},
                **dict.fromkeys(
                    (
                        "allow_attributes",
                        "allow_attributes_without_reason",
                        "blanket_clippy_restriction_lints",
                        "cognitive_complexity",
                        "needless_pass_by_value",
                        "implicit_hasher",
                        "dbg_macro",
                        "print_stdout",
                        "print_stderr",
                        "unwrap_used",
                        "expect_used",
                        "indexing_slicing",
                        "string_slice",
                        "integer_division",
                        "integer_division_remainder_used",
                        "panic_in_result_fn",
                        "unreachable",
                        "host_endian_bytes",
                        "little_endian_bytes",
                        "big_endian_bytes",
                        "let_underscore_must_use",
                        "or_fun_call",
                        "option_if_let_else",
                        "self_named_module_files",
                        "shadow_reuse",
                        "shadow_same",
                        "shadow_unrelated",
                        "str_to_string",
                        "string_lit_as_bytes",
                        "try_err",
                        "unneeded_field_pattern",
                        "use_self",
                        "float_arithmetic",
                        "cast_possible_truncation",
                        "cast_possible_wrap",
                        "cast_precision_loss",
                        "lossy_float_literal",
                        "missing_const_for_fn",
                        "must_use_candidate",
                        "unused_async",
                        "missing_panics_doc",
                        "error_impl_error",
                        "result_large_err",
                        "disallowed_methods",
                    ),
                    "deny",
                ),
            },
            "rust": {
                "unknown_lints": "deny",
                "renamed_and_removed_lints": "deny",
                "missing_docs": "deny",
            },
            "rustdoc": {
                "missing_crate_level_docs": "deny",
                "broken_intra_doc_links": "deny",
                "private_intra_doc_links": "deny",
                "bare_urls": "deny",
                "invalid_html_tags": "deny",
                "invalid_codeblock_attributes": "deny",
                "unescaped_backticks": "deny",
            },
        },
    }
}
_CANONICAL_CLIPPY: typ.Final = {
    "cognitive-complexity-threshold": 9,
    "too-many-arguments-threshold": 4,
    "too-many-lines-threshold": 70,
    "excessive-nesting-threshold": 4,
    "allow-expect-in-tests": True,
}


def required_methods() -> list[dict[str, str]]:
    """Return independent copies of the seven canonical path/reason pairs."""
    return copy.deepcopy(list(_REQUIRED_METHODS))


def canonical_envelope(
    methods: cabc.Sequence[object], priority: object = -1
) -> dict[str, object]:
    """Return synthetic normalized JSON for methods and lint priority."""
    cargo = copy.deepcopy(_CANONICAL_CARGO)
    workspace = typ.cast("dict[str, object]", cargo["workspace"])
    lints = typ.cast("dict[str, object]", workspace["lints"])
    clippy = typ.cast("dict[str, object]", lints["clippy"])
    clippy["disallowed_methods"] = {"level": "deny", "priority": priority}
    return {
        "cargo": cargo,
        "clippy": {
            **_CANONICAL_CLIPPY,
            "disallowed-methods": copy.deepcopy(list(methods)),
        },
    }


def oracle_accepts_priority(priority: object) -> bool:
    """Accept finite integral JSON numbers in Cargo's signed i8 domain."""
    if isinstance(priority, bool):
        return False
    if isinstance(priority, int):
        return _CARGO_PRIORITY_MIN <= priority <= _CARGO_PRIORITY_MAX
    return (
        isinstance(priority, float)
        and math.isfinite(priority)
        and priority.is_integer()
        and _CARGO_PRIORITY_MIN <= priority <= _CARGO_PRIORITY_MAX
    )


def oracle_accepts(methods: cabc.Sequence[object], priority: object = -1) -> bool:
    """Apply method-array rules and normalized JSON priority semantics."""
    if not oracle_accepts_priority(priority):
        return False
    paths: list[str] = []
    exact_pairs: set[tuple[str, str]] = set()
    for method in methods:
        if isinstance(method, str):
            if not method:
                return False
            path = method
        elif isinstance(method, dict):
            if any(not isinstance(key, str) for key in method):
                return False
            method_fields = typ.cast("dict[str, object]", method)
            if set(method_fields).difference(_ALLOWED_METHOD_FIELDS):
                return False
            path_value = method_fields.get("path")
            if not isinstance(path_value, str) or not path_value:
                return False
            for field in ("reason", "replacement"):
                if field in method_fields and not isinstance(method_fields[field], str):
                    return False
            if (
                "allow-invalid" in method_fields
                and type(method_fields["allow-invalid"]) is not bool
            ):
                return False
            path = path_value
            reason = method_fields.get("reason")
            if isinstance(reason, str):
                exact_pairs.add((path, reason))
        else:
            return False
        paths.append(path)

    required_pairs = {
        (method["path"], method["reason"]) for method in _REQUIRED_METHODS
    }
    return len(paths) == len(set(paths)) and required_pairs.issubset(exact_pairs)


def policy_accepts_batch(
    method_lists: cabc.Sequence[cabc.Sequence[object]],
) -> list[bool]:
    """Return real Conftest acceptance for one bounded list of method arrays."""
    envelopes = [canonical_envelope(methods) for methods in method_lists]
    return _accepted_envelopes(envelopes)


def policy_accepts_priority_batch(priorities: cabc.Sequence[object]) -> list[bool]:
    """Return Conftest acceptance for bounded synthetic JSON priorities."""
    envelopes = [
        canonical_envelope(required_methods(), priority) for priority in priorities
    ]
    return _accepted_envelopes(envelopes)


def _accepted_envelopes(envelopes: cabc.Sequence[dict[str, object]]) -> list[bool]:
    """Interpret failures after the result wrapper validates every row."""
    results_by_input = invoke_root_rust_policy_batch(envelopes)
    return [
        not any(result.get("failures", []) for result in results)
        for results in results_by_input
    ]


def _extension_path() -> st.SearchStrategy[str]:
    """Generate stable, non-canonical paths for local extensions."""
    suffix = st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=12
    )
    return suffix.map(lambda value: f"std::test_extension_{value}")


@st.composite
def method_arrays(draw: st.DrawFn) -> list[object]:
    """Generate valid and invalid method arrays across the Rego schema."""
    kind = draw(
        st.sampled_from((
            "valid_permutation",
            "valid_mixed_extensions",
            "missing_required_pair",
            "altered_required_reason",
            "duplicate_mixed_path",
            "unknown_field",
            "invalid_path",
            "invalid_optional_type",
        ))
    )
    methods: list[object] = [dict(method) for method in _REQUIRED_METHODS]
    if kind == "valid_permutation":
        return list(draw(st.permutations(methods)))
    if kind == "valid_mixed_extensions":
        path_values = draw(
            st.lists(_extension_path(), min_size=2, max_size=2, unique=True)
        )
        object_extension = {
            "path": path_values[1],
            "reason": draw(st.text(max_size=10)),
            "replacement": draw(st.text(max_size=10)),
            "allow-invalid": draw(st.booleans()),
        }
        return list(draw(st.permutations([*methods, path_values[0], object_extension])))
    if kind == "missing_required_pair":
        del methods[draw(st.integers(min_value=0, max_value=len(methods) - 1))]
        return methods
    if kind == "altered_required_reason":
        index = draw(st.integers(min_value=0, max_value=len(methods) - 1))
        method = typ.cast("dict[str, str]", methods[index])
        method["reason"] = draw(st.text(min_size=1, max_size=12))
        return methods
    if kind == "duplicate_mixed_path":
        index = draw(st.integers(min_value=0, max_value=len(methods) - 1))
        method = typ.cast("dict[str, str]", methods[index])
        methods.append(method["path"])
        return methods
    if kind == "unknown_field":
        methods.append({
            "path": draw(_extension_path()),
            "local-only": draw(st.booleans()),
        })
        return methods
    if kind == "invalid_path":
        malformed = draw(
            st.sampled_from((
                {},
                {"path": None},
                {"path": 17},
                {"path": ""},
            ))
        )
        methods.append(malformed)
        return methods
    if kind == "invalid_optional_type":
        path = draw(_extension_path())
        malformed_optional = draw(
            st.sampled_from((
                {"reason": 17},
                {"replacement": ["not", "a", "string"]},
                {"allow-invalid": "true"},
                {"reason": None},
                {"replacement": None},
                {"allow-invalid": None},
            ))
        )
        methods.append({"path": path, **malformed_optional})
        return methods
    message = f"unhandled generated case: {kind}"
    raise AssertionError(message)


def policy_input_count() -> int:
    """Return the maximum number of policy inputs in one generated batch."""
    return _POLICY_INPUT_COUNT


def priority_values() -> st.SearchStrategy[object]:
    """Generate in-range and out-of-range values for Cargo's i8 priority."""
    bounded_integer = st.integers(min_value=-256, max_value=256)
    integral_float = bounded_integer.map(float)
    fractional_float = st.tuples(
        st.integers(min_value=-256, max_value=256),
        st.sampled_from((0.25, 0.5, 0.75)),
    ).map(lambda parts: parts[0] + parts[1])
    return st.one_of(
        bounded_integer,
        integral_float,
        fractional_float,
        st.booleans(),
        st.text(max_size=8),
        st.none(),
    )
