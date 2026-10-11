"""Properties of Dependabot group coverage over generated configurations.

The coverage fact answers one question: which group takes a package first, and
is it a catch-all. The answer depends on document order, on pattern and
exclusion forms, and on how an entry's directory glob reads. The oracle here
is written from those rules with string operations only, so it shares no code
with the builder's `fnmatch` and regular-expression handling.
"""

from __future__ import annotations

import typing as typ

from hypothesis import given
from hypothesis import strategies as st

from concordat.rules.dependency_family_coverage import coverage, is_ignored

if typ.TYPE_CHECKING:
    from concordat.rules.dependabot_envelope import ConfigFile

_NAMES: typ.Final = ("pyo3", "pyo3-build-config", "serde", "serde-json", "rstest")
_PATTERNS: typ.Final = ("pyo3", "pyo3-*", "serde*", "*", "rstest", "zzz")

_package = st.sampled_from(_NAMES)
_pattern_lists = st.lists(st.sampled_from(_PATTERNS), min_size=1, max_size=3)
_group_specs = st.lists(
    st.tuples(_pattern_lists, st.lists(st.sampled_from(_PATTERNS), max_size=2)),
    min_size=1,
    max_size=4,
)


def _matches(pattern: str, name: str) -> bool:
    """Return whether *pattern* (exact, `*` or `prefix*`) names *name*."""
    if pattern == "*":
        return True
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return name == pattern


def _config(
    groups: list[tuple[list[str], list[str]]], directory: str, ignore: list[str]
) -> ConfigFile:
    """Return a decoded configuration whose one entry holds *groups* in order."""
    named = {
        f"g{index}": {"patterns": patterns, "exclude-patterns": excluded}
        for index, (patterns, excluded) in enumerate(groups)
    }
    entry = {
        "package-ecosystem": "cargo",
        "directory": directory,
        "groups": named,
        "ignore": [{"dependency-name": name} for name in ignore],
    }
    return {
        "path": ".github/dependabot.yml",
        "parsed": {"version": 2, "updates": [entry]},
        "error": None,
        "group_order": [list(named)],
    }


@given(groups=_group_specs, package=_package)
def test_the_first_group_in_document_order_takes_the_package(
    groups: list[tuple[list[str], list[str]]], package: str
) -> None:
    """The first matching, unexcluded group wins; `*` alone is the catch-all."""
    expected = [
        f"g{index}"
        for index, (patterns, excluded) in enumerate(groups)
        if any(_matches(p, package) for p in patterns)
        and not any(_matches(p, package) for p in excluded)
    ]
    narrow = [
        f"g{index}"
        for index, (patterns, _) in enumerate(groups)
        if f"g{index}" in expected and patterns != ["*"]
    ]

    fact = coverage(_config(groups, "/", []), "cargo", ["/"], package)

    assert fact["matching"] == expected
    assert fact["matching_narrow"] == narrow
    assert fact["group"] == (expected[0] if expected else None)
    assert fact["catch_all"] is (bool(expected) and expected[0] not in narrow)


@given(
    glob=st.sampled_from(["/", "/rust", "/rust/", "rust", "/*", "/**", "/rust/*"]),
    directory=st.sampled_from(["/", "/rust", "/rust/native", "/other"]),
)
def test_a_directory_glob_covers_what_dependabot_reads_it_to_cover(
    glob: str, directory: str
) -> None:
    """`*` stays in one segment, `**` spans segments and slashes are optional."""
    wanted = [part for part in directory.split("/") if part]
    shape = [part for part in glob.split("/") if part]
    if shape == ["**"]:
        expected = True
    elif shape == ["*"]:
        # As Ruby's File.fnmatch reads it, `*` may match nothing, so `/*`
        # reaches the root as well as every one-segment directory.
        expected = len(wanted) <= 1
    elif shape == ["rust", "*"]:
        expected = len(wanted) == 2 and wanted[0] == "rust"
    else:
        expected = wanted == shape
    config = _config([(["*"], [])], glob, [])

    covered = coverage(config, "cargo", [directory], "serde")["entry"] is not None

    assert covered is expected


@given(ignored=st.lists(st.sampled_from(_PATTERNS), max_size=3), package=_package)
def test_an_ignore_rule_names_a_package_by_pattern(
    ignored: list[str], package: str
) -> None:
    """A wildcard `dependency-name` ignores every package it spans."""
    entry: dict[str, object] = {
        "ignore": [{"dependency-name": name} for name in ignored]
    }

    assert is_ignored(entry, package, python=False) is any(
        _matches(name, package) for name in ignored
    )


@given(
    spelling=st.sampled_from([
        "Typing_Extensions",
        "typing.extensions",
        "typing-extensions",
    ])
)
def test_python_ignore_names_are_compared_normalised(spelling: str) -> None:
    """PEP 503 spellings of one package are one package to a Python ignore."""
    entry: dict[str, object] = {"ignore": [{"dependency-name": spelling}]}

    assert is_ignored(entry, "typing-extensions", python=True)
    assert not is_ignored(entry, "typing-extensions", python=False) or (
        spelling == "typing-extensions"
    )
