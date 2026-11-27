"""Build the `policy-input/whitaker-exclusions` envelope.

A Whitaker exclusion (`excluded_crates`, `excluded_paths`, or any other
`excluded_*` key in a `dylint.toml`) switches a lint off for part of a
repository, so it should say why and, for a whole crate, be a decision rather
than a leftover. femtologging excluded `femtologging_rs`, its whole library, from
`no_std_fs_operations` on the strength of an old Whitaker bug, and nothing
rechecked it (femtologging#421).

The envelope records, per exclusion: where it is, whether a comment states a
reason (`whitaker_exclusions_text`), and, for each crate it names, whether the
name is a crate the repository governs, a fixture or vendored crate, or nothing
(`whitaker_exclusions_crates`). The policy decides what the facts mean. A file
that cannot be read or decoded, or a key the text scan cannot find, is carried
for the policy to report as indeterminate.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
import typing as typ

from .documentation_rust import RUST_PRUNED_DIRECTORIES
from .markdown_envelope import (
    Repository,
    is_symlink,
    raise_walk_error,
    read_text,
    resolved_root,
    within_checkout,
)
from .nested_cargo import origin_slug
from .test_environment_types import Reader, strings
from .whitaker_exclusions_crates import crate_kinds, normalised
from .whitaker_exclusions_text import Located, locate_exclusions

if typ.TYPE_CHECKING:
    import collections.abc as cabc

__all__ = [
    "ENVELOPE_KIND",
    "ENVELOPE_SCHEMA_VERSION",
    "WhitakerExclusionsEnvelope",
    "build_whitaker_exclusions_envelope",
]

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/whitaker-exclusions"
DYLINT_CONFIG: typ.Final = "dylint.toml"
OPERATION_READ_DYLINT: typ.Final = "read-dylint-config"
OPERATION_SCAN_DYLINT: typ.Final = "scan-dylint-configs"
UNRESOLVED: typ.Final = "unknown"
WHOLE_CRATE_KEY: typ.Final = "excluded_crates"
PATH_KEY: typ.Final = "excluded_paths"


class Entry(typ.TypedDict):
    """One name in an exclusion list, and what the checkout makes of it."""

    name: str
    resolved: str


class Exclusion(typ.TypedDict):
    """One `excluded_*` key in a `dylint.toml`.

    `kind` is `crates` for a whole-crate exclusion, `paths` for a module-path
    one and `other` for any other `excluded_*` key. `line` is None when the
    text scan could not find the key (a dotted key, an inline table).
    """

    file: str
    table: str
    key: str
    kind: str
    line: int | None
    has_reason: bool
    entries: list[Entry]


class WhitakerExclusionsEnvelope(typ.TypedDict):
    """The `policy-input/whitaker-exclusions` document."""

    schema_version: int
    kind: str
    repository: Repository
    exclusions: list[Exclusion]
    unreadable: list[str]


def _dylint_files(checkout: pathlib.Path) -> list[str]:
    """Return the checkout's `dylint.toml` files, outside the pruned trees."""
    found: list[str] = []
    for current, directories, files in os.walk(checkout, onerror=raise_walk_error):
        directories[:] = sorted(
            name for name in directories if name not in RUST_PRUNED_DIRECTORIES
        )
        base = pathlib.Path(current)
        found.extend(
            (base / name).relative_to(checkout).as_posix()
            for name in files
            if name == DYLINT_CONFIG
            and not is_symlink(base / name, OPERATION_SCAN_DYLINT)
        )
    return sorted(found)


def _decode(
    checkout: pathlib.Path, root: pathlib.Path, relative: str, unreadable: list[str]
) -> tuple[str, dict[str, object]] | None:
    """Return a file's text and parsed table, recording why it could not be read."""
    path = checkout / relative
    if not within_checkout(root, path, OPERATION_READ_DYLINT):
        unreadable.append(f"{relative}: resolves outside the checkout")
        return None
    try:
        text = read_text(path, OPERATION_READ_DYLINT)
        return text, tomllib.loads(text)
    except UnicodeDecodeError as error:
        unreadable.append(f"{relative}: not UTF-8 text: {error}")
    except tomllib.TOMLDecodeError as error:
        unreadable.append(f"{relative}: invalid TOML: {error}")
    return None


def _exclusion_keys(
    parsed: dict[str, object], prefix: str = ""
) -> typ.Iterator[tuple[str, str, object]]:
    """Yield `(table, key, value)` for every `excluded_*` key in a document."""
    for key, value in parsed.items():
        if key.startswith("excluded_"):
            yield prefix, key, value
        elif isinstance(value, dict):
            nested = f"{prefix}.{key}" if prefix else key
            yield from _exclusion_keys(typ.cast("dict[str, object]", value), nested)


def _kind(key: str) -> str:
    """Return how an exclusion key excludes: `crates`, `paths` or `other`."""
    return {WHOLE_CRATE_KEY: "crates", PATH_KEY: "paths"}.get(key, "other")


def _exclusion(
    relative: str,
    found: tuple[str, str, object],
    located: dict[tuple[str, str], Located],
    kinds: dict[str, str],
    unreadable: list[str],
) -> Exclusion:
    """Return the facts of one exclusion key."""
    table_name, key, value = found
    place = located.get((table_name, key))
    names = strings(value)
    if not isinstance(value, list) or len(names) != len(value):
        unreadable.append(
            f"{relative}: [{table_name}] {key} is not a list of strings, so its "
            "entries cannot be read"
        )
    is_whole_crate = _kind(key) == "crates"
    return {
        "file": relative,
        "table": table_name,
        "key": key,
        "kind": _kind(key),
        "line": place.line if place else None,
        "has_reason": bool(place and place.has_reason),
        "entries": [
            {
                "name": name,
                "resolved": kinds.get(normalised(name), UNRESOLVED)
                if is_whole_crate
                else UNRESOLVED,
            }
            for name in names
        ],
    }


def build_whitaker_exclusions_envelope(
    checkout: pathlib.Path,
    _parameters: cabc.Mapping[str, object] | None = None,
) -> WhitakerExclusionsEnvelope:
    """Assemble the whitaker-exclusions policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    _parameters:
        The manifest parameters. The policy reads them as `data.parameters`, so
        the builder has no use for them; the argument gives every registered
        builder one callable type.

    An `OperationalRuleError` propagates if a directory cannot be listed or a
    fact file resolves outside the checkout.

    Returns
    -------
    WhitakerExclusionsEnvelope
        The policy input document assembled from the checkout.
    """
    root = resolved_root(checkout)
    reader = Reader(checkout, root)
    unreadable: list[str] = []
    decoded = {
        relative: _decode(checkout, root, relative, unreadable)
        for relative in _dylint_files(checkout)
    }
    has_exclusions = any(
        parsed is not None and any(True for _ in _exclusion_keys(parsed[1]))
        for parsed in decoded.values()
    )
    kinds = crate_kinds(reader) if has_exclusions else {}
    exclusions = [
        _exclusion(
            relative,
            found,
            {(item.table, item.key): item for item in locate_exclusions(parsed[0])},
            kinds,
            unreadable,
        )
        for relative, parsed in decoded.items()
        if parsed is not None
        for found in _exclusion_keys(parsed[1])
    ]
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": origin_slug(checkout)},
        "exclusions": exclusions,
        "unreadable": sorted({*unreadable, *reader.unreadable}),
    }
