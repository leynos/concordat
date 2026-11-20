"""Specify the version comparisons the dependency-family facts rely on.

The audit cannot depend on `packaging` or a registry, so it compares release
lines itself. These tests pin the two rules that decide a finding: Cargo reads
a bare `0.y` bump as a major update while Python reads it as a minor one, and a
documented upper bound is held only while the declaration stays at or below it.
"""

from __future__ import annotations

import pytest

from concordat.rules.dependency_family_versions import (
    HELD,
    OPEN,
    RELAXED,
    UNKNOWN,
    bound_status,
    compare,
    lowest_version,
    parse_version,
    satisfies,
    upper_bound,
)


def test_release_segments_ignore_a_suffix() -> None:
    """A pre-release or build suffix does not make one release line incomparable."""
    assert parse_version("v0.28.3-rc.1") == (0, 28, 3)
    assert compare((0, 28), (0, 28, 0)) == 0


def test_an_unparseable_version_is_unknown_not_zero() -> None:
    """A spelling this module cannot read must not compare as a real release."""
    assert parse_version("latest") is None
    assert satisfies(">=1", "latest", "pip") is None


@pytest.mark.parametrize(
    ("requirement", "version", "ecosystem", "expected"),
    [
        ("0.28.3", "0.28.9", "cargo", True),
        ("0.28.3", "0.29.0", "cargo", False),
        (">=1.13.3,<2.0.0", "1.14.0", "pip", True),
        (">=1.13.3,<2.0.0", "2.0.0", "pip", False),
        ("~=4.0", "4.9.0", "pip", True),
        ("~=4.0", "5.0.0", "pip", False),
        ("!=4.0.1", "4.0.1", "pip", False),
    ],
)
def test_requirements_are_read_as_conjunctions(
    requirement: str, version: str, ecosystem: str, *, expected: bool
) -> None:
    """Every clause must hold; one failing clause makes the requirement false."""
    assert satisfies(requirement, version, ecosystem) is expected


def test_a_caret_bound_is_the_next_breaking_line() -> None:
    """`0.28.3` admits the 0.28 line and stops before 0.29."""
    bound = upper_bound("0.28.3", "cargo")

    assert bound is not None
    assert compare(bound.version, (0, 29)) == 0
    assert bound.inclusive is False


def test_a_tilde_bound_keeps_the_last_segment_floating() -> None:
    """PEP 440 `~=4.0` admits 4.x and stops at 5."""
    bound = upper_bound("~=4.0", "pip")

    assert bound is not None
    assert compare(bound.version, (5,)) == 0


def test_the_lowest_admitted_version_is_only_evidence() -> None:
    """An open requirement has no lowest release, so no representative version."""
    assert lowest_version(">=1.13.3,<2.0.0") == "1.13.3"
    assert lowest_version("0.28.3") == "0.28.3"
    assert lowest_version("*") is None


@pytest.mark.parametrize(
    ("recorded", "current", "ecosystem", "expected"),
    [
        ("<0.29", "0.28.3", "cargo", HELD),
        ("<0.29", ">=0.28,<0.29", "cargo", HELD),
        ("<0.29", "0.29.1", "cargo", RELAXED),
        ("<0.29", ">=0.28", "cargo", OPEN),
        ("latest", "0.28.3", "cargo", UNKNOWN),
    ],
)
def test_a_documented_bound_is_held_only_while_the_declaration_stays_below(
    recorded: str, current: str, ecosystem: str, *, expected: str
) -> None:
    """Relaxing or removing the ceiling is a compatibility change."""
    assert bound_status(recorded, current, ecosystem) == expected
