"""Specify how a multi-ecosystem Dependabot group reads as a coverage fact.

Dependabot combines the updates of every entry that joins one multi-ecosystem
group into a single pull request. The coverage fact records the group an entry
joins for a package, so the policy can ask whether the members of a tuple such
as maturin and PyO3 travel in one pull request. The fact is read only from
what the configuration states: the entry must name a group the file defines
and its `patterns` must take the package. Anything less leaves the package
outside the group, because a half-wired group protects nothing.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.dependency_family_coverage import coverage

if typ.TYPE_CHECKING:
    from concordat.rules.dependabot_envelope import ConfigFile


def _config(updates: list[dict[str, object]], groups: object) -> ConfigFile:
    """Return a decoded configuration with *updates* and top-level *groups*."""
    parsed: dict[str, object] = {"version": 2, "updates": updates}
    if groups is not None:
        parsed["multi-ecosystem-groups"] = groups
    return {
        "path": ".github/dependabot.yml",
        "parsed": parsed,
        "error": None,
        "group_order": [
            list(typ.cast("dict[str, object]", entry.get("groups") or {}))
            for entry in updates
        ],
    }


def _entry(ecosystem: str, **extra: object) -> dict[str, object]:
    """Return an `updates` entry for the root directory."""
    return {"package-ecosystem": ecosystem, "directory": "/", **extra}


_DEFINED: typ.Final = {"maturin-pyo3": {"schedule": {"interval": "daily"}}}


def test_an_entry_joining_a_defined_group_with_matching_patterns_reports_it() -> None:
    """The group name is recorded when its patterns take the package."""
    config = _config(
        [
            _entry(
                "cargo",
                **{"multi-ecosystem-group": "maturin-pyo3", "patterns": ["pyo3"]},
            )
        ],
        _DEFINED,
    )

    assert coverage(config, "cargo", ["/"], "pyo3")["multi_group"] == "maturin-pyo3"


def test_a_pattern_glob_takes_the_package() -> None:
    """A trailing-star pattern reads as Dependabot reads it."""
    config = _config(
        [
            _entry(
                "cargo",
                **{"multi-ecosystem-group": "maturin-pyo3", "patterns": ["pyo3*"]},
            )
        ],
        _DEFINED,
    )

    assert coverage(config, "cargo", ["/"], "pyo3-build-config")["multi_group"] == (
        "maturin-pyo3"
    )


def test_python_names_match_patterns_in_their_normalised_form() -> None:
    """`Typing_Extensions` and `typing-extensions` are one uv package."""
    config = _config(
        [
            _entry(
                "uv",
                **{"multi-ecosystem-group": "maturin-pyo3", "patterns": ["Maturin"]},
            )
        ],
        _DEFINED,
    )

    assert coverage(config, "uv", ["/"], "maturin")["multi_group"] == "maturin-pyo3"


@pytest.mark.parametrize(
    ("entry_extra", "groups"),
    [
        pytest.param({}, _DEFINED, id="entry-joins-no-group"),
        pytest.param(
            {"multi-ecosystem-group": "maturin-pyo3"}, _DEFINED, id="no-patterns"
        ),
        pytest.param(
            {"multi-ecosystem-group": "maturin-pyo3", "patterns": []},
            _DEFINED,
            id="empty-patterns",
        ),
        pytest.param(
            {"multi-ecosystem-group": "maturin-pyo3", "patterns": ["serde"]},
            _DEFINED,
            id="patterns-miss-the-package",
        ),
        pytest.param(
            {"multi-ecosystem-group": "other", "patterns": ["pyo3"]},
            _DEFINED,
            id="group-not-defined",
        ),
        pytest.param(
            {"multi-ecosystem-group": "maturin-pyo3", "patterns": ["pyo3"]},
            None,
            id="no-top-level-groups",
        ),
        pytest.param(
            {"multi-ecosystem-group": "maturin-pyo3", "patterns": ["pyo3"]},
            ["maturin-pyo3"],
            id="top-level-groups-not-a-mapping",
        ),
        pytest.param(
            {"multi-ecosystem-group": 7, "patterns": ["pyo3"]},
            _DEFINED,
            id="group-name-not-text",
        ),
        pytest.param(
            {"multi-ecosystem-group": "maturin-pyo3", "patterns": "pyo3"},
            _DEFINED,
            id="patterns-not-a-list",
        ),
    ],
)
def test_a_half_wired_group_is_not_reported(
    entry_extra: dict[str, object], groups: object
) -> None:
    """Every missing link leaves the package outside any group."""
    config = _config([_entry("cargo", **entry_extra)], groups)

    assert coverage(config, "cargo", ["/"], "pyo3")["multi_group"] is None


def test_the_fact_coexists_with_the_entrys_own_groups() -> None:
    """A multi-ecosystem group does not disturb the single-ecosystem group fact."""
    config = _config(
        [
            _entry(
                "cargo",
                groups={"minor-and-patch": {"patterns": ["*"]}},
                **{"multi-ecosystem-group": "maturin-pyo3", "patterns": ["pyo3"]},
            )
        ],
        _DEFINED,
    )

    fact = coverage(config, "cargo", ["/"], "pyo3")

    assert (fact["group"], fact["multi_group"]) == ("minor-and-patch", "maturin-pyo3")


def test_a_package_no_entry_covers_has_no_multi_group() -> None:
    """The missing-coverage fact carries an empty multi-ecosystem field."""
    config = _config([_entry("npm")], _DEFINED)

    fact = coverage(config, "cargo", ["/"], "pyo3")

    assert (fact["entry"], fact["multi_group"]) == (None, None)
