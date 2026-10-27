"""Read a workflow step's `if:` for what it lets through.

Two steps in a workflow are asked about their conditions, and they are asked
opposite questions of the same syntax. The *fetch* step must run on every run,
because a fetch that sometimes skips warms nothing on the runs it skips. The
*test* step must not run after a failed fetch, because a test that runs anyway
starts against a cold cache and takes the package-cache lock to fill it.

GitHub's own rule is the reason the two questions differ. A step whose `if:`
names no status function gets an implicit `success()` check prepended, so it
waits for every earlier step whatever else the condition says. A step whose
`if:` *does* name one replaces that guard entirely: `success() && runner.os ==
'Linux'` still waits for the fetch, while `success() || github.ref ==
'refs/heads/main'` runs on `main` after a failed one.

Reading a condition for the second question therefore means evaluating it as
if the fetch had failed. That evaluation is three-valued: operands the reader
can see — the status functions — settle, operands it cannot see — `github.ref`,
`inputs.*` — leave the answer unknown. Three-valued logic then spreads that
unknownness only as far as it should: `success() && X` is false whatever `X`
is, while `success() || X` cannot be settled either way.
"""

from __future__ import annotations

import re
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# A whole `if:` value wrapped in GitHub's expression syntax, keeping the inner
# text so a bare `success()` and a wrapped `${{ success() }}` read alike.
_EXPRESSION_WRAPPER: typ.Final = re.compile(r"^\$\{\{(.*)\}\}$", re.DOTALL)
# Any call to one of GitHub's status functions, compound expressions included.
_STATUS_CALL: typ.Final = re.compile(r"\b(?:success|failure|cancelled|always)\s*\(")
# A status function call with nothing inside it, e.g. `success()`.
_STATUS_ATOM: typ.Final = re.compile(r"(success|failure|cancelled|always)\s*\(\s*\)")
# What each status function is worth once an earlier step has failed.
# `cancelled()` is the one that does not follow from the fetch's outcome:
# whether the run is being cancelled is a fact the reader does not have.
_FAILED_RUN_STATUS: typ.Final[dict[str, bool | None]] = {
    "success": False,
    "failure": True,
    "always": True,
    "cancelled": None,
}


def _condition_body(condition: str) -> str:
    """Return a step's `if:` expression with GitHub's `${{ }}` wrapper removed.

    GitHub accepts an expression either bare or wrapped, so `success()` and
    `${{ success() }}` are the same condition. Reading them differently would
    make the verdict depend on which spelling the author happened to use.

    Returns
    -------
    str
        The expression itself, without surrounding whitespace.
    """
    stripped = condition.strip()
    wrapped = _EXPRESSION_WRAPPER.match(stripped)
    return wrapped.group(1).strip() if wrapped is not None else stripped


def condition_proves_order(step: cabc.Mapping[str, object]) -> bool:
    """Return whether a fetch step's `if:` leaves it running on every run.

    A step guarded by `runner.os == 'Linux'` skips entirely elsewhere, and the
    test step that follows then starts against a cold cache. GitHub's own
    default is `success()`, so an explicit `success()` is the one spelling
    that still proves the order; anything else the reader cannot evaluate.

    This is the *fetch* step's question. The test step asks a different one —
    whether it runs only after success — which `runs_only_after_success`
    answers.

    Returns
    -------
    bool
        True when the step carries no `if:`, or carries exactly `success()`.
    """
    condition = step.get("if")
    if condition is None:
        return True
    return isinstance(condition, str) and _condition_body(condition) == "success()"


def runs_only_after_success(step: cabc.Mapping[str, object]) -> bool | None:
    """Return whether a step runs only when every earlier step succeeded.

    GitHub applies its `success()` default in two ways. A step with no `if:`
    gets it outright, and a step whose `if:` names no status function gets
    `success() && (…)`. A condition that *does* name one replaces that default
    entirely, and what it then allows depends on the expression: `always()` and
    `failure()` run the step after a failed fetch, while `success() && …` still
    rules it out.

    Returns
    -------
    bool | None
        True when the step cannot run after a failed fetch, so the fetch gates
        it. False when it can. None when the condition contains operands the
        reader does not evaluate — `github.ref`, for instance — and the answer
        therefore depends on the run.
    """
    condition = step.get("if")
    if condition is None:
        return True
    if not isinstance(condition, str):
        # A YAML scalar rather than an expression. The reader evaluates
        # expressions, so this is a condition it cannot answer for — not a
        # licence to assume the step is gated.
        return None
    body = _condition_body(condition)
    if _STATUS_CALL.search(body) is None:
        # Naming no status function means GitHub's implicit `success()` guard
        # still applies, so the step waits for every earlier step to succeed.
        return True
    # A status function is present, which replaces that implicit guard. The
    # step is gated only if the expression is false whenever the fetch failed.
    outcome = _evaluated_after_failure(body)
    return None if outcome is None else not outcome


def _evaluated_after_failure(body: str) -> bool | None:
    """Return a condition's value in every run where an earlier step failed.

    The question the rule needs answered is not "is this condition true", but
    "could this step run although the fetch before it failed". Evaluating the
    expression with `success()` held false answers exactly that. Operands the
    reader does not evaluate — `github.ref`, `inputs.*` — are unknown, and
    three-valued logic decides how far that uncertainty spreads:
    `success() && X` is false whatever `X` is, while `success() || X` cannot be
    settled.

    Returns
    -------
    bool | None
        The condition's value: True, False, or None when it depends on an
        operand the reader cannot see.
    """
    value, end = _or_expression(body, 0)
    # Anything left over is syntax this reader does not model, so the honest
    # answer is that it does not know rather than the value parsed so far.
    return value if not body[end:].strip() else None


def _or_expression(body: str, index: int) -> tuple[bool | None, int]:
    """Parse a `||` chain from *index*, returning its value and the next offset."""
    value, index = _and_expression(body, index)
    while True:
        index = _skip_space(body, index)
        if not body.startswith("||", index):
            return value, index
        right, index = _and_expression(body, _skip_space(body, index + 2))
        value = _or_value(left=value, right=right)


def _and_expression(body: str, index: int) -> tuple[bool | None, int]:
    """Parse a `&&` chain from *index*, returning its value and the next offset."""
    value, index = _negated_operand(body, index)
    while True:
        index = _skip_space(body, index)
        if not body.startswith("&&", index):
            return value, index
        right, index = _negated_operand(body, _skip_space(body, index + 2))
        value = _and_value(left=value, right=right)


def _or_value(*, left: bool | None, right: bool | None) -> bool | None:
    """Combine two values with `||` under three-valued logic.

    Returns
    -------
    bool | None
        True when either side is true. False when both are false. None when
        neither is true and at least one side is unknown, because the unknown
        could have been the true one.
    """
    if left is True or right is True:
        return True
    if left is False and right is False:
        return False
    return None


def _and_value(*, left: bool | None, right: bool | None) -> bool | None:
    """Combine two values with `&&` under three-valued logic.

    Returns
    -------
    bool | None
        False when either side is false. True when both are true. None when
        neither is false and at least one side is unknown.
    """
    if left is False or right is False:
        return False
    if left is True and right is True:
        return True
    return None


def _negated_operand(body: str, index: int) -> tuple[bool | None, int]:
    """Parse an optional `!` and the operand after it."""
    index = _skip_space(body, index)
    # `!=` belongs to the operand that follows, not to a negation.
    if body.startswith("!", index) and not body.startswith("!=", index):
        value, index = _negated_operand(body, index + 1)
        return (None if value is None else not value), index
    return _operand(body, index)


def _operand(body: str, index: int) -> tuple[bool | None, int]:
    """Parse one operand: a bracketed group, a status call, or an unknown term."""
    index = _skip_space(body, index)
    if index < len(body) and body[index] == "(":
        value, index = _or_expression(body, index + 1)
        index = _skip_space(body, index)
        closed = index < len(body) and body[index] == ")"
        return (value if closed else None), min(index + 1, len(body))
    if (status := _STATUS_ATOM.match(body, index)) is not None:
        return _FAILED_RUN_STATUS[status.group(1)], status.end()
    return None, _unknown_operand_end(body, index)


def _unknown_operand_end(body: str, index: int) -> int:
    """Return where the operand starting at *index* ends.

    An operand the reader does not evaluate still has to be stepped over, so it
    ends at the first `&&` or `||` that is not inside its own brackets.

    Returns
    -------
    int
        The offset just past the operand, which may be the end of the string.
    """
    depth = 0
    while index < len(body):
        char = body[index]
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return index
            depth -= 1
        elif depth == 0 and (
            body.startswith("&&", index) or body.startswith("||", index)
        ):
            return index
        index += 1
    return index


def _skip_space(body: str, index: int) -> int:
    """Return *index* advanced past any whitespace."""
    while index < len(body) and body[index].isspace():
        index += 1
    return index
