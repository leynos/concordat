"""Find the scopes, patterns and items that hide a helper's name at a call site.

The handoff reader splices a helper's body in at the call, which is only right
when the name written there is the free function. The readers here answer what
else the name may denote: a parameter of the enclosing function or of a closure,
a `let` or loop variable, a destructuring pattern, a `const` or `static` item, a
`use` import, or a `match` arm's pattern. Any of them means the call reaches
something the scan cannot read, so the handoff is declined rather than guessed.
A binding's scope is read as Rust scopes it: a `let` binds from the end of its
statement, and an `if let` or `while let` only inside the block it guards, so a
call in an initializer or after a guard still reaches the free function.

A `match` arm's pattern binds every name it mentions, so
`match callback { configure => configure(..) }` calls the bound value; the scope
ends with the arm's body, and it reaches the arm's guard too, because the
pattern is in scope there (`configure if configure(..) => ..`). A loop's
pattern binds inside the loop's body, and the iterator before it may itself be
a block (`for configure in { hooks } { .. }`), which is the expression rather
than the body. The readers here locate the arms and the blocks that
hold a call, because both questions are answered by the same brace walk: which
blocks enclose the call, and which of them is a match.

Blocks are identified by the offset of their opening brace rather than by their
depth, so two sibling blocks at the same depth are told apart. A sibling block
that has closed does not enclose the call, which is what keeps its pattern or
item out of the answer.

A binding counts only in the scope that holds the call, so the file's function
items are indexed once (:class:`FileIndex`): the innermost function whose body
still holds the call is that scope, and a function item declared inside the
caller is not mistaken for it. A function item's name may stand on the line
after its `fn` (`fn` then `run(..)`), which is the same item and the same
scope. The same index matches a `const` or `static` to the block that declares
it.
"""

from __future__ import annotations

import bisect
import re
import typing as typ

from .nested_cargo_syntax import (
    _binding_pattern,
    _closure_headers,
    _split_parameters,
    closure_end,
    parameter_names,
    pattern_binds,
    top_level_item,
)
from .rust_source import scan_balanced

# An arm arrow, its guard's `if`, and the brackets that hold a pattern
# together. A pattern's own comma (`(configure, other) =>`) is inside its
# parentheses, so only a comma at the arm's own level ends one. The guard is
# what makes the pattern's bindings visible to the call written in it.
_ARM_TOKENS: typ.Final = re.compile(r"=>|\bif\b|[()\[\]{},]")

# A following item is another scope, so a name's arguments end there. Rust
# writes a function's qualifiers before `fn` in any of several orders, so
# `unsafe fn`, `default fn`, `extern "C" fn` and their combinations all begin a
# new scope just as a plain `fn` does. An item may begin on the line that ends
# the previous one (`} fn later(..)`), so the boundary is the word `fn` and not
# the start of a line; `fn` is a keyword, and the space before a name keeps it
# from matching `fn` as part of another word or a `fn(..)` type. Rust lets the
# name stand on the line after `fn`, so every separator is whitespace rather
# than a space or tab.
_FUNCTION_QUALIFIER: typ.Final = (
    r"(?:pub(?:\([^)]*\))?\s+|default\s+|const\s+|async\s+"
    r"|unsafe\s+|extern\s*\"[^\"]*\"\s+)*"
)
FUNCTION_BOUNDARY: typ.Final = re.compile(rf"\b{_FUNCTION_QUALIFIER}fn\s+\w+")


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
    body; an arrow deeper in one belongs to a match the body holds. A closing
    bracket that is not a block's brace does not end the arm: the binding is in
    scope for the whole expression, so `configure => noop() + configure(..)`
    still holds its arm after `noop()` closes. A guard is part of the arm's
    scope too: `configure if configure(..) => ..` calls the bound value, so a
    depth-zero `if` before the arrow measures the pattern before it and leaves
    the arm active through the guard. A segment that ends inside a pattern
    rather than a body holds no call, so it binds nothing.

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
    in_guard = False
    for found in _ARM_TOKENS.finditer(segment):
        mark = found.group()
        if mark in "([{":
            depth += 1
        elif mark in ")]}":
            depth = max(depth - 1, 0)
            if mark == "}" and depth == 0 and in_body:
                in_body = False
                pattern_start = found.end()
        elif depth:
            continue
        elif mark == "if":
            if in_body or in_guard:
                # The body's or the guard's own `if` decides nothing about the
                # arm; only the first one, before the arrow, opens the guard.
                continue
            # The pattern's bindings are in scope in the guard, so the arm is
            # active from here to the arrow even though the arrow is still to
            # come.
            binds = bound.search(segment[pattern_start : found.start()]) is not None
            in_guard = True
        elif mark == "=>":
            if not in_guard:
                binds = bound.search(segment[pattern_start : found.start()]) is not None
            in_guard = False
            in_body = True
        else:
            in_body = False
            in_guard = False
            pattern_start = found.end()
    return binds if (in_body or in_guard) else False


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


def _guarded_block(masked: str, after: int, start: int, at: int) -> int | None:
    """Return the block an `if let` or `while let` after *after* guards at *at*.

    The block is the first brace written outside the initializer's own
    brackets. A guard whose block has closed before the call does not hold it,
    so the call after the guard reaches whatever the name meant before it. An
    initializer that writes a block of its own (`match y { .. }`) makes the
    guard unreadable, and the call is then read as the free function.

    Returns
    -------
    int | None
        The guarded block's opening brace, or None when it does not hold *at*.
    """
    open_at = open_braces(masked, start, at)
    depth = 0
    index = after
    while index < at:
        char = masked[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        elif char == "{" and depth == 0:
            return index if index in open_at else None
        index += 1
    return None


def _statement_end_after(masked: str, after: int) -> int:
    """Return the offset just past the statement that continues after *after*.

    A `let` binds its name only once its initializer has been evaluated, so a
    call written in that initializer (`let configure = configure(command);`)
    still reaches whatever the name meant before the binding. The statement
    ends at the first `;` outside its own brackets, or at the brace that
    closes its block when the semicolon is omitted.

    Returns
    -------
    int
        The offset just past the statement, or the end of *masked*.
    """
    depth = 0
    index = after
    while index < len(masked):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return index + 1
            depth -= 1
        elif char == ";" and depth == 0:
            return index + 1
        index += 1
    return index


def _let_holds(masked: str, start: int, at: int, found: re.Match[str]) -> bool:
    """Report whether the `let` at *found* binds a name that is live at *at*.

    An `if let` or `while let` binds only inside the block it guards, so a
    call after that block reaches the free function again. An ordinary `let`
    binds from the end of its statement, so a call written in the initializer
    is not shadowed by the name it helps to compute.

    Returns
    -------
    bool
        True when the binding governs *at*.
    """
    before = masked[start : found.start()].rstrip()
    if re.search(r"(?<!\w)(?:if|while)$", before) is not None:
        return _guarded_block(masked, found.end(), start, at) is not None
    return at >= _statement_end_after(masked, found.end())


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

    A binding's scope can be narrower than its block: an `if let` binds only
    inside the block it guards, and an ordinary `let` binds only from the end
    of its statement. Both are asked of the binding itself rather than of the
    block, so the binding's own initializer and the code after a guard are
    read from the free function.

    Returns
    -------
    bool
        True when a live `let` binds *name*.
    """
    bound = re.compile(rf"\b{re.escape(name)}\b")
    binder = re.compile(rf"\blet\s+{_BINDING}{re.escape(name)}\b")
    for found in binder.finditer(masked, start, at):
        if _let_holds(masked, start, at, found) and _live(
            masked, start, _innermost(masked, start, found.start()), at
        ):
            return True
    pattern = re.compile(r"\blet\b([^=;]*?)\s*=")
    for found in pattern.finditer(masked, start, at):
        candidate = found[1]
        destructures = any(char in candidate for char in "([{")
        if not destructures or bound.search(candidate) is None:
            continue
        if _let_holds(masked, start, at, found) and _live(
            masked, start, _innermost(masked, start, found.start()), at
        ):
            return True
    return False


def _loop_body(masked: str, after: int, at: int) -> int | None:
    """Return the opening brace of the loop body that holds *at*.

    The body follows the loop's `in` and iterator expression, and that
    expression may hold a block of its own in a closure
    (`for x in items.map(|i| { i }) {}`). Brackets are tracked so such a block
    is not mistaken for the body; the body is the first brace written outside
    them. The expression may itself be a block (`for x in { hooks } { .. }`),
    so a brace written immediately after `in` is read as the expression's when
    another brace follows the block it opens; otherwise it is the body. A body
    that closes before the call leaves the call outside every loop, so a block
    written after the loop is not read as its body.

    Returns
    -------
    int | None
        The body's opening brace, or None when it does not hold *at*.
    """
    depth = 0
    index = after
    previous = after
    while index < len(masked) and index <= at:
        char = masked[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        elif char == "{" and depth == 0:
            close = scan_balanced(masked, index)
            if close is None:
                return None
            immediately = not masked[previous:index].strip()
            if immediately and _followed_by_a_brace(masked, close):
                # The block is the iterator expression, not the body: the
                # body's brace follows it.
                index = close + 1
                previous = index
                continue
            return index if close > at else None
        index += 1
    return None


def _followed_by_a_brace(masked: str, close: int) -> bool:
    """Report whether the next character after *close* is a brace.

    Returns
    -------
    bool
        True when only whitespace separates the closing brace at *close* from
        an opening brace.
    """
    index = close + 1
    while index < len(masked) and masked[index].isspace():
        index += 1
    return index < len(masked) and masked[index] == "{"


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
    """Report whether a closure header holding *at* binds *name*.

    A closure's own arguments are its values, not the caller's, so a call
    written inside one reaches whatever the header bound. The binding may sit
    anywhere in the list (`|_: (), configure: fn(&mut Command)|`), so every
    parameter's binding is read, and only the text before its type delimiter
    counts. The header binds only while its body holds the call: a call after
    a closure that has ended reaches the free function again.

    Returns
    -------
    bool
        True when a `|..|` header before *at* binds *name* and its body holds
        *at*.
    """
    bound = re.compile(rf"\b{re.escape(name)}\b")
    for header in _closure_headers(masked, start, at):
        if closure_end(masked, header.end()) <= at:
            continue
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
