"""Compare the version ranges a dependency declaration or contract states.

The dependency-family rule judges whether a repository has relaxed a documented
compatibility bound, and whether a recorded derived input still satisfies its
canonical pin. It cannot ask `packaging` (the project does not depend on it) or
a registry, so this module implements the small, well-specified subset those
judgements need: dotted release versions, Cargo caret requirements, and the
PEP 440 `<`, `<=`, `~=`, `==` and `!=` operators.

A version this module cannot parse, or an operator it does not implement, is
reported as unknown. Unknown is never treated as satisfied or as held: the
caller turns it into an indeterminate fact.
"""

from __future__ import annotations

import re
import typing as typ

_VERSION: typ.Final = re.compile(r"^v?(?P<release>\d+(?:\.\d+)*)(?:[-+].*)?$")
_CLAUSE: typ.Final = re.compile(
    r"^(?P<operator><=|>=|==|!=|~=|\^|<|>|=)\s*(?P<text>.+)$"
)

# The specification levels this module distinguishes. `unknown` is a fact, not
# an error: the caller reports it rather than guessing.
HELD: typ.Final = "held"
RELAXED: typ.Final = "relaxed"
OPEN: typ.Final = "open"
UNKNOWN: typ.Final = "unknown"


class UpperBound(typ.NamedTuple):
    """An upper limit expressed by a requirement."""

    version: tuple[int, ...]
    inclusive: bool
    raw: str


def parse_version(text: str) -> tuple[int, ...] | None:
    """Return the numeric release segments of *text*, or None when unusable.

    Pre-release and build suffixes are ignored: the compatibility questions
    here concern the release line, and a suffix would otherwise make two
    spellings of one line incomparable.

    Returns
    -------
    tuple[int, ...] | None
        The release segments, or None when *text* is not a dotted version.
    """
    match = _VERSION.match(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group("release").split("."))


def _padded(
    left: tuple[int, ...], right: tuple[int, ...]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Return two versions padded to a common length with zeroes."""
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)), right + (0,) * (width - len(right))


def compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """Return -1, 0 or 1 as *left* sorts before, with or after *right*."""
    padded_left, padded_right = _padded(left, right)
    if padded_left < padded_right:
        return -1
    if padded_left > padded_right:
        return 1
    return 0


def _caret_upper(parts: tuple[int, ...]) -> tuple[int, ...]:
    """Return the exclusive upper limit of Cargo's caret requirement."""
    major = parts[0]
    if major != 0:
        return (major + 1,)
    if len(parts) == 1:
        return (1,)
    minor = parts[1]
    if minor != 0 or len(parts) == 2:
        return (0, minor + 1)
    return (0, 0, parts[2] + 1)


def _tilde_upper(parts: tuple[int, ...]) -> tuple[int, ...]:
    """Return the exclusive upper limit of PEP 440's compatible release."""
    if len(parts) == 1:
        return (parts[0] + 1,)
    prefix = list(parts[:-1])
    prefix[-1] += 1
    return tuple(prefix)


def _bound_from_clause(clause: str, ecosystem: str) -> UpperBound | None:
    """Return the upper limit one requirement clause states, if any.

    Returns
    -------
    UpperBound | None
        The limit, or None when the clause states no upper limit or is not
        one this module understands.
    """
    text = clause.strip()
    if not text:
        return None
    match = _CLAUSE.match(text)
    if match is None:
        # A bare Cargo requirement is a caret requirement; a bare Python
        # requirement is not a valid specifier and states no limit.
        if ecosystem != "cargo":
            return None
        version = parse_version(text)
        return (
            None
            if version is None
            else UpperBound(_caret_upper(version), inclusive=False, raw=text)
        )
    operator = match.group("operator")
    version = parse_version(match.group("text"))
    if version is None:
        return None
    match operator:
        case "<":
            return UpperBound(version, inclusive=False, raw=text)
        case "<=":
            return UpperBound(version, inclusive=True, raw=text)
        case "~=":
            return UpperBound(_tilde_upper(version), inclusive=False, raw=text)
        case "^":
            return UpperBound(_caret_upper(version), inclusive=False, raw=text)
        case _:
            return None


def upper_bound(requirement: str, ecosystem: str) -> UpperBound | None:
    """Return the tightest upper limit *requirement* states, or None.

    Parameters
    ----------
    requirement:
        The declaration's requirement text.
    ecosystem:
        ``cargo`` or a Python ecosystem (``pip``/``uv``). Bare Cargo
        requirements are caret requirements; bare Python ones are not
        specifiers.

    Returns
    -------
    UpperBound | None
        The tightest limit, or None when the requirement states none.
    """
    bounds = [
        bound
        for clause in requirement.split(",")
        if (bound := _bound_from_clause(clause, ecosystem)) is not None
    ]
    if not bounds:
        return None
    return min(
        bounds,
        key=lambda bound: (
            bound.version,
            not bound.inclusive,
        ),
    )


def _clause_satisfied(
    clause: str, version: tuple[int, ...], ecosystem: str
) -> bool | None:
    """Return whether *version* satisfies one clause, or None when unknown."""
    text = clause.strip()
    if not text:
        return True
    match = _CLAUSE.match(text)
    if match is None:
        if ecosystem != "cargo":
            return None
        lower = parse_version(text)
        if lower is None:
            return None
        # `all` rather than `and`: both bounds compare against the same
        # version, and Pylint reads the pair as a chainable comparison.
        return all((
            compare(version, lower) >= 0,
            compare(version, _caret_upper(lower)) < 0,
        ))
    operator = match.group("operator")
    wanted_text = match.group("text").strip()
    if operator == "==" and wanted_text.endswith(".*"):
        prefix = parse_version(wanted_text[:-2])
        return (
            None
            if prefix is None
            else _padded(version, prefix)[0][: len(prefix)] == prefix
        )
    wanted = parse_version(wanted_text)
    if wanted is None:
        return None
    relation = compare(version, wanted)
    match operator:
        case "==" | "=":
            return relation == 0
        case "!=":
            return relation != 0
        case "<":
            return relation < 0
        case "<=":
            return relation <= 0
        case ">":
            return relation > 0
        case ">=":
            return relation >= 0
        case "~=":
            return all((relation >= 0, compare(version, _tilde_upper(wanted)) < 0))
        case "^":
            return all((relation >= 0, compare(version, _caret_upper(wanted)) < 0))
        case _:
            return None


def satisfies(requirement: str, version: str, ecosystem: str) -> bool | None:
    """Return whether *version* satisfies *requirement*, or None when unknown.

    A requirement is a conjunction: every clause must hold. Any clause this
    module cannot evaluate makes the whole answer unknown rather than false,
    so a selector is never reported as matching by accident.

    Returns
    -------
    bool | None
        True when every clause holds, False when one does not, and None when
        any clause or the version could not be evaluated.
    """
    wanted = parse_version(version)
    if wanted is None:
        return None
    outcomes = [
        _clause_satisfied(clause, wanted, ecosystem)
        for clause in requirement.split(",")
        if clause.strip()
    ]
    if not outcomes:
        return True
    if any(outcome is False for outcome in outcomes):
        return False
    if any(outcome is None for outcome in outcomes):
        return None
    return True


def lowest_version(requirement: str) -> str | None:
    """Return the lowest release a requirement admits, when it is knowable.

    This is used only to choose a representative version for Dependabot's
    update classification when no lock records one. It is evidence, not a
    resolution: an open requirement yields None.

    Returns
    -------
    str | None
        The dotted lower release, or None when the requirement admits none.
    """
    for clause in requirement.split(","):
        text = clause.strip()
        match = _CLAUSE.match(text)
        candidate = match.group("text") if match is not None else text
        if match is not None and match.group("operator") in {"<", "<=", "!="}:
            continue
        parsed = parse_version(candidate)
        if parsed is not None:
            return ".".join(str(part) for part in parsed)
    return None


def bound_status(recorded: str, current: str, ecosystem: str) -> str:
    """Return how *current* compares with the documented *recorded* bound.

    Returns
    -------
    str
        ``held`` when the current requirement is the same or tighter,
        ``relaxed`` when it admits a version the recorded bound excludes,
        ``open`` when it states no upper limit, and ``unknown`` when either
        requirement cannot be compared.
    """
    current_bound = upper_bound(current, ecosystem)
    if current_bound is None:
        return OPEN
    recorded_bound = upper_bound(recorded, ecosystem)
    if recorded_bound is None:
        return UNKNOWN
    relation = compare(current_bound.version, recorded_bound.version)
    if relation < 0:
        return HELD
    if relation > 0:
        return RELAXED
    # At the same version, an inclusive limit admits more than an exclusive
    # one. Anything else is the same limit.
    if current_bound.inclusive and not recorded_bound.inclusive:
        return RELAXED
    return HELD
