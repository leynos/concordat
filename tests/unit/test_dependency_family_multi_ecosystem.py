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


def _split_config() -> ConfigFile:
    """Return the two-entry shape: the group entry first, then the plain entry.

    Dependabot builds one job per entry. An entry that joins a multi-ecosystem
    group runs only that group, over the dependencies its `patterns` take, so
    the member's ecosystem also needs a plain entry for everything else. The
    plain entry ignores the member, which the group entry already handles.

    Returns
    -------
    ConfigFile
        A decoded configuration holding the group entry then the plain entry.
    """
    group_entry = _entry(
        "uv", **{"multi-ecosystem-group": "maturin-pyo3", "patterns": ["maturin"]}
    )
    plain_entry = _entry(
        "uv",
        groups={
            "pylint-astroid": {"patterns": ["pylint", "astroid"]},
            "minor-and-patch": {"patterns": ["*"], "update-types": ["minor", "patch"]},
        },
        ignore=[{"dependency-name": "maturin"}],
    )
    return _config([group_entry, plain_entry], _DEFINED)


def test_the_group_entry_handles_the_member_it_takes() -> None:
    """The member is covered by the group entry, not by the plain entry."""
    fact = coverage(_split_config(), "uv", ["/"], "maturin")

    assert (fact["entry"], fact["multi_group"], fact["ignored"]) == (
        0,
        "maturin-pyo3",
        False,
    )


@pytest.mark.parametrize("package", ["pylint", "astroid"])
def test_the_plain_entry_handles_what_the_group_entry_does_not_take(
    package: str,
) -> None:
    """Skip the group entry for a package its `patterns` miss, as Dependabot does."""
    fact = coverage(_split_config(), "uv", ["/"], package)

    assert (fact["entry"], fact["group"], fact["multi_group"]) == (
        1,
        "pylint-astroid",
        None,
    )


def test_an_entry_that_ignores_a_package_does_not_handle_it() -> None:
    """The first entry that actually processes the package supplies its facts."""
    ignoring = _entry("uv", ignore=[{"dependency-name": "pytest"}])
    handling = _entry("uv", groups={"minor-and-patch": {"patterns": ["*"]}})
    config = _config([ignoring, handling], None)

    assert coverage(config, "uv", ["/"], "pytest")["entry"] == 1


def test_a_package_only_ignored_keeps_the_ignoring_entry() -> None:
    """When no entry processes the package, the first covering entry answers."""
    ignoring = _entry("uv", ignore=[{"dependency-name": "pytest"}])
    config = _config([ignoring], None)

    fact = coverage(config, "uv", ["/"], "pytest")

    assert (fact["entry"], fact["ignored"]) == (0, True)


def test_a_lone_group_entry_leaves_other_packages_unhandled() -> None:
    """The merged cuprum shape: nothing handles pylint, and the fact says so."""
    group_entry = _entry(
        "uv", **{"multi-ecosystem-group": "maturin-pyo3", "patterns": ["maturin"]}
    )
    config = _config([group_entry], _DEFINED)

    fact = coverage(config, "uv", ["/"], "pylint")

    assert (fact["entry"], fact["group"], fact["multi_group"]) == (0, None, None)


def test_when_no_entry_handles_a_package_the_first_covering_entry_answers() -> None:
    """Both entries ignore it, so the document-order first one supplies the fact."""
    first = _entry("uv", ignore=[{"dependency-name": "pytest"}])
    second = _entry("uv", ignore=[{"dependency-name": "pytest"}])
    config = _config([first, second], None)

    assert coverage(config, "uv", ["/"], "pytest")["entry"] == 0


def test_a_package_one_entry_ignores_and_no_entry_handles_reads_as_ignored() -> None:
    """The group entry does not take it and the plain entry ignores it: held back."""
    group_entry = _entry(
        "uv", **{"multi-ecosystem-group": "maturin-pyo3", "patterns": ["maturin"]}
    )
    plain_entry = _entry(
        "uv",
        groups={"minor-and-patch": {"patterns": ["*"]}},
        ignore=[{"dependency-name": "maturin"}, {"dependency-name": "pytest-bdd"}],
    )
    config = _config([group_entry, plain_entry], _DEFINED)

    assert coverage(config, "uv", ["/"], "pytest-bdd")["ignored"] is True


def test_a_package_another_entry_handles_does_not_read_as_ignored() -> None:
    """The plain entry ignores maturin but the group entry handles it: not held back."""
    fact = coverage(_split_config(), "uv", ["/"], "maturin")

    assert fact["ignored"] is False
