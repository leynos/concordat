"""Specify the Make variable expansion the envelope builders share."""

from __future__ import annotations

import typing as typ

import pytest

if typ.TYPE_CHECKING:
    from concordat.rules.makefile_facts import MakeVariable

from concordat.rules.makefile_expansion import (
    SHELL_DOLLAR,
    Assignments,
    expand,
    shown,
    single_assignments,
)


def _variable(
    name: str,
    value: str,
    *,
    conditions: list[object] | None = None,
    define: bool = False,
) -> MakeVariable:
    """Return a variable as `makeutil` reports it."""
    return typ.cast(
        "MakeVariable",
        {
            "name": name,
            "raw_value": value,
            "conditions": conditions or [],
            "define_block": define,
        },
    )


def test_a_single_plain_assignment_is_kept() -> None:
    """One unconditional assignment has one value to substitute."""
    values = single_assignments([_variable("A", "x")])

    assert values == {"A": "x"}


def test_ambiguous_assignments_are_left_out() -> None:
    """Twice assigned, conditional and `define` variables have no one value."""
    values = single_assignments([
        _variable("TWICE", "a"),
        _variable("TWICE", "b"),
        _variable("COND", "c", conditions=[{"kind": "ifeq"}]),
        _variable("DEF", "d", define=True),
    ])

    assert values == {}


def test_an_escaped_hash_in_a_value_is_a_hash() -> None:
    r"""Make reads `\#` in a variable's value as `#`."""
    values = single_assignments([_variable("A", "x\\#y")])

    assert values == {"A": "x#y"}


def test_nested_references_settle() -> None:
    """A value that names another variable resolves through it."""
    result = expand("$(A) end", {"A": "$(B) mid", "B": "start"})

    assert (result.text, result.is_resolved) == ("start mid end", True)


def test_an_unknown_reference_is_unresolved() -> None:
    """A reference with no one value stays, and is flagged."""
    result = expand("run $(MISSING)", {})

    assert (result.text, result.is_resolved) == ("run $(MISSING)", False)


def test_a_cycle_ends_unresolved() -> None:
    """A self-referencing variable never settles."""
    assert expand("$(A)", {"A": "$(A)"}).is_resolved is False


def test_a_shell_dollar_survives_as_a_placeholder() -> None:
    """`$$` is a shell dollar, kept apart from Make references."""
    result = expand("echo $$HOME", {})

    assert SHELL_DOLLAR in result.text
    assert shown(result.text) == "echo $HOME"
    assert result.is_resolved is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$(dir src/a.py)", "src/"),
        ("$(dir a.py)", "./"),
        ("$(notdir src/a.py)", "a.py"),
        ("$(lastword a b c)", "c"),
        ("$(firstword a b c)", "a"),
        ("$(abspath ./x/../y)", "y"),
        ("$(strip a  b)", "a b"),
    ],
)
def test_the_file_name_functions_are_evaluated(text: str, expected: str) -> None:
    """The text functions that build file names are evaluated."""
    assert expand(text, {}).text == expected


def test_an_error_only_guard_expands_to_nothing() -> None:
    """`$(if COND,$(error ...))` either aborts Make or adds no text."""
    guard = (
        "$(if $(or $(strip $(T)),$(findstring --x,$(F) $(G))),"
        "$(error use cargo directly, or unset T))"
    )
    result = expand("$(GUARD)RUSTFLAGS=-Dw cargo test", {"GUARD": guard})

    assert (result.text, result.is_resolved) == ("RUSTFLAGS=-Dw cargo test", True)


@pytest.mark.parametrize(
    "guard",
    [
        "$(if $(T),$(error no),yes)",
        "$(if $(T),echo hi)",
        "$(if $(T),$(error no) and more)",
        # `$(error)` with no text is a reference to a variable named `error`.
        "$(if yes,$(error))",
        # A condition that can redefine a variable the recipe then uses.
        "$(if $(eval CMD := cargo test),$(error stop))",
        "$(if $(shell touch x),$(error stop))",
        "$(if $(file >x,y),$(error stop))",
    ],
)
def test_a_guard_with_other_text_stays_unresolved(guard: str) -> None:
    """Only a guard whose sole branch is an error is empty; the rest is unread."""
    assert expand(f"{guard} cargo test", {}).is_resolved is False


def test_a_guard_condition_that_reaches_eval_through_a_variable_stays_unresolved() -> (
    None
):
    """A known variable in the condition is looked through for side effects."""
    values = {"SETUP": "$(eval CMD := cargo test)"}

    result = expand("$(if $(SETUP),$(error stop))go", values)

    assert result.is_resolved is False


def test_a_substitution_reference_is_looked_through() -> None:
    """`$(SETUP:x=y)` expands SETUP, so SETUP's eval is part of the condition."""
    values = Assignments({"SETUP": "$(eval CMD := cargo test)"}, ["SETUP", "CMD"])

    result = expand("$(if $(SETUP:x=y),$(error stop))go", values)

    assert result.is_resolved is False


def test_a_guard_naming_an_unfollowable_assignment_stays_unresolved() -> None:
    """A variable the Makefile assigns twice has no one value to prove pure."""
    values = Assignments({}, ["SETUP", "SETUP"])

    assert expand("$(if $(SETUP),$(error stop))go", values).is_resolved is False


def test_a_guard_condition_through_a_deep_chain_stays_unresolved() -> None:
    """A chain of variables longer than the passes cannot be proved pure."""
    chain = {f"V{index}": f"$(V{index + 1})" for index in range(20)}
    chain["V20"] = "$(eval X := 1)"

    result = expand("$(if $(V0),$(error stop))go", Assignments(chain, list(chain)))

    assert result.is_resolved is False


def test_a_guard_on_an_environment_variable_is_dropped() -> None:
    """A variable the Makefile never assigns is data, not code."""
    values = Assignments({"FLAGS": "-q"}, ["FLAGS"])

    result = expand("$(if $(CARGO_BUILD_TARGET)$(FLAGS),$(error stop))go", values)

    assert (result.text, result.is_resolved) == ("go", True)


def test_make_builtins_are_known() -> None:
    """`$(MAKE)` and `$(MAKEFILE_LIST)` name the root Makefile's own run."""
    assert expand("$(MAKE) x $(MAKEFILE_LIST)", {}).text == "make x Makefile"
