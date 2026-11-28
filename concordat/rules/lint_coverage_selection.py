"""Say which packages a bare Cargo command selects at a surface's manifest.

A command with no `-p` and no `--workspace` selects the workspace's
`default-members`; with none, a virtual workspace selects every member and a
workspace with a root package selects only that package. `gate-lint-coverage`
credits a lint run with the features of the packages it selects, so a bare run
at a grown workspace's root does not stand for its members.

A `default-members` entry may be a glob. The audit does not expand it, so the
selection is reported as unknown and the policy treats a bare run there as
indeterminate.
"""

from __future__ import annotations

import posixpath
import typing as typ

from .test_environment_types import table

if typ.TYPE_CHECKING:
    from .lint_tables_manifests import PackageFacts
    from .test_environment_types import Reader

__all__ = ["default_selection", "resolver_of"]

_GLOB_CHARACTERS: typ.Final = frozenset("*?[]")


def default_selection(
    reader: Reader, key: str, packages: list[PackageFacts]
) -> tuple[list[str], bool]:
    """Return the packages a bare command at the surface's manifest selects.

    Returns
    -------
    tuple[list[str], bool]
        The selected package names, and whether the selection is known (it is
        not when `default-members` holds a glob).
    """
    root = reader.toml(reader.checkout / key) or {}
    default_members = table(root.get("workspace")).get("default-members")
    if isinstance(default_members, list):
        return _listed_members(
            key, list(typ.cast("list[object]", default_members)), packages
        )
    root_name = table(root.get("package")).get("name")
    if isinstance(root_name, str):
        return [root_name], True
    return sorted(p["name"] for p in packages), True


_EDITION_RESOLVERS: typ.Final = {"2021": "2", "2024": "3"}


def resolver_of(reader: Reader, key: str) -> str:
    """Return the feature resolver of the workspace (or package) at *key*.

    `[workspace] resolver` says so; without it a root package's edition does
    (2021 is resolver 2, 2024 resolver 3), and anything else is resolver 1, the
    default of a virtual workspace.

    Returns
    -------
    str
        `1`, `2` or `3`, as written or implied.
    """
    root = reader.toml(reader.checkout / key) or {}
    declared = table(root.get("workspace")).get("resolver")
    if not isinstance(declared, str):
        declared = table(root.get("package")).get("resolver")
    if isinstance(declared, str):
        return declared
    edition = table(root.get("package")).get("edition")
    if table(edition).get("workspace") is True:
        # `edition.workspace = true`: the root package takes its own workspace's.
        edition = table(table(root.get("workspace")).get("package")).get("edition")
    return _EDITION_RESOLVERS.get(edition, "1") if isinstance(edition, str) else "1"


def _listed_members(
    key: str, default_members: list[object], packages: list[PackageFacts]
) -> tuple[list[str], bool]:
    """Return the packages a `default-members` list names, and whether it is known.

    Returns
    -------
    tuple[list[str], bool]
        The named packages, and False when an entry is a glob (every package is
        then returned, for the caller to treat as unknown).
    """
    entries = [m for m in default_members if isinstance(m, str)]
    if any(_GLOB_CHARACTERS & set(entry) for entry in entries):
        return sorted(p["name"] for p in packages), False
    base = posixpath.dirname(key)
    joined = (posixpath.normpath(posixpath.join(base, entry)) for entry in entries)
    wanted = {"" if path == "." else path for path in joined}
    return sorted(p["name"] for p in packages if p["directory"] in wanted), True
