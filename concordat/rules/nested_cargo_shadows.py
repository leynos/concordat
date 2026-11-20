"""Find the scopes, patterns and items that hide a helper's name at a call site.

The handoff reader splices a helper's body in at the call, which is only right
when the name written there is the free function. The readers here answer what
else the name may denote: a parameter of the enclosing function or of a closure,
a `let` or loop variable, a destructuring pattern, a `const` or `static` item, a
`use` import, or a `match` arm's pattern. Any of them means the call reaches
something the scan cannot read, so the handoff is declined rather than guessed.

A `match` arm's pattern binds every name it mentions, so
`match callback { configure => configure(..) }` calls the bound value; the scope
ends with the arm's body. The readers here locate the arms and the blocks that
hold a call, because both questions are answered by the same brace walk: which
blocks enclose the call, and which of them is a match.

Blocks are identified by the offset of their opening brace rather than by their
depth, so two sibling blocks at the same depth are told apart. A sibling block
that has closed does not enclose the call, which is what keeps its pattern or
item out of the answer.

A binding counts only in the scope that holds the call, so the file's function
items are indexed once (:class:`FileIndex`): the innermost function whose body
still holds the call is that scope, and a function item declared inside the
caller is not mistaken for it. The same index matches a `const` or `static` to
the block that declares it.
"""

from __future__ import annotations

import bisect
import re
import typing as typ

from .nested_cargo_syntax import (
    CLOSURE_HEADER,
    _binding_pattern,
    _split_parameters,
    parameter_names,
    pattern_binds,
    top_level_item,
)
from .rust_source import scan_balanced

# An arm arrow and the brackets that hold a pattern together. A pattern's own
# comma (`(configure, other) =>`) is inside its parentheses, so only a comma at
# the arm's own level ends one.
_ARM_TOKENS: typ.Final = re.compile(r"=>|[()\[\]{},]")

# A following item is another scope, so a name's arguments end there. Rust
# writes a function's qualifiers before `fn` in any of several orders, so
# `unsafe fn`, `default fn`, `extern "C" fn` and their combinations all begin a
# new scope just as a plain `fn` does. An item may begin on the line that ends
# the previous one (`} fn later(..)`), so the boundary is the word `fn` and not
# the start of a line; `fn` is a keyword, and the space before a name keeps it
# from matching `fn` as part of another word or a `fn(..)` type.
_FUNCTION_QUALIFIER: typ.Final = (
    r"(?:pub(?:\([^)]*\))?[ \t]+|default[ \t]+|const[ \t]+|async[ \t]+"
    r"|unsafe[ \t]+|extern[ \t]*\"[^\"]*\"[ \t]+)*"
)
FUNCTION_BOUNDARY: typ.Final = re.compile(rf"\b{_FUNCTION_QUALIFIER}fn[ \t]+\w+")


class FileIndex(typ.NamedTuple):
    """The `fn` definitions and item boundaries of one masked source.

    Attributes
    ----------
    definitions:
        Each `fn` name with the matches that define it, in source order.
    boundaries:
        The start offset of every function item, in source order.
    """

    definitions: dict[str, list[re.Match[str]]]
    boundaries: list[int]


def file_index(masked: str) -> FileIndex:
    """Index the `fn` definitions and item boundaries of *masked* once.

    Each handoff in a file asks the same questions about the same text, so the
    names and boundaries are collected once rather than rescanned per call.

    Returns
    -------
    FileIndex
        The definitions by name and the ordered boundary offsets.
    """
    definitions: dict[str, list[re.Match[str]]] = {}
    for found in re.finditer(r"\bfn\s+(?P<name>\w+)", masked):
        definitions.setdefault(found["name"], []).append(found)
    return FileIndex(
        definitions, [found.start() for found in FUNCTION_BOUNDARY.finditer(masked)]
    )


def enclosing_start(masked: str, at: int, index: FileIndex) -> int:
    """Return the start of the innermost function whose body holds *at*.

    The latest `fn` before a call is not necessarily the caller's scope: a
    function declared inside another one (`fn unrelated() {}`) is an item of
    its own, so a call after it would otherwise be read against that inner
    item and miss the enclosing function's parameters. The body is the first
    `{` at bracket depth zero after the item starts, so the boundaries are
    walked back from the call and the first whose body has not closed is the
    one that holds it. A call outside every function keeps the whole file as
    its scope.

    Returns
    -------
    int
        The start offset of the enclosing function, or 0 when none holds *at*.
    """
    boundary = bisect.bisect_left(index.boundaries, at)
    for start in reversed(index.boundaries[:boundary]):
        item = top_level_item(masked, start)
        if item is None or item[1] != "{":
            continue
        close = scan_balanced(masked, item[0])
        if close is not None and close > at:
            return start
    return 0


def _arm_pattern_binds(segment: str, name: str) -> bool:
    """Report whether the arm holding the segment's end binds *name*.

    Only one arm is active at the call, so only the pattern of the arm whose
    body the segment ends in counts; an earlier sibling arm's binding ends
    with that arm. The pattern is the text between the arm's start and its
    `=>`, where an arm at the match's own level ends at a comma, and an arm
    whose body is a block also ends at the block's closing brace, because Rust
    lets that comma be left out. Only an arrow outside brackets starts an arm
    body; an arrow deeper in one belongs to a match the body holds. A segment
    that ends inside a pattern rather than a body holds no call, so it binds
    nothing.

    Returns
    -------
    bool
        True when the active arm's pattern mentions *name* as a whole word.
    """
    bound = re.compile(rf"\b{re.escape(name)}\b")
    depth = 0
    pattern_start = 0
    binds = False
    in_body = False
    for found in _ARM_TOKENS.finditer(segment):
        mark = found.group()
        if mark in "([{":
            depth += 1
        elif mark in ")]}":
            depth = max(depth - 1, 0)
            if depth == 0 and in_body:
                in_body = False
                pattern_start = found.end()
        elif depth:
            continue
        elif mark == "=>":
            binds = bound.search(segment[pattern_start : found.start()]) is not None
            in_body = True
        else:
            in_body = False
            pattern_start = found.end()
    return binds if in_body else False


def match_arm_binds(masked: str, name: str, start: int, at: int) -> bool:
    """Report whether the match arm holding *at* binds *name*.

    A pattern binds every name it mentions, so a call written with that name
    reaches the bound value rather than the free function:
    `match callback { configure => configure(&mut command) }` calls the
    callback. The blocks open at the call are read from the innermost out,
    because the arm's body may be a block of its own: the match brace is the
    one whose text holds the arm's `=>`.

    Returns
    -------
    bool
        True when an arm pattern enclosing *at* mentions *name*.
    """
    blocks = open_braces(masked, start, at)
    return any(
        _arm_pattern_binds(masked[brace + 1 : at], name) for brace in reversed(blocks)
    )


def open_braces(masked: str, base: int, at: int) -> list[int]:
    """Return the offsets of the braces open at *at*, scanning from *base*.

    The offsets identify the blocks rather than their depth, so two sibling
    blocks that sit at the same depth are told apart.

    Returns
    -------
    list[int]
        The opening braces still unclosed at *at*, outermost first.
    """
    openers: list[int] = []
    for index in range(base, min(at, len(masked))):
        char = masked[index]
        if char == "{":
            openers.append(index)
        elif char == "}" and openers:
            openers.pop()
    return openers


# `ref` and `mut` may each be present, in either combination, before the name
# a pattern binds; neither changes which name is bound.
_BINDING: typ.Final = r"(?:(?:ref\s+)?mut\s+|ref\s+)?"
_IN: typ.Final = re.compile(r"\bin\b")
_FOR: typ.Final = re.compile(r"\bfor\b")


def _live(masked: str, start: int, declarer: int | None, at: int) -> bool:
    """Report whether the block opened at *declarer* still holds *at*.

    A `let` or loop binding lives only in the block that declares it, so one
    written in a sibling block that has already closed is out of scope at the
    call. A binding at the file's top level has no declarer and is in scope
    throughout.

    Returns
    -------
    bool
        True when the declaring block encloses *at*, or there is none.
    """
    return declarer is None or declarer in open_braces(masked, start, at)


def _innermost(masked: str, start: int, at: int) -> int | None:
    """Return the opening brace of the innermost block open at *at*.

    Returns
    -------
    int | None
        The block's opening brace, or None when no block encloses *at*.
    """
    openers = open_braces(masked, start, at)
    return openers[-1] if openers else None


def _let_binds(masked: str, name: str, start: int, at: int) -> bool:
    """Report whether a live `let` between *start* and *at* binds *name*.

    A `let` binds its names from its statement to the end of the block that
    holds it, so one in a block that has closed before the call does not
    shadow the free function there. A tuple, slice, tuple-struct or struct
    pattern binds names without writing `let name`: `let (configure,) = ..`,
    `let Hook(configure) = ..` and `let Settings { configure } = ..` all
    shadow the free function of that name. Any bracket before the `=` makes
    the pattern a destructuring one; the pattern runs to the `=`, so the
    initializer is not part of it and a call on the right-hand side
    (`let x = configure(..)`) is not mistaken for a binding.

    Returns
    -------
    bool
        True when a live `let` binds *name*.
    """
    bound = re.compile(rf"\b{re.escape(name)}\b")
    binder = re.compile(rf"\blet\s+{_BINDING}{re.escape(name)}\b")
    for found in binder.finditer(masked, start, at):
        if _live(masked, start, _innermost(masked, start, found.start()), at):
            return True
    pattern = re.compile(r"\blet\b([^=;]*?)\s*=")
    for found in pattern.finditer(masked, start, at):
        candidate = found[1]
        destructures = any(char in candidate for char in "([{")
        if not destructures or bound.search(candidate) is None:
            continue
        if _live(masked, start, _innermost(masked, start, found.start()), at):
            return True
    return False


def _loop_body(masked: str, after: int, at: int) -> int | None:
    """Return the opening brace of the loop body that holds *at*.

    The body follows the loop's `in` and iterator expression, and that
    expression may hold a block of its own in a closure
    (`for x in items.map(|i| { i }) {}`). A block that has closed before the
    call is such a block rather than the body, so the first brace whose
    balanced close still holds *at* is the body. A brace after the call, or
    none that closes after it, leaves the call outside every loop body.

    Returns
    -------
    int | None
        The body's opening brace, or None when it does not hold *at*.
    """
    index = after
    while index < len(masked):
        brace = masked.find("{", index)
        if brace < 0 or brace > at:
            return None
        close = scan_balanced(masked, brace)
        if close is None:
            return None
        if close > at:
            return brace
        index = close + 1
    return None


def _for_binds(masked: str, name: str, start: int, at: int) -> bool:
    """Report whether a `for` loop whose body holds *at* binds *name*.

    A loop's pattern binds only inside the loop body, so `for (configure,) in
    hooks {}` shadows the free function there and nowhere else: a call after
    the loop reaches the `fn`. A plain pattern may carry `ref` or `mut`, and a
    destructuring pattern binds names without writing the plain form, so both
    are read.

    Returns
    -------
    bool
        True when a loop body holding *at* binds *name*.
    """
    bound = re.compile(rf"\b{re.escape(name)}\b")
    plain = re.compile(rf"\s*{_BINDING}{re.escape(name)}\b")
    for found in _FOR.finditer(masked, start, at):
        opening = found.end()
        inside = _IN.search(masked, opening, at)
        if inside is None:
            continue
        binding = masked[opening : inside.start()]
        destructures = any(char in binding for char in "([{")
        if destructures:
            binds = bound.search(binding) is not None
        else:
            binds = plain.match(binding) is not None
        if binds and _loop_body(masked, inside.end(), at) is not None:
            return True
    return False


def _closure_binds(masked: str, start: int, at: int, name: str) -> bool:
    """Report whether a closure header between *start* and *at* binds *name*.

    A closure's own arguments are its values, not the caller's, so a call
    written inside one reaches whatever the header bound. The binding may sit
    anywhere in the list (`|_: (), configure: fn(&mut Command)|`), so every
    parameter's binding is read, and only the text before its type delimiter
    counts.

    Returns
    -------
    bool
        True when a `|..|` header before *at* binds *name*.
    """
    bound = re.compile(rf"\b{re.escape(name)}\b")
    for header in CLOSURE_HEADER.finditer(masked, start, at):
        if any(
            bound.search(_binding_pattern(part)) is not None
            for part in _split_parameters(header["params"])
        ):
            return True
    return False


def _item_shadows(masked: str, name: str, at: int, index: FileIndex) -> bool:
    """Report whether a `const` or `static` of *name* is in scope at *at*.

    An item declaration is in scope for its whole block, so one written after
    the call shadows the free function there just as one written before it
    does: `configure(&mut command); const configure: fn(&mut Command) = noop;`
    calls the constant, not the `fn` beside it. A declaration in a block that
    encloses the call counts too, because that block's items are in scope
    throughout it: the call is declined rather than read from the free
    function. The block that declares the item is matched by the offset of its
    opening brace against the blocks open at the call, so a sibling block's
    item -- which is not in scope at the call -- does not shadow it.

    A `static mut` binding declares the name just as a plain `static` does, so
    the optional `mut` is read as part of the declaration rather than as a
    word that hides it.

    Returns
    -------
    bool
        True when an item of that name is in scope at *at*.
    """
    start = enclosing_start(masked, at, index)
    item = top_level_item(masked, start)
    if item is None or item[1] != "{":
        return False
    close = scan_balanced(masked, item[0])
    if close is None:
        return False
    at_blocks = open_braces(masked, item[0], at)
    declaration = re.compile(rf"\b(?:const|static)\s+(?:mut\s+)?\b{re.escape(name)}\b")
    for found in declaration.finditer(masked, item[0], close):
        declarer = open_braces(masked, item[0], found.start())
        if not declarer or declarer[-1] == item[0] or declarer[-1] in at_blocks:
            return True
    return False


def imported(masked: str, name: str) -> bool:
    """Report whether a `use` in the file could bring *name* in.

    A call to an imported function may resolve to another file, and it may
    resolve to an inline module in the same file (`use
    self::support::configure`). The scan does not resolve imports, so it cannot
    tell which definition the call reaches, and a same-file `fn` of that name is
    not certainly the one it does.
    The handoff is therefore declined rather than read from the local namesake;
    a false positive here would hide a real compile.

    A glob import (`use support::*`, `use support::{*}`) binds names without
    spelling any of them, so while one is present no name is certainly the
    file's own. Any `*` inside a `use` item is that glob, because a path
    segment cannot otherwise hold one. That reads as an import of every name,
    which further errs towards not demanding configuration.

    Returns
    -------
    bool
        True when a `use` item names *name* or imports a whole module.
    """
    if re.search(r"\buse\b[^;]*\*", masked) is not None:
        return True
    return re.search(rf"\buse\b[^;]*\b{re.escape(name)}\b", masked) is not None


def shadowed(masked: str, name: str, at: int, index: FileIndex) -> bool:
    """Report whether *name* may be a local binding at *at* rather than a `fn`.

    A parameter of the caller's own function, a closure's own parameter (in
    any position), a loop variable, `let`, `const` or `static` with the
    helper's name shadows the free function, so the call reaches something the
    scan cannot read. A `let` or a loop variable counts only before the call,
    because a value bound later is not the one the call reaches, while a
    `const` or `static` counts anywhere in the caller's block, because an item
    is in scope for the whole block. A `let` lives only in the block that
    declares it, so one in a sibling block that has already closed no longer
    shadows the name, and a loop's binding lives only in the loop body, so a
    call after the loop reaches the free function again. A `let` whose pattern
    destructures the name (`let (configure,) = ..`) binds it too, as does a
    loop's destructuring pattern (`for (configure,) in ..`), and so does a
    binding that borrows the value (`let ref configure = ..`, `|ref mut
    configure|`): the name still denotes the borrowed value rather than the
    free function. The enclosing function's parameter list is read because
    `fn run(configure: fn(&mut Command))` calls the argument, not the free
    function; a function item declared inside it is not the caller's scope, so
    the innermost enclosing body is found rather than the latest declaration.
    A parameter written as a pattern (`fn run((configure,): ..)`, `fn
    run(Settings { hook: configure }: ..)`) binds names the plain reader cannot
    see, so the handoff is declined rather than guessed.

    Returns
    -------
    bool
        True when a binding of that name is in scope at *at*.
    """
    at = min(at, len(masked))
    start = enclosing_start(masked, at, index)
    if _let_binds(masked, name, start, at) or _for_binds(masked, name, start, at):
        return True
    if match_arm_binds(masked, name, start, at):
        return True
    if _item_shadows(masked, name, at, index):
        return True
    if _closure_binds(masked, start, at, name):
        return True
    if pattern_binds(masked, start, name):
        return True
    return name in parameter_names(masked, start)
