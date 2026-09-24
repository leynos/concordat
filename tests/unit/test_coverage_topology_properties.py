"""Properties of the upload-guard reader over generated conditions.

The synthetic cases in `test_coverage_topology_readers` name the hazards
found so far. These properties walk the space around them: any ordering of
conjuncts, any spacing around the operators, operators hidden inside quoted
strings, and an unquoted disjunct at any position. The generator knows
which conditions it built to be guarded, and the reader must agree.
"""

from __future__ import annotations

import typing as typ

from hypothesis import given
from hypothesis import strategies as st

from tests.unit.coverage_credential_support import is_guarded_upload

# Each guard as its two operands and operator, so the spacing inside it can
# vary as well as the spacing around it.
_REF_GUARDS: typ.Final = (
    ("github.ref", "==", "'refs/heads/main'"),
    ("'refs/heads/main'", "==", "github.ref"),
)
_TOKEN_GUARDS: typ.Final = (("steps.token.outputs.available", "==", "'true'"),)
# Conjuncts that are neither guard, some hiding an operator in a quoted
# string, which must not split the condition. The retired `env` guard is
# among them: it needs the token bound in the step's `env`.
_OTHER_CONJUNCTS: typ.Final = (
    "env.CS_ACCESS_TOKEN != ''",
    "steps.other.outputs.available == 'true'",
    "github.actor != 'bot'",
    "github.actor != 'a || b'",
    "github.event.head_commit.message != 'x && y'",
    "github.repository == 'leynos/concordat'",
    "github.ref != 'refs/heads/main'",
)
_DISJUNCT: typ.Final = "github.event_name == 'workflow_dispatch'"

_spacing = st.sampled_from([" ", "  ", "\t", " \n "])


@st.composite
def _guard(draw: st.DrawFn, forms: tuple[tuple[str, str, str], ...]) -> str:
    """Draw one guard with arbitrary spacing around its operator.

    Returns
    -------
        The guard's text.
    """
    left, operator, right = draw(st.sampled_from(forms))
    return f"{left}{draw(_spacing)}{operator}{draw(_spacing)}{right}"


@st.composite
def conditions(draw: st.DrawFn) -> tuple[str, bool]:
    """Draw an upload condition and whether it was built to be guarded.

    Returns
    -------
        The condition text and the generator's own verdict.
    """
    has_ref = draw(st.booleans())
    has_token = draw(st.booleans())
    conjuncts = [
        *([draw(_guard(_REF_GUARDS))] if has_ref else []),
        *([draw(_guard(_TOKEN_GUARDS))] if has_token else []),
        *draw(st.lists(st.sampled_from(_OTHER_CONJUNCTS), max_size=3)),
    ]
    conjuncts = draw(st.permutations(conjuncts)) if conjuncts else [_DISJUNCT]
    operators = [
        f"{draw(_spacing)}&&{draw(_spacing)}" for _ in range(len(conjuncts) - 1)
    ]
    has_disjunct = draw(st.booleans())
    if has_disjunct:
        # A disjunct may land between any two conjuncts or at the end.
        position = draw(st.integers(0, len(operators)))
        disjoined = f"{draw(_spacing)}||{draw(_spacing)}{_DISJUNCT}"
        conjuncts[position] = f"{conjuncts[position]}{disjoined}"
    text = conjuncts[0] + "".join(
        operator + conjunct
        for operator, conjunct in zip(operators, conjuncts[1:], strict=True)
    )
    if draw(st.booleans()):
        text = f"${{{{ {text} }}}}"
    return text, has_ref and has_token and not has_disjunct


@given(conditions())
def test_the_reader_agrees_with_the_generator(case: tuple[str, bool]) -> None:
    """A condition is guarded exactly when both guards are whole conjuncts.

    Any unquoted `||` voids the guard, wherever it lands, because it binds
    loosest and makes every conjunct optional.
    """
    condition, is_guarded = case
    assert is_guarded_upload(condition, {"token"}) is is_guarded, condition
