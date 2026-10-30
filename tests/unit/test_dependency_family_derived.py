"""Specify the consumer-declared bounds and derived-input facts.

These are the facts a repository declares in `.concordat`: a compatibility
bound that must not be relaxed, and a canonical value that copied occurrences
must still match. Every selector is resolved here, outside policy evaluation,
and anything that cannot be resolved stays a fact with its reason.
"""

from __future__ import annotations

import typing as typ

from concordat.rules.dependency_family_derived import (
    AMBIGUOUS,
    RESOLVED,
    derived_inputs,
    protected_bounds,
)
from concordat.rules.dependency_family_versions import (
    HELD,
    OPEN,
    RELAXED,
    UNKNOWN,
)

if typ.TYPE_CHECKING:
    import pathlib

    from concordat.rules.dependency_family_envelope import Declaration

_RELATIONSHIPS = {"pyo3-build-config-family": {"pyo3", "pyo3-build-config"}}


def _declaration(
    package: str,
    requirement: str,
    *,
    manifest: str = "Cargo.toml",
    ecosystem: str = "cargo",
) -> Declaration:
    """Return the declaration fields the bound comparison reads."""
    return typ.cast(
        "Declaration",
        {
            "manifest": manifest,
            "package": package,
            "requirement": requirement,
            "ecosystem": ecosystem,
        },
    )


def _bound(
    bound: str,
    *,
    package: str = "pyo3",
    relationship: str = "pyo3-build-config-family",
) -> dict[str, object]:
    """Return one complete protected-bound declaration."""
    return {
        "relationship": relationship,
        "package": package,
        "bound": bound,
        "rationale": "the extension relies on the 0.28 build interface",
        "review_trigger": "review when the extension migrates to 0.30",
    }


def test_a_bound_still_stated_by_the_declaration_is_held() -> None:
    """A declaration at or below the ceiling keeps the recorded bound."""
    problems: list[str] = []
    found = protected_bounds(
        {"protected_bounds": [_bound("<0.29")]},
        [_declaration("pyo3", ">=0.28,<0.29")],
        _RELATIONSHIPS.keys(),
        _RELATIONSHIPS,
        problems,
    )

    assert problems == []
    assert found[0]["status"] == HELD
    assert found[0]["current"] == ">=0.28,<0.29"


def test_a_ceiling_the_declaration_has_moved_past_is_relaxed() -> None:
    """Admitting a version the recorded bound excluded is a compatibility change."""
    found = protected_bounds(
        {"protected_bounds": [_bound("<0.29")]},
        [_declaration("pyo3", "0.29.1")],
        _RELATIONSHIPS.keys(),
        _RELATIONSHIPS,
        [],
    )

    assert found[0]["status"] == RELAXED


def test_a_declaration_with_no_ceiling_is_open() -> None:
    """An unbounded declaration has given the bound up entirely."""
    found = protected_bounds(
        {"protected_bounds": [_bound("<0.29")]},
        [_declaration("pyo3", ">=0.28")],
        _RELATIONSHIPS.keys(),
        _RELATIONSHIPS,
        [],
    )

    assert found[0]["status"] == OPEN


def test_a_bound_this_audit_cannot_parse_is_unknown() -> None:
    """An unparseable bound is indeterminate, never assumed held."""
    found = protected_bounds(
        {"protected_bounds": [_bound("latest")]},
        [_declaration("pyo3", "0.28.3")],
        _RELATIONSHIPS.keys(),
        _RELATIONSHIPS,
        [],
    )

    assert found[0]["status"] == UNKNOWN


def test_a_bound_on_a_non_member_is_a_problem_and_no_fact() -> None:
    """A declaration outside the relationship cannot be read as protection."""
    problems: list[str] = []
    found = protected_bounds(
        {"protected_bounds": [_bound("<0.29", package="serde-pyobject")]},
        [_declaration("serde-pyobject", "0.8.0")],
        _RELATIONSHIPS.keys(),
        _RELATIONSHIPS,
        problems,
    )

    assert found == []
    assert problems == [
        (
            "a dependencies.protected_bounds entry names serde-pyobject, which is "
            "not a member of pyo3-build-config-family"
        )
    ]


def test_a_copied_makefile_variable_that_has_drifted_does_not_match(
    tmp_path: pathlib.Path,
) -> None:
    """The canonical requirement is read from the manifest, the copy from the file."""
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    (tmp_path / "Makefile").write_text(
        "PYO3_VERSION := 0.29.0\n\ncheck:\n\t@echo $(PYO3_VERSION)\n",
        encoding="utf-8",
    )

    found = derived_inputs(
        tmp_path,
        {
            "derived_inputs": [
                {
                    "id": "pyo3-version",
                    "comparator": "satisfies",
                    "canonical": {"manifest": "Cargo.toml", "package": "pyo3"},
                    "selectors": [
                        {
                            "kind": "makefile-variable",
                            "path": "Makefile",
                            "variable": "PYO3_VERSION",
                        }
                    ],
                    "rationale": "one PyO3 line across the build",
                    "review_trigger": "review when PyO3 0.29 is adopted",
                }
            ]
        },
        [_declaration("pyo3", "0.28.3")],
        [],
    )

    selector = found[0]["selectors"][0]
    assert found[0]["canonical"]["status"] == RESOLVED
    assert selector["status"] == RESOLVED
    assert selector["value"] == "0.29.0"
    assert selector["matches"] is False


def test_a_workflow_expression_is_unresolved_not_matched(
    tmp_path: pathlib.Path,
) -> None:
    """A value the audit does not evaluate is indeterminate, never equal."""
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n  build:\n    steps:\n"
        "      - run: make check\n"
        "        env:\n"
        '          PYO3_VERSION: "${{ matrix.pyo3 }}"\n',
        encoding="utf-8",
    )

    found = derived_inputs(
        tmp_path,
        {
            "derived_inputs": [
                {
                    "id": "pyo3-version",
                    "comparator": "satisfies",
                    "canonical": {"manifest": "Cargo.toml", "package": "pyo3"},
                    "selectors": [
                        {
                            "kind": "workflow-env",
                            "path": ".github/workflows/ci.yml",
                            "variable": "PYO3_VERSION",
                            "tool": "make",
                        }
                    ],
                    "rationale": "one PyO3 line across the build",
                    "review_trigger": "review when PyO3 0.29 is adopted",
                }
            ]
        },
        [_declaration("pyo3", "0.28.3")],
        [],
    )

    selector = found[0]["selectors"][0]
    assert selector["status"] == "unresolved"
    assert selector["matches"] is None


def test_two_disagreeing_occurrences_are_ambiguous(
    tmp_path: pathlib.Path,
) -> None:
    """A selector with more than one distinct value has no single answer."""
    (tmp_path / "Makefile").write_text(
        "a:\n\techo pyo3==0.28.3\n\nb:\n\techo pyo3==0.29.0\n",
        encoding="utf-8",
    )
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")

    found = derived_inputs(
        tmp_path,
        {
            "derived_inputs": [
                {
                    "id": "pyo3-copy",
                    "comparator": "equal",
                    "canonical": {"manifest": "Cargo.toml", "package": "pyo3"},
                    "selectors": [
                        {
                            "kind": "makefile-recipe",
                            "path": "Makefile",
                            "tool": "pyo3",
                        }
                    ],
                    "rationale": "one PyO3 line across the build",
                    "review_trigger": "review when PyO3 0.29 is adopted",
                }
            ]
        },
        [_declaration("pyo3", "==0.28.3")],
        [],
    )

    assert found[0]["selectors"][0]["status"] == AMBIGUOUS
