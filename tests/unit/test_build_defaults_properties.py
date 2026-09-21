"""Property tests for the build-defaults readers' documented contracts.

Two readers in this rule accept input a repository author writes freely, and
the example tests above pin the spellings the estate happens to use today. The
properties here are derived from what the modules promise rather than from how
they are written: that Cargo's two spellings of a value-taking flag mean the
same thing, and that a heading inside a fenced block is never a heading.
"""

from __future__ import annotations

import typing as typ

from hypothesis import given
from hypothesis import strategies as st

from concordat.rules.cargo_config import (
    VALUE_TAKING_FLAGS,
    classify_target_key,
    normalise_flags,
)
from concordat.rules.exception_docs import extract_headings

# A flag value, as it appears after its letter: `threads=8`, `link-arg=...`.
values = st.text(
    alphabet=st.characters(
        min_codepoint=33, max_codepoint=126, exclude_characters="\\'\""
    ),
    min_size=1,
    max_size=12,
)


@given(flag=st.sampled_from(sorted(VALUE_TAKING_FLAGS)), value=values)
def test_the_split_and_joined_spellings_agree(flag: str, value: str) -> None:
    """Cargo reads `["-C", "x"]` and `["-Cx"]` alike, so the reader must too.

    This is the contract the estate's three spellings of one linker flag rest
    on. If they disagreed for some value, a repository would comply under one
    spelling and fail under the other with the same configuration.
    """
    split = normalise_flags([flag, value])
    joined = normalise_flags([flag + value])
    assert split == joined, (
        f"{flag!r} + {value!r} normalizes to {split!r} split and {joined!r} joined"
    )


@given(text=st.text(max_size=40))
def test_normalising_a_string_matches_normalising_its_tokens(text: str) -> None:
    """Cargo splits a string-valued `rustflags` on whitespace and reads the rest.

    The string form is a route to every flag the array form carries, so a
    reader that treated them differently would let one of them past.
    """
    assert normalise_flags(text) == normalise_flags(text.split()), (
        f"the string and array forms of {text!r} must read alike"
    )


@given(
    before=st.lists(st.text(max_size=20), max_size=4),
    fenced=st.lists(st.text(max_size=20), max_size=4),
    level=st.integers(min_value=1, max_value=6),
    heading=st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=8,
    ),
)
def test_a_heading_inside_a_fence_is_never_extracted(
    before: list[str], fenced: list[str], level: int, heading: str
) -> None:
    """The fence state machine's whole purpose, over arbitrary surrounding text.

    Every one of these exception sections quotes Rust, whose attributes and
    doc comments begin with `#`, so a heading-shaped line inside a fence is
    the normal case rather than an odd one.
    """
    marker = "#" * level
    lines = [
        *[line for line in before if not line.lstrip().startswith(("#", "`", "~"))],
        "```text",
        *[line for line in fenced if not line.lstrip().startswith(("`", "~"))],
        f"{marker} {heading}",
        "```",
    ]
    found = [item.text for item in extract_headings(lines)]
    assert heading not in found, (
        f"a fenced heading must not be extracted, got {found!r} from {lines!r}"
    )


@given(
    key=st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40
    )
)
def test_an_unplaced_key_claims_nothing(key: str) -> None:
    """Unclassified means the reader made no claim, in either direction.

    The policy reads `linux` and `linux_only` from the fact rather than from
    `classified`, so an unplaced key that still asserted one of them would be
    a guess the rest of the rule would act on.
    """
    classification = classify_target_key(key)
    if classification.is_classified:
        return
    assert classification.is_linux is False, f"{key!r} claims Linux while unplaced"
    assert classification.is_linux_only is False, (
        f"{key!r} claims Linux-only while unplaced"
    )


@given(
    key=st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40
    )
)
def test_linux_only_implies_linux(key: str) -> None:
    """A source that applies only on Linux is a source that applies on Linux.

    BD-002 asks the first question to decide where the linker must be, and the
    second to decide where it must not be. A key satisfying one and not the
    other would be required to carry the flag and refused for carrying it.
    """
    classification = classify_target_key(key)
    if classification.is_linux_only:
        assert classification.is_linux, f"{key!r} is Linux-only but not Linux"


def test_the_value_taking_flags_are_the_ones_documented() -> None:
    """The sampled set is the module's, so a new flag joins these properties."""
    assert {"-C", "-Z"} <= VALUE_TAKING_FLAGS, (
        "the standard's own flags must be among the value-taking ones"
    )
    assert all(len(flag) == 2 for flag in typ.cast("set[str]", VALUE_TAKING_FLAGS)), (
        "only single-letter flags are rejoined with the token that follows"
    )
