"""Expand the variables a `makeutil` report records, the way Make would.

`makeutil parse` reports each assignment with its raw value and conditions but
substitutes nothing. A policy that has to read a recipe such as
`$(INTERROGATE) $(FLAGS) $(TARGETS)` needs the substituted text. This module
does the mechanical part for the envelope builders that need it: it keeps the
variables that have one unconditional value, substitutes them (and a few text
functions that build file names) until the text settles, and says whether any
reference survived. A reference that survives is an unresolved fact for the
caller to carry, never a guess.

`$$` is a shell dollar, not a reference. It stays as a placeholder in the
expanded text so a caller can tell shell substitution from a Make reference
that was left behind; :func:`shown` turns the placeholders back into `$`.
"""

from __future__ import annotations

import collections
import dataclasses
import posixpath
import re
import types
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .makefile_facts import MakeVariable

SHELL_DOLLAR: typ.Final = "\x00"
# Stands for a Make reference the expansion left; it lexes as a word of its own.
UNRESOLVED_MARK: typ.Final = "\x01"
# Follows the `$(` of an `if` call that has been judged and kept. A later pass,
# after substitution has rewritten what is inside it, must not judge it again.
JUDGED_MARK: typ.Final = "\x02"

# How many times a value may name another variable before the expansion gives
# up. A cycle never settles, so it ends as unresolved rather than looping.
EXPANSION_PASSES: typ.Final = 8

# Variables GNU Make defines itself. The Makefile under audit is the root one,
# so its own list is that file alone.
BUILTIN_VARIABLES: typ.Final = types.MappingProxyType({
    "MAKEFILE_LIST": "Makefile",
    "MAKE": "make",
    "CURDIR": ".",
})

_VARIABLE_REFERENCE: typ.Final = re.compile(
    r"\$(?:\(([A-Za-z_][A-Za-z0-9_]*)\)|\{([A-Za-z_][A-Za-z0-9_]*)\})"
)
# A plain reference: `$(NAME)`, `${NAME}` or the single-character `$X`. Anything
# fancier (a substitution reference, a name with odd characters) is not one.
_SIMPLE_REFERENCE: typ.Final = re.compile(
    r"\$(?:\((?P<paren>[A-Za-z_][A-Za-z0-9_]*)\)|\{(?P<brace>[A-Za-z_][A-Za-z0-9_]*)\}"
    r"|(?P<short>[A-Za-z0-9_]))"
)
_UNRESOLVED_REFERENCE: typ.Final = re.compile(r"\$[({]")
_IF_CALL: typ.Final = re.compile(r"\$\(if\s")
_ERROR_CALL: typ.Final = re.compile(r"\$\(error\s")
_PURE_FUNCTIONS: typ.Final = (
    "if", "or", "and", "not", "strip", "findstring", "filter", "filter-out",
    "subst", "patsubst", "word", "words", "firstword", "lastword", "sort",
    "dir", "notdir", "basename", "suffix", "join", "wordlist",
)  # fmt: skip
# The opening of a call to one of those: `$(if `, `${strip ` and so on.
_PURE_CALL_OPEN: typ.Final = re.compile(
    r"\$[({]\s*(?:" + "|".join(re.escape(name) for name in _PURE_FUNCTIONS) + r")\s"
)
_FUNCTION_CALL: typ.Final = re.compile(
    r"\$\((dir|notdir|lastword|firstword|abspath|realpath|strip)\s+([^()$]*)\)"
)


@dataclasses.dataclass(frozen=True, slots=True)
class Expansion:
    """A Make text after variable substitution, and whether it fully resolved."""

    text: str
    is_resolved: bool


def shown(text: str) -> str:
    """Return *text* with the placeholders turned back into what they stood for."""
    plain = text.replace(SHELL_DOLLAR, "$").replace(UNRESOLVED_MARK, "$")
    return plain.replace(JUDGED_MARK, "")


def joined(text: str) -> str:
    """Return *text* with backslash-newline continuations folded to spaces."""
    return re.sub(r"\\\n[ \t]*", " ", text)


def _is_plain(variable: MakeVariable, count: int) -> bool:
    """Return whether *variable* is assigned once, unconditionally, outside `define`."""
    conditions = variable.get("conditions", [])
    has_conditions = isinstance(conditions, list) and bool(conditions)
    return count == 1 and not variable.get("define_block", False) and not has_conditions


class Assignments(collections.UserDict[str, str]):
    """The variables with one plain value, and the names of every other assignment.

    *assigned* lists every variable the Makefile assigns, whether or not the
    expansion can substitute it. A variable named there but absent from the
    mapping is one the Makefile defines in a way the expansion cannot follow;
    a variable named nowhere comes from the environment or the command line.
    """

    assigned: frozenset[str]

    def __init__(
        self, values: cabc.Mapping[str, str], assigned: cabc.Iterable[str]
    ) -> None:
        """Hold the substitutable *values* and the *assigned* names."""
        super().__init__(values)
        self.assigned = frozenset(assigned)


def single_assignments(variables: cabc.Sequence[MakeVariable]) -> Assignments:
    r"""Return each variable that has exactly one plain assignment, and its value.

    A variable assigned twice, conditionally or in a `define` block has no one
    value the expansion can substitute, so it is left out and a reference to it
    stays unresolved. Make turns `\#` in a value into `#`, so that is undone
    here and in no recipe.

    Returns
    -------
    Assignments
        Variable name to its raw value, remembering every name assigned.
    """
    names = [str(variable.get("name", "")) for variable in variables]
    values = {
        name: joined(str(variable.get("raw_value", ""))).replace("\\#", "#")
        for name, variable in zip(names, variables, strict=True)
        if _is_plain(variable, names.count(name))
    }
    return Assignments(values, names)


def _dir(words: list[str]) -> list[str]:
    """Return each word's directory part, `./` when it has none."""
    return [word[: word.rfind("/") + 1] if "/" in word else "./" for word in words]


def _notdir(words: list[str]) -> list[str]:
    """Return each word without its directory part."""
    return [word.rsplit("/", 1)[-1] for word in words]


def _lastword(words: list[str]) -> list[str]:
    """Return the last word, or nothing."""
    return words[-1:]


def _firstword(words: list[str]) -> list[str]:
    """Return the first word, or nothing."""
    return words[:1]


def _normalised(words: list[str]) -> list[str]:
    """Return each word as a normalised relative path."""
    return [posixpath.normpath(word) for word in words]


_FUNCTIONS: typ.Final[dict[str, cabc.Callable[[list[str]], list[str]]]] = {
    "dir": _dir,
    "notdir": _notdir,
    "lastword": _lastword,
    "firstword": _firstword,
    "strip": list,
    "abspath": _normalised,
    "realpath": _normalised,
}


def _call_function(match: re.Match[str]) -> str:
    """Evaluate the Make text functions that build file names.

    `abspath` and `realpath` normalise and stay relative: the checkout is the
    Makefile's own directory, which is all those calls name in practice.

    Returns
    -------
    str
        The function's result, as Make would produce it for these arguments.
    """
    return " ".join(_FUNCTIONS[match.group(1)](match.group(2).split()))


def _close_of(text: str, start: int) -> int | None:
    """Return the index of the parenthesis closing the one at *start*, if any."""
    depth = 0
    for index in range(start, len(text)):
        match text[index]:
            case "(":
                depth += 1
            case ")":
                depth -= 1
                if depth == 0:
                    return index
            case _:
                pass
    return None


def _top_level_commas(body: str) -> list[int]:
    """Return the offsets of the commas in *body* that sit outside parentheses."""
    depth = 0
    found: list[int] = []
    for index, char in enumerate(body):
        match char:
            case "(":
                depth += 1
            case ")":
                depth -= 1
            case "," if depth == 0:
                found.append(index)
            case _:
                pass
    return found


def _is_error_call(text: str) -> bool:
    """Return whether *text* is one `$(error ...)` call and nothing else."""
    stripped = text.strip()
    return (
        _ERROR_CALL.match(stripped) is not None
        and _close_of(stripped, 1) == len(stripped) - 1
    )


def _reference_name(match: re.Match[str]) -> str:
    """Return the variable name a `$(NAME)`, `${NAME}` or `$X` reference names."""
    return match.group("paren") or match.group("brace") or match.group("short")


def _is_pure(
    condition: str, known: cabc.Mapping[str, str], assigned: cabc.Container[str]
) -> bool:
    """Return whether evaluating *condition* can only test text, never change it.

    `$(eval ...)`, `$(shell ...)`, `$(file ...)` and the like can redefine a
    variable the recipe goes on to use, so a guard whose condition calls one
    does not vanish. The condition is accepted only when every `$` in it opens a
    call to a pure text function or is a plain reference (`$(NAME)`, `$X`):
    a substitution reference, an odd variable name or a function not on the list
    is not provably pure. The known variables the condition names are looked
    through, since their values are expanded with it, and a variable the
    Makefile assigns but the expansion cannot follow, or a chain longer than the
    passes allow, is not provably pure. A variable the Makefile never assigns is
    data from the environment, such as `CARGO_BUILD_TARGET`. That is a stated
    assumption, not a proof: this reads the Makefile as committed, and a caller
    who puts Make code in the environment or on the command line can defeat any
    static reading of it.

    Returns
    -------
    bool
        True when the condition is provably free of side effects.
    """
    current = condition
    for _ in range(EXPANSION_PASSES):
        rest = _SIMPLE_REFERENCE.sub("", _PURE_CALL_OPEN.sub("", current))
        if "$" in rest:
            return False
        names = {
            _reference_name(match) for match in _SIMPLE_REFERENCE.finditer(current)
        }
        if any(name in assigned and name not in known for name in names):
            return False
        updated = _SIMPLE_REFERENCE.sub(
            lambda match: known.get(_reference_name(match), match.group(0)), current
        )
        if updated == current:
            return True
        current = updated
    return False


def _guard_close(
    text: str,
    match: re.Match[str],
    known: cabc.Mapping[str, str],
    assigned: cabc.Container[str],
) -> int | None:
    """Return where the `$(if` at *match* closes, if it is a droppable guard.

    Returns
    -------
    int | None
        The index of its closing parenthesis, or None when the call is not an
        error-only guard with a side-effect-free condition.
    """
    close = _close_of(text, match.start() + 1)
    if close is None:
        return None
    body = text[match.end() : close]
    commas = _top_level_commas(body)
    if len(commas) != 1:
        return None
    branch = body[commas[0] + 1 :]
    if _is_error_call(branch) and _is_pure(body[: commas[0]], known, assigned):
        return close
    return None


def _drop_error_guards(
    text: str, known: cabc.Mapping[str, str], assigned: cabc.Container[str]
) -> str:
    """Remove each `$(if COND,$(error ...))`, which expands to nothing or aborts.

    A guard whose only branch is `$(error ...)` either stops Make before any
    recipe runs or contributes no text, so for reading a recipe that does run it
    is empty whatever its condition says, provided the condition has no side
    effects. A guard with an else branch, with any other text in its branch, or
    with a condition that may change a variable is left for the caller to find
    unresolved.

    Returns
    -------
    str
        *text* without those guards.
    """
    position = 0
    while (match := _IF_CALL.search(text, position)) is not None:
        close = _guard_close(text, match, known, assigned)
        if close is None:
            keep = match.start() + 2
            text = text[:keep] + JUDGED_MARK + text[keep:]
            position = match.end() + 1
        else:
            text = text[: match.start()] + text[close + 1 :]
            position = match.start()
    return text


def expand(text: str, values: cabc.Mapping[str, str]) -> Expansion:
    """Substitute `$(NAME)` references until the text settles.

    Any reference left after the passes (a Make function this module does not
    evaluate, an unset or ambiguous variable, a cycle) marks the expansion
    unresolved.

    Returns
    -------
    Expansion
        The substituted text and whether every reference was resolved.
    """
    assigned = getattr(values, "assigned", frozenset())
    # A built-in the Makefile assigns itself is the Makefile's, not Make's.
    known = {
        name: value
        for name, value in {**BUILTIN_VARIABLES, **values}.items()
        if name in values or name not in assigned
    }

    def substitute(match: re.Match[str]) -> str:
        """Replace one ``$(NAME)`` reference, leaving unknown names untouched."""
        value = known.get(match.group(1) or match.group(2))
        # A `$$` in a value is a shell dollar once the reference is expanded.
        return match.group(0) if value is None else value.replace("$$", SHELL_DOLLAR)

    current = text.replace("$$", SHELL_DOLLAR)
    for _ in range(EXPANSION_PASSES):
        updated = _FUNCTION_CALL.sub(
            _call_function,
            _VARIABLE_REFERENCE.sub(
                substitute, _drop_error_guards(current, known, assigned)
            ),
        )
        if updated == current:
            break
        current = updated
    return Expansion(current, _UNRESOLVED_REFERENCE.search(current) is None)
