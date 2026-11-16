"""Resolve the names a test sets a cargo target from, inside one function.

A target directory is rarely written inline: `.env("CARGO_TARGET_DIR", dir)`
names a local, and what the local holds decides whether the build can ever be
warm. The reader inlines the `let` bindings of the function that builds the
command, so `dir.path()` reads as `tempfile::tempdir().unwrap().path()` and can
be classified.

Names resolve in one function and no further. A name bound to different
expressions there, a parameter, and a name the reader never sees bound all read
as unknown rather than as another function's binding of the same name.
"""

from __future__ import annotations

import re
import typing as typ

from .nested_cargo_text import split_top_level
from .rust_source import scan_balanced

if typ.TYPE_CHECKING:
    from .nested_cargo_source import RustSource

STRING: typ.Final = re.compile(r'"(?:[^"\\]|\\.)*"')
_FUNCTION: typ.Final = re.compile(r"\bfn\s+[A-Za-z_]\w*")
_FUNCTION_HEAD: typ.Final = re.compile(r"fn\s+\w+\s*(?:<[^{(]*>)?\s*\(")
_BODY_OR_END: typ.Final = re.compile(r"[{;]")
_LET: typ.Final = re.compile(
    r"\blet\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)\s*(?::[^=;]*)?=\s*(?P<rhs>[^;]*);"
)
_TEMPORARY_PARAMETER: typ.Final = re.compile(
    r"(?P<name>[A-Za-z_]\w*)\s*:\s*&?\s*(?:mut\s+)?(?:tempfile\s*::\s*)?TempDir\b"
)
_IDENTIFIER: typ.Final = re.compile(r"(?<![\w.:$])[a-z_]\w*\b(?!\s*[(:!])")
_EXPANSION_DEPTH: typ.Final = 3
# Markers for a name the reader cannot resolve: a name bound to different
# expressions, and a parameter of the function. They start with a capital so that
# the identifier pattern, which reads lower-case names, cannot expand them again.
_AMBIGUOUS: typ.Final = "<Ambiguous>"
_PARAMETER: typ.Final = "<Parameter>"
_PATTERN_NAME: typ.Final = re.compile(r"[A-Za-z_]\w*")
_NESTING: typ.Final = str.maketrans("{}[]", "()()")
_PATTERN_KEYWORDS: typ.Final = frozenset({"mut", "ref", "self"})


def _function_spans(masked: str) -> list[tuple[int, int]]:
    """Return the `(start, end)` of every function that has a body."""
    spans: list[tuple[int, int]] = []
    for match in _FUNCTION.finditer(masked):
        opening = _BODY_OR_END.search(masked, match.end())
        if opening is None or opening.group() != "{":
            continue
        end = scan_balanced(masked, opening.start())
        if end is not None:
            spans.append((match.start(), end))
    return spans


def function_text(source: RustSource, offset: int) -> str:
    """Return the text of the innermost function that contains *offset*.

    The signature is included, because a parameter typed `TempDir` is a binding
    too. A name bound in one function is not visible in another, so the reader
    resolves a command's target values inside the function that builds the
    command and not across the whole file. An offset outside any function (a
    `static` initialiser) is read against the whole file.

    Returns
    -------
    str
        The function's source text from its `fn` keyword to its closing brace.
    """
    containing = [
        span for span in _function_spans(source.masked) if span[0] <= offset < span[1]
    ]
    if not containing:
        return source.text
    start, end = max(containing)
    return source.text[start:end]


def _signature(text: str) -> str | None:
    """Return the parameter list of the function *text* starts with, if it is one."""
    head = _FUNCTION_HEAD.match(text)
    if head is None:
        return None
    close = scan_balanced(text, head.end() - 1)
    return None if close is None else text[head.end() : close - 1]


def _signature_parameters(text: str) -> set[str]:
    """Return every name the parameters of the function *text* starts with bind.

    A parameter is `pattern: Type`, and the pattern may destructure
    (`(target,): (&Path,)`, `Pair { target, .. }: Pair`), so every name the
    pattern binds counts. Braces and brackets nest as parentheses do, and
    `split_top_level` only tracks parentheses, so they are rewritten first.

    Returns
    -------
    set[str]
        The names, empty when *text* is not a function or takes none.
    """
    signature = _signature(text)
    if signature is None:
        return set()
    names: set[str] = set()
    for segment in split_top_level(signature.translate(_NESTING)):
        pattern = _pattern_of(segment)
        if pattern is not None:
            names.update(_PATTERN_NAME.findall(pattern))
    return names - _PATTERN_KEYWORDS


def _pattern_of(parameter: str) -> str | None:
    """Return the pattern of a `pattern: Type` parameter, or None if it has none.

    The separator is the first colon outside any nesting that is not half of a
    path's `::`, so a renamed field (`Pair { path: target }: Pair`) does not end
    the pattern early.

    Returns
    -------
    str | None
        The text before the separator, or None when there is no separator.
    """
    depth = 0
    index = 0
    while index < len(parameter):
        char = parameter[index]
        if parameter.startswith("::", index):
            index += 2
            continue
        depth += (char == "(") - (char == ")")
        if char == ":" and depth == 0:
            return parameter[:index]
        index += 1
    return None


def local_bindings(text: str) -> dict[str, str]:
    """Return the expression each local name is bound to in *text*.

    The reader does not track which binding is live, so a name bound to
    different expressions is answered with an ambiguous binding, which no
    pattern matches, and the value reads `unknown` instead of a guess in either
    direction. A name bound to the same expression everywhere is unambiguous.
    A `TempDir` parameter is bound to the type, so that `dir.path()` reads as
    temporary; any other parameter holds what the caller passed, which the
    reader does not see, so it is bound to the unknown, and an unrelated binding
    of the same name in another function is never read in its place.

    Returns
    -------
    dict[str, str]
        Right-hand-side source text by name.
    """
    seen: dict[str, set[str]] = {}
    for match in _LET.finditer(text):
        seen.setdefault(match["name"], set()).add(" ".join(match["rhs"].split()))
    temporary = {match["name"] for match in _TEMPORARY_PARAMETER.finditer(text)}
    for name in temporary:
        seen.setdefault(name, set()).add("tempfile::TempDir")
    for name in _signature_parameters(text) - temporary:
        seen.setdefault(name, set()).add(_PARAMETER)
    return {
        name: next(iter(rhs)) if len(rhs) == 1 else _AMBIGUOUS
        for name, rhs in seen.items()
    }


def expand(expression: str, bindings: dict[str, str]) -> str:
    """Replace each locally bound name in *expression* by what it was bound to.

    String literals are left alone, so a path component that happens to spell a
    variable name is not substituted.

    Returns
    -------
    str
        The expression with up to three levels of local bindings inlined.
    """

    def replace(chunk: re.Match[str]) -> str:
        """Return the bound expression for one identifier, parenthesised."""
        rhs = bindings.get(chunk.group())
        return f"({rhs})" if rhs is not None else chunk.group()

    for _ in range(_EXPANSION_DEPTH):
        pieces = re.split(f"({STRING.pattern})", expression)
        expanded = "".join(
            piece if index % 2 else _IDENTIFIER.sub(replace, piece)
            for index, piece in enumerate(pieces)
        )
        if expanded == expression:
            break
        expression = expanded
    return expression
