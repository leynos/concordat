"""Specify how the rule manifest's revision list is rewritten and shipped."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import packages
from concordat.rules.whitaker_revisions import (
    REFS_KEY,
    replace_refs,
)


def test_replacing_the_list_touches_only_the_defaults() -> None:
    """The schema entry of the same key is prose and keeps its wording."""
    text = (
        "    properties:\n      " + REFS_KEY + ":\n        type: array\n"
        "  defaults:\n    " + REFS_KEY + ':\n      - "' + "a" * 40 + '"\n    other: 1\n'
    )

    updated = replace_refs(text, ["b" * 40, "c" * 40])

    assert updated == (
        text.split("  defaults:")[0]
        + "  defaults:\n    "
        + REFS_KEY
        + ':\n      - "'
        + "b" * 40
        + '"\n      - "'
        + "c" * 40
        + '"\n    other: 1\n'
    )


def test_replacing_a_list_that_is_absent_is_an_error() -> None:
    """A manifest with no list is not silently left unchanged."""
    with pytest.raises(OperationalRuleError, match="no defaults"):
        replace_refs("defaults: {}\n", ["a" * 40])


def test_replacing_refuses_a_short_revision() -> None:
    """Only full commit ids may enter the list the policy compares against."""
    text = f'    {REFS_KEY}:\n      - "{"a" * 40}"\n'

    with pytest.raises(OperationalRuleError, match="full lowercase"):
        replace_refs(text, ["abc123"])


@pytest.mark.parametrize(
    "ref",
    ["A" * 40, "g" * 40, "a" * 39, "a" * 41, f"{'a' * 39} ", ""],
    ids=[
        "uppercase",
        "non-hex",
        "short-by-one",
        "long-by-one",
        "trailing-space",
        "empty",
    ],
)
def test_replacing_refuses_a_malformed_revision_of_any_shape(ref: str) -> None:
    """Only 40 lowercase hexadecimal digits enter the list the policy compares."""
    text = f'    {REFS_KEY}:\n      - "{"a" * 40}"\n'

    with pytest.raises(OperationalRuleError, match="full lowercase"):
        replace_refs(text, [ref])


def test_the_shipped_list_leads_with_its_roots() -> None:
    """Every approved root is listed, and the first entry is the first root."""
    parameters = packages.rule_parameters(
        packages.rule_package_dir("whitaker-provisioning")
    )
    roots = typ.cast("list[str]", parameters["install_whitaker_roots"])
    refs = typ.cast("list[str]", parameters[REFS_KEY])

    assert refs[0] == roots[0]
    assert set(roots) <= set(refs)
    assert len(refs) == len(set(refs))
