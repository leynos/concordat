"""Read the two configuration files that decide how cognitive complexity is gated.

The baseline requires cognitive complexity to be gated one of two ways: by
Clippy (`cognitive_complexity = "deny"` plus `cognitive-complexity-threshold`
in `clippy.toml`), or by Whitaker's `macro_aware_cognitive_complexity`, opted in
through `dylint.toml` with Clippy's threshold removed so one gate owns the
budget. The policy decides which applies; this module reads the files for it.

Each file is the nearest one at or above the package's directory inside the
checkout, as the tools load them. A file that cannot be decoded is recorded on
the reader (and so reported as indeterminate) and read as absent here.
"""

from __future__ import annotations

import typing as typ

from .test_environment_types import table

if typ.TYPE_CHECKING:
    import pathlib

    from .test_environment_types import Reader

__all__ = ["ClippyConfig", "DylintConfig", "clippy_config", "dylint_config"]

CLIPPY_NAMES: typ.Final = (".clippy.toml", "clippy.toml")
DYLINT_NAMES: typ.Final = ("dylint.toml",)
MACRO_AWARE: typ.Final = "macro_aware_cognitive_complexity"


class ClippyConfig(typ.TypedDict):
    """The `clippy.toml` a package is linted by: its path and complexity threshold."""

    path: str | None
    cognitive_threshold: object | None


class DylintConfig(typ.TypedDict):
    """The `dylint.toml` facts the macro-aware gate depends on.

    `experimental` is Whitaker's opt-in list as written (None when absent);
    `table` is the lint's own table (None when absent); and
    `max_complexity_is_integer` is true only for a TOML integer, because a float
    such as `9.0` is rejected by Whitaker but indistinguishable from `9` once
    the document is JSON.
    """

    path: str | None
    experimental: list[object] | None
    table: dict[str, object] | None
    max_complexity_is_integer: bool


def _nearest(
    reader: Reader, directory: pathlib.Path, names: tuple[str, ...]
) -> pathlib.Path | None:
    """Return the first of *names* in *directory* or its parents inside the checkout."""
    for current in (directory, *directory.parents):
        found = next(
            ((current / name) for name in names if (current / name).is_file()), None
        )
        if found is not None or current == reader.checkout:
            return found
    return None


def clippy_config(reader: Reader, directory: pathlib.Path) -> ClippyConfig:
    """Return the Clippy configuration that applies to a package directory.

    Returns
    -------
    ClippyConfig
        The file's path and `cognitive-complexity-threshold`, both None when no
        file applies or it cannot be decoded.
    """
    found = _nearest(reader, directory, CLIPPY_NAMES)
    config = reader.toml(found) if found is not None else None
    if found is None or config is None:
        return {"path": None, "cognitive_threshold": None}
    return {
        "path": reader.relative(found),
        "cognitive_threshold": config.get("cognitive-complexity-threshold"),
    }


def dylint_config(reader: Reader, directory: pathlib.Path) -> DylintConfig:
    """Return the Dylint configuration that applies to a package directory.

    Returns
    -------
    DylintConfig
        The opt-in list and the macro-aware lint's table, None when absent or
        when the file cannot be decoded.
    """
    found = _nearest(reader, directory, DYLINT_NAMES)
    config = reader.toml(found) if found is not None else None
    if found is None or config is None:
        return {
            "path": None,
            "experimental": None,
            "table": None,
            "max_complexity_is_integer": False,
        }
    experimental = table(config.get("whitaker")).get("experimental")
    lint_table = config.get(MACRO_AWARE)
    max_complexity = table(lint_table).get("max_complexity")
    return {
        "path": reader.relative(found),
        "experimental": (
            list(typ.cast("list[object]", experimental))
            if isinstance(experimental, list)
            else None
        ),
        "table": table(lint_table) if isinstance(lint_table, dict) else None,
        "max_complexity_is_integer": isinstance(max_complexity, int)
        and not isinstance(max_complexity, bool),
    }
