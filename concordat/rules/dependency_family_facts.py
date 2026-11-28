"""Share the declaration fact shapes and the requirement helpers.

Both ecosystem collectors produce the same declaration shape, and both need to
read a requirement's form, a lock file, a resolution placeholder and the
Dependabot coverage of a directory. Keeping that vocabulary here means a Cargo
edge and a Python requirement are described by the same fields and the same
rules, rather than by two interpretations that could drift apart.
"""

from __future__ import annotations

import re
import tomllib
import typing as typ

from .dependency_family_coverage import (
    Coverage,
    directory_matches,
    entry_directories,
    mapping,
    sequence,
)
from .dependency_family_versions import lowest_version
from .fs_probe import probe_file

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .dependabot_envelope import ConfigFile
    from .dependency_family_resolution import Resolution


class Declaration(typ.TypedDict):
    """One dependency edge declared in a governed manifest.

    Attributes
    ----------
    section:
        The manifest table the edge sits in.
    target:
        The Cargo target the table applies to, or None.
    feature_context:
        The features the edge is declared under, kept apart from the update
        decision so an optional lane is visible as evidence.
    """

    manifest: str
    ecosystem: str
    section: str
    target: str | None
    optional: bool
    in_default: bool
    default_features: bool
    requested_features: list[str]
    extras: list[str]
    markers: str | None
    workspace_inherited: bool
    declared_name: str
    package: str
    requirement: str
    requirement_kind: str
    upper_bounded: bool
    resolved: Resolution
    coverage: Coverage


class Monitoring(typ.TypedDict):
    """Whether an ecosystem's native Dependabot entry covers a project.

    Attributes
    ----------
    native:
        The ecosystem Dependabot should update the project with: ``cargo``,
        ``uv`` when a `uv.lock` is present, otherwise ``pip``.
    covered:
        The ecosystems that have an entry covering the directory.
    duplicate:
        Whether both `pip` and `uv` entries cover the same project.
    """

    manifest: str
    directory: str
    native: str
    covered: list[str]
    duplicate: bool


def text(value: object) -> str | None:
    """Return a non-blank scalar as text, or None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def strings(value: object) -> list[str]:
    """Return a list's non-blank string items, in order."""
    return [
        item.strip()
        for item in sequence(value) or []
        if isinstance(item, str) and item.strip()
    ]


def classify(requirement: str) -> tuple[str, bool]:
    """Return how a requirement constrains its package, and whether it is bounded.

    An exact pin is not an upper bound: it is the very thing a version-update
    pull request replaces. A bound is an explicit `<`, `<=` or tilde clause.

    Returns
    -------
    tuple[str, bool]
        The requirement kind (`exact`, `bounded`, `caret` or `open`) and
        whether it carries an explicit upper bound.
    """
    literal = requirement.strip()
    if not literal or literal == "*":
        return "open", False
    bounded = any(
        clause.strip().startswith(("<", "~"))
        for clause in literal.split(",")
        if clause.strip()
    )
    if bounded:
        return "bounded", True
    if re.fullmatch(r"(?:==|=)\s*[^*,<>~\s]+", literal):
        return "exact", False
    return "caret", False


def read_lock(lock: pathlib.Path) -> tuple[dict[str, object], str | None]:
    """Return a decoded lock file, or why it could not be read."""
    probe = probe_file(lock)
    if probe.read_error is not None:
        return {}, f"cannot read {lock.name}: {probe.read_error}"
    if not probe.present:
        return {}, f"{lock.name} is absent"
    try:
        return tomllib.loads(lock.read_text(encoding="utf-8")), None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        return {}, f"cannot parse {lock.name}: {error}"


def no_resolution(reason: str, lock: str | None) -> Resolution:
    """Return a resolution carrying only the reason it could not be made."""
    return {
        "versions": [],
        "lock": lock,
        "reason": reason,
        "package_id": None,
        "member_edges": {},
    }


def representative(requirement: str, resolved: Resolution) -> str | None:
    """Return the version whose update class can break the declared line."""
    if resolved["versions"]:
        return resolved["versions"][0]
    return lowest_version(requirement)


def covered_ecosystems(
    config: ConfigFile | None, directories: cabc.Sequence[str]
) -> list[str]:
    """Return the ecosystems with an `updates` entry covering any directory.

    A workspace member is updated through its workspace root, so the caller
    passes the member's own directory and the ancestor workspace directories
    that actually list it. Dependabot then needs only one native entry anywhere
    on that chain.

    Returns
    -------
    list[str]
        The distinct native ecosystems that cover the declared surface.
    """
    if config is None or config["error"] is not None:
        return []
    updates = sequence((mapping(config["parsed"]) or {}).get("updates")) or []
    return sorted({
        str(table["package-ecosystem"])
        for entry in updates
        if (table := mapping(entry)) is not None
        and isinstance(table.get("package-ecosystem"), str)
        and any(
            directory_matches(pattern, directory)
            for pattern in entry_directories(table)
            for directory in directories
        )
    })
