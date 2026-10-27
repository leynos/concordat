"""Read a workflow step's `if:` as the two entry-point questions need it.

The two questions are asked of the same syntax. A *fetch* step proves the order
only when it runs on every run, so any condition that can skip it loses the
proof. A *test* step proves it when it cannot run after a failed fetch, which
turns on GitHub's implicit-`success()` rule: naming no status function keeps the
guard, and naming one replaces it with whatever the expression says.

The evaluator is three-valued because a condition can rest on a fact the reader
does not have. `success() && github.ref == 'refs/heads/main'` is false after a
failed fetch whatever the branch is; `success() || github.ref == …` is not.

Two spellings of the same syntax matter. GitHub's expression parser lowercases
a function name before looking it up, so `Always()` is `always()`. A
single-quoted literal is data: the `success()` inside one is text the condition
compares, and neither it nor any `)` or `||` beside it is syntax.
"""

from __future__ import annotations

import pytest

from concordat.rules.nested_cargo_conditions import (
    condition_proves_order,
    runs_only_after_success,
)

# A condition is given as the `if:` value GitHub would see.


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (None, True),
        ("success()", True),
        ("${{ success() }}", True),
        # Anything that can skip the fetch warms nothing on the runs it skips.
        ("runner.os == 'Linux'", False),
        ("always()", False),
        ("${{ success() && runner.os == 'Linux' }}", False),
        # GitHub matches expression function names without regard to case.
        ("Success()", True),
        ("${{ SUCCESS() }}", True),
        ("Always()", False),
    ],
    ids=[
        "no-condition",
        "bare-success",
        "wrapped-success",
        "platform-only",
        "always",
        "success-and-platform",
        "capitalised-success",
        "wrapped-upper-success",
        "capitalised-always",
    ],
)
def test_the_fetch_step_must_run_on_every_run(
    condition: str | None, *, expected: bool
) -> None:
    """A fetch guarded by anything but `success()` may skip and warm nothing."""
    step = {} if condition is None else {"if": condition}

    assert condition_proves_order(step) is expected


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (None, True),
        # Naming no status function leaves GitHub's implicit `success()` guard
        # in place, so the step still waits for the fetch.
        ("runner.os == 'Linux'", True),
        ("success()", True),
        ("${{ success() }}", True),
        ("success() && runner.os == 'Linux'", True),
        ("${{ success() && runner.os == 'Linux' }}", True),
        # True after a failed fetch, so the step can run against a cold cache.
        ("failure()", False),
        ("always()", False),
        ("failure() || success()", False),
        ("!success()", False),
        ("!(success())", False),
        ("success() || (failure() && always())", False),
        # A conjunction with `success()` is false however the rest resolves.
        ("success() && (failure() || github.ref == 'refs/heads/main')", True),
        # Depends on a fact the reader does not have.
        ("success() || github.ref == 'refs/heads/main'", None),
        ("cancelled()", None),
        ("!cancelled()", None),
        ("${{ success() || inputs.force }}", None),
        # GitHub matches expression function names without regard to case, so
        # `ALWAYS()` allows the step after a failed fetch just as the lower-case
        # spelling does, and `SUCCESS()` names a status function rather than
        # leaving GitHub's implicit guard in place.
        ("Always()", False),
        ("${{ ALWAYS() }}", False),
        ("Failure()", False),
        ("SUCCESS()", True),
        ("SUCCESS() || github.ref == 'refs/heads/main'", None),
        # A quoted literal is data, not syntax. `always()` spelled inside one
        # names no status function, so GitHub's implicit `success()` guard
        # stands and the step waits for the fetch.
        ("github.event.name == 'always()'", True),
        # Brackets and connectors inside a literal belong to the string. The
        # `|| failure()` here is a real disjunction, so the step can run after
        # a failed fetch even though `success()` is one of its operands.
        ("success() && github.event.name == 'a)b' || failure()", False),
        # `''` escapes a quote, so the literal does not end at the doubled
        # quote and the `||` after it is still part of the string.
        ("success() && github.event.name == 'it''s || b'", True),
        # Syntax this reader does not model is unknown, not the part it read.
        ("success() ${{ github.ref }}", None),
        # An unclosed literal is not an expression GitHub would run, so the
        # reader cannot say what the step would have done.
        ("github.event.name == 'it''s", None),
        # A YAML scalar rather than an expression is not something it can read.
        (True, None),
    ],
    ids=[
        "no-condition",
        "platform-only",
        "bare-success",
        "wrapped-success",
        "success-and-platform",
        "wrapped-success-and-platform",
        "failure",
        "always",
        "failure-or-success",
        "not-success",
        "bracketed-not-success",
        "success-or-what-follows",
        "success-and-unknown",
        "success-or-branch",
        "cancelled",
        "not-cancelled",
        "wrapped-success-or-input",
        "capitalised-always",
        "wrapped-upper-always",
        "capitalised-failure",
        "upper-success",
        "upper-success-or-branch",
        "status-call-in-literal",
        "bracket-and-connector-in-literal",
        "escaped-quote-in-literal",
        "trailing-syntax",
        "unclosed-literal",
        "non-string",
    ],
)
def test_a_test_step_is_gated_when_it_cannot_run_after_a_failure(
    condition: object, *, expected: bool | None
) -> None:
    """The step is gated iff the condition is false in every failed run."""
    step = {} if condition is None else {"if": condition}

    assert runs_only_after_success(step) is expected
