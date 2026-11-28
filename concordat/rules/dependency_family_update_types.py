"""Classify a dependency update the way Dependabot does for each ecosystem.

A group's `update-types` only has its documented meaning if the rule classifies
an update the way the updater will. The distinction this module exists for is
pre-1.0 Cargo: Dependabot treats a `0.y` bump as a major update, so a group
covering only `minor` and `patch` does not take it. Python projects use semver
classes, where the same `0.28.3 -> 0.29.2` bump is a minor update.

The classification mirrors dependabot-core's `SemverGroupingRules` and Cargo
version classes as read on 2026-10-30. No pinned commit of dependabot-core is
verified here, so the envelope never claims the classification is certified;
it records the fact the policy acts on. A version this module cannot parse
yields None, which the policy reports as indeterminate rather than compliant.
"""

from __future__ import annotations

import typing as typ

from .dependency_family_versions import parse_version

UPDATE_TYPES: typ.Final = ("major", "minor", "patch")
MAJOR: typ.Final = "major"
MINOR: typ.Final = "minor"
PATCH: typ.Final = "patch"


def update_type(ecosystem: str, current: str, updated: str) -> str | None:
    """Return Dependabot's update class for one version transition.

    Parameters
    ----------
    ecosystem:
        ``cargo``, or a Python ecosystem (``pip``/``uv``).
    current:
        The version before the update.
    updated:
        The version the update would install.

    Returns
    -------
    str | None
        ``major``, ``minor`` or ``patch``, or None when either version is not
        parseable. A transition between equal releases has no update class.
    """
    before = parse_version(current)
    after = parse_version(updated)
    if before is None or after is None:
        return None
    # Pad short spellings such as `1` or `0.2` so a missing component reads as
    # zero rather than making the class comparison skip a release line.
    before = _padded(before)
    after = _padded(after)
    if ecosystem == "cargo":
        return _cargo_type(before, after)
    return _semver_type(before, after)


def _padded(parts: tuple[int, ...]) -> tuple[int, ...]:
    """Return *parts* with missing release components filled as zero."""
    return parts + (0,) * max(0, 3 - len(parts))


def _semver_type(before: tuple[int, ...], after: tuple[int, ...]) -> str | None:
    """Return the semver class of a transition between padded releases."""
    if before == after:
        return None
    if before[0] != after[0]:
        return MAJOR
    if before[1] != after[1]:
        return MINOR
    return PATCH


def _cargo_type(before: tuple[int, ...], after: tuple[int, ...]) -> str | None:
    """Return Cargo's update class, where a pre-1.0 minor bump is major.

    This follows Cargo's compatibility rule: while the major release is zero,
    changing the minor component is a breaking change, and Dependabot files it
    as a major update. A patch change inside one zero-minor line stays a patch.

    Returns
    -------
    str | None
        ``major``, ``minor`` or ``patch``, or None for equal releases.
    """
    if before == after:
        return None
    if before[0] != after[0]:
        return MAJOR
    if before[0] != 0:
        return MINOR if before[1] != after[1] else PATCH
    before_minor = before[1] if len(before) > 1 else 0
    after_minor = after[1] if len(after) > 1 else 0
    if before_minor != after_minor:
        return MAJOR
    return PATCH


def breaking_update_type(ecosystem: str, version: str | None) -> str | None:
    """Return the update class that can break *version*'s current line.

    For Cargo the breaking class is always a major update: a pre-1.0 `0.y`
    bump is classified as major, and after 1.0 a major bump is. For Python a
    pre-1.0 breaking bump is a minor update and a post-1.0 one is major.

    Returns
    -------
    str | None
        The update class, or None when *version* is absent or unparseable.
    """
    if version is None:
        return None
    parts = parse_version(version)
    if parts is None:
        return None
    # Ask the classifier about a release one breaking step above the current
    # line, so there is one rule for what "breaking" means per ecosystem.
    target = _next_breaking(parts, ecosystem)
    return update_type(ecosystem, version, ".".join(str(part) for part in target))


def _next_breaking(parts: tuple[int, ...], ecosystem: str) -> tuple[int, int, int]:
    """Return a release beyond the current line's compatibility window."""
    if ecosystem == "cargo" or parts[0] != 0:
        return (parts[0] + 1, 0, 0)
    return (0, (parts[1] if len(parts) > 1 else 0) + 1, 0)
