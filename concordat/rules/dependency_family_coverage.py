"""Read how Dependabot's configuration treats a published package.

The coverage fact answers four questions for one package in one directory:
which `updates` entry covers the directory, which group takes the package first
for each update class (document order is part of the configuration's meaning,
so a narrow group must precede a catch-all), whether an `ignore` rule protects
it, and which multi-ecosystem group, if any, the entry joins for it. Python
names are compared in their PEP 503 form, as Dependabot does for pip and uv.

`update-types` is evaluated per class because Dependabot files a pre-1.0 Cargo
`0.y` bump as a major update while the same transition is a minor update for a
Python project. A group that takes only minor and patch updates does not take
the bump that can break the family.

Dependabot builds one job per `updates` entry. When several entries cover the
same directory, the facts belong to the first entry that processes the package:
one that joins a multi-ecosystem group runs only that group, over the
dependencies its `patterns` take, and an entry that ignores the package skips
it. An `ignore` rule therefore holds a package back only when every covering
entry ignores it.
"""

from __future__ import annotations

import fnmatch
import pathlib
import re
import typing as typ

from .dependency_family_update_types import (
    UPDATE_TYPES,
    breaking_update_type,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .dependabot_envelope import ConfigFile

_SEMVER_PREFIX: typ.Final = "version-update:semver-"
_SINGLE: typ.Final = "single"
_MULTI: typ.Final = "multi"


class GroupMatch(typ.TypedDict):
    """One group that takes a package for an update class."""

    entry: int
    group: str
    directory: str
    catch_all: bool
    selection: str


class IgnoreRule(typ.TypedDict):
    """One `ignore` rule that names the package."""

    rule: int
    dependency_name: str
    update_types: list[str]
    versions: str | None
    partial: bool
    protects_breaking: bool


class Coverage(typ.TypedDict):
    """How Dependabot treats one declaration's published package name."""

    entry: int | None
    group: str | None
    catch_all: bool
    matching: list[str]
    matching_narrow: list[str]
    ignored: bool
    multi_group: str | None
    reason: str | None
    by_update_type: dict[str, GroupMatch | None]
    matching_narrow_by_update_type: dict[str, list[GroupMatch]]
    breaking_update_type: str | None
    ignore_rules: list[IgnoreRule]
    multi_ecosystem_group: GroupMatch | None


def mapping(value: object) -> dict[str, object] | None:
    """Return *value* as a string-keyed mapping, or None when it is not one."""
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else None


def sequence(value: object) -> list[object] | None:
    """Return *value* as a list, or None when it is not one."""
    return typ.cast("list[object]", value) if isinstance(value, list) else None


def normalise(name: str) -> str:
    """Return a Python package name in its comparison form (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def directory_matches(pattern: str, directory: str) -> bool:
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


def entry_directories(entry: dict[str, object]) -> list[str]:
    """Return the directories one `updates` entry lists."""
    listed = sequence(entry.get("directories"))
    if listed is not None:
        return [str(item) for item in listed]
    single = entry.get("directory")
    return [str(single)] if single is not None else []


def _update_types(value: object) -> list[str] | None:
    """Return the update classes a group or ignore rule names, if any."""
    listed = sequence(value)
    if listed is None:
        return None
    classes = []
    for item in listed:
        text = str(item)
        classes.append(text.removeprefix(_SEMVER_PREFIX))
    return classes


def _patterns_match(group: dict[str, object], package: str) -> bool:
    """Return whether a group's patterns name *package* and no exclusion does."""
    patterns = sequence(group.get("patterns"))
    if patterns is None:
        return False
    excluded = sequence(group.get("exclude-patterns")) or []
    if any(fnmatch.fnmatchcase(package, str(pattern)) for pattern in excluded):
        return False
    return any(fnmatch.fnmatchcase(package, str(pattern)) for pattern in patterns)


def _is_catch_all(group: dict[str, object]) -> bool:
    """Return whether a group's patterns contain `*` anywhere."""
    return any(str(pattern) == "*" for pattern in sequence(group.get("patterns")) or [])


def _group_takes(
    group: dict[str, object], package: str, update_type: str | None = None
) -> bool:
    """Return whether *group* takes one update of *package*.

    Returns
    -------
    bool
        Whether the group applies and, when an update class is given, lists
        that class (or lists none and so takes every class).
    """
    if group.get("applies-to", "version-updates") != "version-updates":
        return False
    if not _patterns_match(group, package):
        return False
    if update_type is None:
        return True
    classes = _update_types(group.get("update-types"))
    return classes is None or update_type in classes


def _name_matches(name: str, package: str, *, python: bool) -> bool:
    """Return whether one `dependency-name` pattern names *package*."""
    return fnmatch.fnmatchcase(package, normalise(name) if python else name)


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
        _name_matches(name, package, python=python) for name in _ignore_names(entry)
    )


def _ignore_rules(
    entry: dict[str, object],
    package: str,
    *,
    python: bool,
    breaking: str | None,
) -> list[IgnoreRule]:
    """Return the ignore rules naming *package*, with their protection scope.

    A rule with `versions` protects only the versions it lists, which this
    audit does not interpret, so it is recorded as partial evidence rather than
    as a bound. A rule without `update-types` covers every class.

    Returns
    -------
    list[IgnoreRule]
        The matching rules, in document order.
    """
    found: list[IgnoreRule] = []
    for index, raw in enumerate(sequence(entry.get("ignore")) or []):
        rule = mapping(raw) or {}
        name = rule.get("dependency-name")
        if not isinstance(name, str) or not _name_matches(name, package, python=python):
            continue
        classes = _update_types(rule.get("update-types")) or []
        versions = rule.get("versions")
        partial = versions is not None
        protects = not partial and (
            not classes or (breaking is not None and breaking in classes)
        )
        found.append({
            "rule": index,
            "dependency_name": name,
            "update_types": classes,
            "versions": str(versions) if versions is not None else None,
            "partial": partial,
            "protects_breaking": protects,
        })
    return found


def directories_of(manifest: str) -> list[str]:
    """Return a manifest's directory and each ancestor, as Dependabot directories.

    A workspace member is updated through its workspace root, so a covering
    entry for an ancestor directory covers the member too. The Cargo caller
    narrows this list to ancestors whose workspace actually lists the member;
    this function supplies the unfiltered chain.

    Returns
    -------
    list[str]
        The `/`-rooted directories from the manifest's own up to the root.
    """
    parent = pathlib.PurePosixPath(manifest).parent
    chain = [parent, *parent.parents]
    return ["/" + str(item).strip("./") if str(item) != "." else "/" for item in chain]


def _empty_types() -> dict[str, GroupMatch | None]:
    """Return a per-update-class map with every class unclaimed."""
    return dict.fromkeys(UPDATE_TYPES)


def _missing_coverage(reason: str, breaking: str | None) -> Coverage:
    """Return the coverage fact for a package no group configuration reaches."""
    return {
        "entry": None,
        "group": None,
        "catch_all": False,
        "matching": [],
        "matching_narrow": [],
        "ignored": False,
        "multi_group": None,
        "reason": reason,
        "by_update_type": _empty_types(),
        "matching_narrow_by_update_type": {kind: [] for kind in UPDATE_TYPES},
        "breaking_update_type": breaking,
        "ignore_rules": [],
        "multi_ecosystem_group": None,
    }


def _entry_covers(entry: object, ecosystem: str, directory: str) -> bool:
    """Return whether an `updates` entry updates *ecosystem* in *directory*."""
    table = mapping(entry)
    return (
        table is not None
        and table.get("package-ecosystem") == ecosystem
        and any(directory_matches(p, directory) for p in entry_directories(table))
    )


def _covering_directory(
    entry: dict[str, object], directories: cabc.Sequence[str]
) -> str:
    """Return the first directory of *directories* the entry covers."""
    return next(
        directory
        for directory in directories
        if any(
            directory_matches(pattern, directory)
            for pattern in entry_directories(entry)
        )
    )


def coverage(
    config: ConfigFile | None,
    ecosystem: str,
    directories: cabc.Sequence[str],
    package: str,
    *,
    version: str | None = None,
) -> Coverage:
    """Return how Dependabot's configuration treats one published package.

    Parameters
    ----------
    config:
        The decoded Dependabot configuration, or None.
    ecosystem:
        The declaration's ecosystem, which selects the entry.
    directories:
        The directories that may cover the manifest, nearest first.
    package:
        The published package name Dependabot matches.
    version:
        The resolved or declared representative version, used to identify
        the update class that can break the current line.

    Returns
    -------
    Coverage
        The covering entry, the group that takes the package for each update
        class, whether the first group is a catch-all, ignore-rule protection,
        and the reason when none could be established.
    """
    breaking = breaking_update_type(ecosystem, version)
    if config is None or config["error"] is not None:
        return _missing_coverage("no decodable Dependabot configuration", breaking)
    updates = sequence((mapping(config["parsed"]) or {}).get("updates"))
    if updates is None:
        return _missing_coverage(
            "the Dependabot configuration has no updates", breaking
        )
    python = ecosystem in {"pip", "uv"}
    covering = _covering_entries(updates, ecosystem, directories)
    if not covering:
        return _missing_coverage(
            f"no {ecosystem} entry covers {directories[0]}", breaking
        )
    index, table = _handling_entry(config, covering, package, python=python)
    rules = _ignore_rules(table, package, python=python, breaking=breaking)
    return {
        **_entry_coverage(
            config, index, table, directories, package, breaking, python=python
        ),
        "ignored": _is_held_back(config, covering, package, python=python),
        "multi_group": _multi_group(config, table, package, python=python),
        "ignore_rules": rules,
    }


def _is_held_back(
    config: ConfigFile,
    covering: list[tuple[int, dict[str, object]]],
    package: str,
    *,
    python: bool,
) -> bool:
    """Return whether an `ignore` rule keeps every covering entry off *package*.

    A package one entry ignores and another processes is still updated, so an
    `ignore` rule protects it only when no covering entry handles it.

    Returns
    -------
    bool
        Whether the package is held back by an `ignore` rule.
    """
    if any(_processes(config, entry, package, python=python) for _, entry in covering):
        return False
    return any(is_ignored(entry, package, python=python) for _, entry in covering)


def _covering_entries(
    updates: list[object], ecosystem: str, directories: cabc.Sequence[str]
) -> list[tuple[int, dict[str, object]]]:
    """Return the entries covering the nearest directory that any entry covers.

    Returns
    -------
    list[tuple[int, dict[str, object]]]
        Index and entry, in document order; empty when none covers a directory.
    """
    for directory in directories:
        found = [
            (index, typ.cast("dict[str, object]", entry))
            for index, entry in enumerate(updates)
            if _entry_covers(entry, ecosystem, directory)
        ]
        if found:
            return found
    return []


def _handling_entry(
    config: ConfigFile,
    covering: list[tuple[int, dict[str, object]]],
    package: str,
    *,
    python: bool,
) -> tuple[int, dict[str, object]]:
    """Return the first covering entry that processes *package*.

    Dependabot builds one job per entry. An entry that joins a multi-ecosystem
    group runs only that group, over the dependencies its `patterns` take, and
    an entry that ignores a package skips it, so the facts belong to the first
    entry that does neither. When none processes the package the first covering
    entry answers, and its facts show the package is not taken by any group.

    Returns
    -------
    tuple[int, dict[str, object]]
        The entry's index and mapping.
    """
    for index, entry in covering:
        if _processes(config, entry, package, python=python):
            return index, entry
    return covering[0]


def _processes(
    config: ConfigFile, entry: dict[str, object], package: str, *, python: bool
) -> bool:
    """Return whether *entry*'s job considers *package* for a version update."""
    if entry.get("multi-ecosystem-group") is not None:
        return _multi_group(config, entry, package, python=python) is not None
    return not is_ignored(entry, package, python=python)


def _joined_group(config: ConfigFile, entry: dict[str, object]) -> str | None:
    """Return the defined multi-ecosystem group *entry* names, if any."""
    name = entry.get("multi-ecosystem-group")
    defined = mapping((mapping(config["parsed"]) or {}).get("multi-ecosystem-groups"))
    if defined is None or not isinstance(name, str):
        return None
    return name if name in defined else None


def _multi_group(
    config: ConfigFile, entry: dict[str, object], package: str, *, python: bool
) -> str | None:
    """Return the multi-ecosystem group *entry* joins for *package*, if any.

    Dependabot folds the updates of every entry that names one group into a
    single pull request, so the name is a fact only when the whole chain holds:
    the entry names a group the file defines at the top level, and the entry's
    `patterns` take the package. A named but undefined group, or patterns that
    miss the package, leave it outside the group.

    Returns
    -------
    str | None
        The group name, or None when the package is not in a defined group.
    """
    name = _joined_group(config, entry)
    if name is None:
        return None
    wanted = normalise(package) if python else package
    patterns = [str(pattern) for pattern in sequence(entry.get("patterns")) or []]
    comparable = [normalise(pattern) if python else pattern for pattern in patterns]
    taken = any(fnmatch.fnmatchcase(wanted, pattern) for pattern in comparable)
    return name if taken else None


def _matching_groups(
    groups: dict[str, object] | None, order: list[str] | None, package: str
) -> tuple[list[str], list[str], dict[str, dict[str, object]]]:
    """Return groups that take *package*, the narrow ones, and their bodies."""
    matching: list[str] = []
    narrow: list[str] = []
    bodies: dict[str, dict[str, object]] = {}
    for name in order or []:
        group = mapping((groups or {}).get(name))
        if group is None or not _group_takes(group, package):
            continue
        bodies[name] = group
        matching.append(name)
        if not _is_catch_all(group):
            narrow.append(name)
    return matching, narrow, bodies


def _entry_coverage(
    config: ConfigFile,
    index: int,
    entry: dict[str, object],
    directories: cabc.Sequence[str],
    package: str,
    breaking: str | None,
    *,
    python: bool,
) -> Coverage:
    """Return the group coverage of *package* in one covering entry.

    Returns
    -------
    Coverage
        The coverage fields for the entry, without the cross-entry `ignored`
        fact.
    """
    order = config["group_order"][index] if index < len(config["group_order"]) else None
    matching, narrow, bodies = _matching_groups(
        mapping(entry.get("groups")), order, package
    )
    directory = _covering_directory(entry, directories)
    multi = _multi_ecosystem_match(
        config, index, entry, directory, package, python=python
    )
    by_type: dict[str, GroupMatch | None] = {}
    narrow_by_type: dict[str, list[GroupMatch]] = {}
    for update_type in UPDATE_TYPES:
        first = _first_group(order, bodies, package, update_type, index, directory)
        by_type[update_type] = first or multi
        narrow_for_type = [
            _group_match(index, name, directory, bodies[name])
            for name in order or []
            if name in bodies
            and not _is_catch_all(bodies[name])
            and _group_takes(bodies[name], package, update_type)
        ]
        if multi is not None and not multi["catch_all"]:
            narrow_for_type.append(multi)
        narrow_by_type[update_type] = narrow_for_type
    first = matching[0] if matching else None
    return {
        "entry": index,
        "group": first,
        "catch_all": first is not None and first not in narrow,
        "matching": matching,
        "matching_narrow": narrow,
        "ignored": False,
        "multi_group": None,
        "reason": None,
        "by_update_type": by_type,
        "matching_narrow_by_update_type": narrow_by_type,
        "breaking_update_type": breaking,
        "ignore_rules": [],
        "multi_ecosystem_group": multi,
    }


def _group_match(
    index: int, name: str, directory: str, group: dict[str, object]
) -> GroupMatch:
    """Return the coverage match of one single-ecosystem group."""
    return {
        "entry": index,
        "group": name,
        "directory": directory,
        "catch_all": _is_catch_all(group),
        "selection": _SINGLE,
    }


def _first_group(
    order: list[str] | None,
    bodies: dict[str, dict[str, object]],
    package: str,
    update_type: str,
    index: int,
    directory: str,
) -> GroupMatch | None:
    """Return the first group in document order that takes one update class."""
    for name in order or []:
        group = bodies.get(name)
        if group is not None and _group_takes(group, package, update_type):
            return _group_match(index, name, directory, group)
    return None


def _multi_ecosystem_match(
    config: ConfigFile,
    index: int,
    entry: dict[str, object],
    directory: str,
    package: str,
    *,
    python: bool,
) -> GroupMatch | None:
    """Return the multi-ecosystem group *entry* joins for *package*, if any.

    A multi-ecosystem group has no `update-types`: Dependabot selects the
    entry's dependencies by `patterns` and consolidates every ecosystem that
    names the group into one pull request. Membership therefore covers every
    update class, and the match is a fact the policy may treat as a group that
    takes the package.

    Returns
    -------
    GroupMatch | None
        The match, or None when the entry joins no defined group for the
        package.
    """
    name = _multi_group(config, entry, package, python=python)
    if name is None:
        return None
    return {
        "entry": index,
        "group": name,
        "directory": directory,
        "catch_all": _is_catch_all({"patterns": entry.get("patterns")}),
        "selection": _MULTI,
    }
