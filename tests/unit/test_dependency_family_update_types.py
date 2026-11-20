"""Specify Dependabot's update classification per ecosystem.

A group that lists `update-types: [minor, patch]` does not take a Cargo `0.y`
bump, because Cargo treats the transition that can break a pre-1.0 family as a
major update. The same transition is a minor update for a Python package. The
facts must therefore carry the breaking class for each declaration rather than
one class for a relationship.
"""

from __future__ import annotations

import pytest

from concordat.rules.dependency_family_update_types import (
    UPDATE_TYPES,
    breaking_update_type,
    update_type,
)


def test_the_update_classes_are_the_three_dependabot_names() -> None:
    """The policy lists `update-types` by these names."""
    assert UPDATE_TYPES == ("major", "minor", "patch")


@pytest.mark.parametrize(
    ("ecosystem", "current", "updated", "expected"),
    [
        ("cargo", "0.28.3", "0.28.4", "patch"),
        ("cargo", "0.28.3", "0.29.0", "major"),
        ("cargo", "1.2.3", "1.3.0", "minor"),
        ("cargo", "1.2.3", "2.0.0", "major"),
        ("uv", "0.28.3", "0.29.2", "minor"),
        ("pip", "1.2.3", "1.3.0", "minor"),
        ("pip", "1.2.3", "2.0.0", "major"),
        ("cargo", "1", "1.0.1", "patch"),
        ("cargo", "0.2", "0.3", "major"),
        ("pip", "1", "2", "major"),
        ("uv", "0", "0.1", "minor"),
    ],
)
def test_a_pre_1_0_bump_is_major_for_cargo_and_minor_for_python(
    ecosystem: str, current: str, updated: str, *, expected: str
) -> None:
    """The same package bump moves differently in each ecosystem."""
    assert update_type(ecosystem, current, updated) == expected


def test_an_unchanged_release_has_no_update_class() -> None:
    """No release moved, so there is no update for a group to take."""
    assert update_type("cargo", "1.2.3", "1.2.3") is None
    assert update_type("pip", "0.28", "0.28.0") is None


def test_an_unknown_version_has_no_update_class() -> None:
    """A version this audit cannot parse is a fact with no class, not a guess."""
    assert update_type("cargo", "latest", "0.29.0") is None
    assert breaking_update_type("cargo", None) is None
    assert breaking_update_type("cargo", "not-a-version") is None


@pytest.mark.parametrize(
    ("ecosystem", "version", "expected"),
    [
        ("cargo", "0.28.3", "major"),
        ("cargo", "1.2.3", "major"),
        ("uv", "0.28.3", "minor"),
        ("uv", "1.2.3", "major"),
    ],
)
def test_the_breaking_class_follows_the_current_line(
    ecosystem: str, version: str, *, expected: str
) -> None:
    """Python keeps a 0.x line together through a minor bump; Cargo does not."""
    assert breaking_update_type(ecosystem, version) == expected
