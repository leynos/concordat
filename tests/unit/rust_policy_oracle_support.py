"""Independent validation rules for synthetic Rust lint-policy inputs."""

from __future__ import annotations

import copy
import math
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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


def required_methods() -> list[dict[str, str]]:
    """Return independent copies of the seven canonical path/reason pairs."""
    return copy.deepcopy(list(_REQUIRED_METHODS))


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


def _oracle_method_fields(method: object) -> dict[str, object] | None:
    """Return the allowed fields from an object-shaped method entry."""
    if not isinstance(method, dict):
        return None
    if any(not isinstance(key, str) for key in method):
        return None
    fields = typ.cast("dict[str, object]", method)
    if set(fields).difference(_ALLOWED_METHOD_FIELDS):
        return None
    return fields


def _optional_method_fields_are_valid(fields: dict[str, object]) -> bool:
    """Check optional string and boolean field types independently."""
    optional_string_fields = ("reason", "replacement")
    return all(
        isinstance(fields[field], str)
        for field in optional_string_fields
        if field in fields
    ) and ("allow-invalid" not in fields or type(fields["allow-invalid"]) is bool)


def _oracle_method_entry(method: object) -> tuple[str, str | None] | None:
    """Return a validated path/reason pair for one method entry."""
    if isinstance(method, str):
        if not method:
            return None
        return method, None
    fields = _oracle_method_fields(method)
    if fields is None:
        return None
    if not _optional_method_fields_are_valid(fields):
        return None
    path = fields.get("path")
    if not isinstance(path, str):
        return None
    if not path:
        return None
    reason = fields.get("reason")
    return path, reason if isinstance(reason, str) else None


def _method_paths_are_unique(entries: cabc.Sequence[tuple[str, str | None]]) -> bool:
    """Return whether all validated method entries have unique paths."""
    paths = [path for path, _ in entries]
    return len(paths) == len(set(paths))


def _required_pairs_present(entries: cabc.Sequence[tuple[str, str | None]]) -> bool:
    """Return whether all canonical path/reason pairs are present."""
    required_pairs = {
        (method["path"], method["reason"]) for method in _REQUIRED_METHODS
    }
    actual_pairs = {(path, reason) for path, reason in entries if reason is not None}
    return required_pairs.issubset(actual_pairs)


def _oracle_methods_match_requirements(methods: cabc.Sequence[object]) -> bool:
    """Require valid entries, unique paths, and all required path/reason pairs."""
    entries = [_oracle_method_entry(method) for method in methods]
    if any(entry is None for entry in entries):
        return False
    valid_entries = [entry for entry in entries if entry is not None]
    return _method_paths_are_unique(valid_entries) and _required_pairs_present(
        valid_entries
    )


def oracle_accepts(methods: cabc.Sequence[object], priority: object = -1) -> bool:
    """Apply method-array rules and normalized JSON priority semantics."""
    return oracle_accepts_priority(priority) and _oracle_methods_match_requirements(
        methods
    )
