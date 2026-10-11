"""Read how Dependabot's configuration treats a published package.

The coverage fact answers three questions for one package in one directory:
which `updates` entry covers the directory, which group takes the package first
(document order is part of the configuration's meaning, so a narrow group must
precede a catch-all), and whether an `ignore` rule names it. Python names are
compared in their PEP 503 form, as Dependabot does for pip and uv.
"""

from __future__ import annotations

import fnmatch
import pathlib
import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .dependabot_envelope import ConfigFile


class Coverage(typ.TypedDict):
    """How Dependabot treats one declaration's published package name."""

    entry: int | None
    group: str | None
    catch_all: bool
    matching: list[str]
    matching_narrow: list[str]
    ignored: bool
    reason: str | None


def mapping(value: object) -> dict[str, object] | None:
    """Return *value* as a string-keyed mapping, or None when it is not one."""
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else None


def sequence(value: object) -> list[object] | None:
    """Return *value* as a list, or None when it is not one."""
    return typ.cast("list[object]", value) if isinstance(value, list) else None


def normalise(name: str) -> str:
    """Return a Python package name in its comparison form (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _directory_matches(pattern: str, directory: str) -> bool:
    """Return whether a Dependabot directory glob covers *directory*.

    `*` stays within one path segment and `**` spans segments, as Dependabot
    reads them; a leading or trailing slash is optional in the configuration.

    Returns
    -------
    bool
        Whether the glob covers the directory.
    """
    wanted = "/" + directory.strip("/")
    glob = "/" + pattern.strip("/")
    tokens = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*"}
    regex = "".join(
        tokens.get(part, re.escape(part)) for part in re.split(r"(\*\*/|\*\*|\*)", glob)
    )
    return re.fullmatch(regex, wanted) is not None


def _entry_directories(entry: dict[str, object]) -> list[str]:
    """Return the directories one `updates` entry lists."""
    listed = sequence(entry.get("directories"))
    if listed is not None:
        return [str(item) for item in listed]
    single = entry.get("directory")
    return [str(single)] if single is not None else []


def _group_takes(group: dict[str, object], package: str) -> bool:
    """Return whether *group* takes a version update of *package*."""
    if group.get("applies-to", "version-updates") != "version-updates":
        return False
    patterns = sequence(group.get("patterns"))
    if patterns is None:
        return False
    excluded = sequence(group.get("exclude-patterns")) or []
    excludes = [str(item) for item in excluded]
    if any(fnmatch.fnmatchcase(package, pattern) for pattern in excludes):
        return False
    return any(fnmatch.fnmatchcase(package, str(pattern)) for pattern in patterns)


def _ignore_names(entry: dict[str, object]) -> list[str]:
    """Return the `dependency-name` values of an entry's `ignore` rules."""
    names = (
        (mapping(rule) or {}).get("dependency-name")
        for rule in sequence(entry.get("ignore")) or []
    )
    return [name for name in names if isinstance(name, str)]


def is_ignored(entry: dict[str, object], package: str, *, python: bool) -> bool:
    """Return whether an `ignore` rule of *entry* names *package*.

    Dependabot reads `dependency-name` as a pattern in which `*` spans any
    characters, and compares Python names in their normalised form.

    Returns
    -------
    bool
        Whether a rule's name or pattern covers the package.
    """
    return any(
        fnmatch.fnmatchcase(package, normalise(name) if python else name)
        for name in _ignore_names(entry)
    )


def directories_of(manifest: str) -> list[str]:
    """Return a manifest's directory and each ancestor, as Dependabot directories.

    A workspace member is updated through its workspace root, so a covering
    entry for an ancestor directory covers the member too.

    Returns
    -------
    list[str]
        The `/`-rooted directories from the manifest's own up to the root.
    """
    parent = pathlib.PurePosixPath(manifest).parent
    chain = [parent, *parent.parents]
    return ["/" + str(item).strip("./") if str(item) != "." else "/" for item in chain]


def _missing_coverage(reason: str) -> Coverage:
    """Return the coverage fact for a package no group configuration reaches."""
    return {
        "entry": None,
        "group": None,
        "catch_all": False,
        "matching": [],
        "matching_narrow": [],
        "ignored": False,
        "reason": reason,
    }


def _entry_covers(entry: object, ecosystem: str, directory: str) -> bool:
    """Return whether an `updates` entry updates *ecosystem* in *directory*."""
    table = mapping(entry)
    return (
        table is not None
        and table.get("package-ecosystem") == ecosystem
        and any(_directory_matches(p, directory) for p in _entry_directories(table))
    )


def coverage(
    config: ConfigFile | None,
    ecosystem: str,
    directories: cabc.Sequence[str],
    package: str,
) -> Coverage:
    """Return how Dependabot's configuration treats one published package.

    Returns
    -------
    Coverage
        The covering entry, the first group that takes the package, whether
        that group is a catch-all, and whether an `ignore` rule names it; or
        the reason none could be established.
    """
    if config is None or config["error"] is not None:
        return _missing_coverage("no decodable Dependabot configuration")
    updates = sequence((mapping(config["parsed"]) or {}).get("updates"))
    if updates is None:
        return _missing_coverage("the Dependabot configuration has no updates")
    covering = (
        (index, entry)
        for directory in directories
        for index, entry in enumerate(updates)
        if _entry_covers(entry, ecosystem, directory)
    )
    found = next(covering, None)
    if found is None:
        return _missing_coverage(f"no {ecosystem} entry covers {directories[0]}")
    index, entry = found
    table = typ.cast("dict[str, object]", entry)
    ignored = is_ignored(table, package, python=ecosystem in {"pip", "uv"})
    return {**_entry_coverage(config, index, table, package), "ignored": ignored}


def _matching_groups(
    groups: dict[str, object] | None, order: list[str] | None, package: str
) -> tuple[list[str], list[str]]:
    """Return the groups that take *package*, and those that are not catch-alls."""
    matching: list[str] = []
    narrow: list[str] = []
    for name in order or []:
        group = mapping((groups or {}).get(name))
        if group is None or not _group_takes(group, package):
            continue
        matching.append(name)
        if group.get("patterns") != ["*"]:
            narrow.append(name)
    return matching, narrow


def _entry_coverage(
    config: ConfigFile,
    index: int,
    entry: dict[str, object],
    package: str,
) -> Coverage:
    """Return the group coverage of *package* in one covering entry."""
    order = config["group_order"][index] if index < len(config["group_order"]) else None
    matching, narrow = _matching_groups(mapping(entry.get("groups")), order, package)
    first = matching[0] if matching else None
    return {
        "entry": index,
        "group": first,
        "catch_all": first is not None and first not in narrow,
        "matching": matching,
        "matching_narrow": narrow,
        "ignored": False,
        "reason": None,
    }
