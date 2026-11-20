"""Read which statements and helper bodies add to one command.

A command's arguments are not always written beside its construction. They
follow it to the next `Command::new` or the next function item, and a command
bound to a name and handed to a helper is also read against that helper's
body. This module finds that window, resolves each handoff, and splices the
helper's statements in at the call site so a launch in either place ends the
arguments in the order they run.

A handoff the scan cannot attribute -- a helper defined twice, a call reached
through a path or method, a name a `use` item could import (a glob import names
none but could bring any in, and an import may name an inline module in the same
file as easily as another file, which the scan does not resolve), a helper whose
body names the caller's binding as well as its receiving parameter, a caller
binding that shadows the helper by `let` or `let ref`, by a parameter or by a
destructuring pattern (`let (configure,) = ..`, `let Hook(configure) = ..`), a
helper name bound by a parameter pattern (`fn run((configure,): ..)`,
`fn run(Callbacks { setup: configure }: ..)`), rebound by a closure's own
`|..|` list in any position (`|_: (), configure: fn(&mut Command)|`) or bound
by a `match` arm's pattern, a `const` or `static` item of that name, mutable
or not, in the caller's block or a block enclosing it (an item is in scope for
its whole block, so one declared after the call still shadows it, while a
`let` counts only before the call, and an item in a nested block that does not
hold the call shadows nothing), a parameter reassigned or shadowed -- is
declined rather than guessed, because a false positive would demand
configuration from a repository that needs none. A call written inside a
closure body is declined as well: the body runs when the closure is called, not
where it is written, so `let deferred = |_| configure(&mut command);` adds
nothing to the command until the closure is invoked, which the scan does not
follow. The same holds for a helper's own parameter: a call on it inside a
closure body (`let _deferred = || { target.arg("build"); };`) or inside a
`match` arm whose pattern binds the parameter's name
(`Ok(target) => target.arg("build")`) is not the caller's command, so the
helper is declined at the statement that holds it. A helper declared `async`
is declined as well, because its body runs only when the future is polled and
not where the call is written. A `let` binding is read even
when its constructor begins on the next line, because Rust ends the name at the
`=` rather than at the line, and the parameters read are those of the innermost
function whose body holds the call, so a function item declared inside the
caller is not mistaken for its scope. A `return` in a helper is kept where it is
written, and the statements after it are wrapped in a block of their own and
have the keyword blanked, because they run only when the return was skipped; a
call that is the return's own expression (`return target.arg("--help")`) runs
before control leaves, so the return statement itself stays unconditional and
only what follows it is guarded. A return moved after its block would end
every path instead, so the reader could not split the caller's runs there.
Guarding the tail rather than starting a new run at it is what keeps the
caller's own statements after the call on the returning path too. A spliced
fragment with no call on the parameter keeps its braces in the order it was
written, with the `else` between them, so the caller still sees which arm the
fragment belongs to rather than reading the arms as code that runs one after
the other. The reading is textual, and every boundary and brace is read from
the masked source, so a `;` or a `{` in a string moves nothing.

:mod:`concordat.rules.nested_cargo_shadows` answers the scope and shadow
questions a handoff raises -- which function body holds the call, and what else
the helper's name may denote there -- and
:mod:`concordat.rules.nested_cargo_arguments` decides what the statements this
module returns mean.
"""

from __future__ import annotations

import bisect
import re
import typing as typ

from .nested_cargo_receivers import LAUNCH, reaches_through
from .nested_cargo_shadows import (
    FUNCTION_BOUNDARY,
    FileIndex,
    enclosing_start,
    imported,
    match_arm_binds,
    shadowed,
)
from .nested_cargo_syntax import (
    CLOSURE_HEADER,
    _body_span,
    _parameter_at,
    inside_closure,
)

# A call behind `&&` or `||` does not run on every path to the launch.
SHORT_CIRCUIT: typ.Final = re.compile(r"&&|\|\|")
# A `return` ends the path it sits on, so a call after it is conditional.
_RETURN: typ.Final = re.compile(r"\breturn\b")
# A new command's arguments belong to it, not to the one before it.
COMMAND_NEW: typ.Final = re.compile(r"Command::new\(")
# The braces of a statement with no call on the command, the `else` (with the
# `if` that qualifies it) written between them, and any `return`. Keeping them
# in the order they were written is what tells a spliced reader which arm a
# later statement belongs to, and where a path ends.
_BRACES_AND_ARMS: typ.Final = re.compile(r"\belse\s+if\b|\belse\b|\breturn\b|[{}]")
# A binding may name the constructor through a path (`std::process::Command`),
# so the qualified path that follows `=` is part of the binding, not text
# between it and the command.
BINDING: typ.Final = re.compile(
    r"let[ \t]+(?:mut[ \t]+)?(?P<name>\w+)[ \t]*(?::[^=;]+)?=\s*"
    r"(?:[A-Za-z_]\w*[ \t]*::[ \t]*)*$"
)
# An `async fn`'s body runs when its future is polled, not where the call is
# written. The other qualifiers may follow `async` in either order
# (`async unsafe fn`, `async extern "C" fn`), so they are read as a run.
_ASYNC: typ.Final = re.compile(r"\basync\s+(?:(?:unsafe|extern\s*\"[^\"]*\")\s+)*$")


def _argument_position(before: str | None) -> int | None:
    """Return the zero-based position of the bound command in a call.

    *before* is the text of the arguments that precede the command. A segment
    with a bracket, a block or a closure can hide a top-level comma, so its
    comma count is not trusted and the position is declined.

    Returns
    -------
    int | None
        The position, or None when the arguments before the command make it
        uncertain.
    """
    if before is None:
        return 0
    if any(char in before for char in "[{|<"):
        return None
    return before.count(",") + 1


class Window(typ.NamedTuple):
    """A command's statements in original and masked form, offset for offset.

    Attributes
    ----------
    text:
        The statements as written, so a flag inside a string is still readable.
    masked:
        The same statements with comments and literal bodies blanked, so a
        brace or semicolon in a literal neither opens a block nor ends a
        statement.
    """

    text: str
    masked: str


def _identifier(pattern: str) -> str:
    """Return *pattern* anchored so it cannot begin inside a longer word."""
    return rf"(?<![\w]){pattern}"


def _closure_shadows(statement: str, name: str, own: re.Pattern[str]) -> bool:
    """Report whether a closure header before the call binds *name*.

    A closure may name the command parameter in any position
    (`|_: (), target: &mut Command| target.arg(..)`), not only first, so the
    whole header is read. The closure's own arguments are not the caller's
    command, so a later call inside it must not be spliced into the caller's
    window.

    Returns
    -------
    bool
        True when a `|..|` header before the call names the parameter.
    """
    found = own.search(statement)
    limit = len(statement) if found is None else found.start()
    return any(
        re.search(rf"\b{re.escape(name)}\b", header["params"]) is not None
        for header in CLOSURE_HEADER.finditer(statement[:limit])
    )


def _hidden_use(
    masked: str,
    statement: str,
    own: re.Pattern[str],
    start: int,
    parameter: str,
    offset: int,
) -> bool:
    """Report whether a use of the parameter cannot be attributed to the call.

    A call written inside a closure body runs only when the closure is called,
    even when the header names no parameter (`|| { target.arg("build") }`), and
    a call inside a match arm whose pattern binds the parameter's name reaches
    the arm's value rather than the caller's command. Neither is the caller's
    work, so the statement that holds such a use ends the read.

    Returns
    -------
    bool
        True when a use of the parameter is neither the caller's nor on a path
        the caller reaches.
    """
    return any(
        inside_closure(masked, start, offset + use.start())
        or match_arm_binds(masked, parameter, start, offset + use.start())
        for use in own.finditer(statement)
    )


def _statements(masked: str, start: int, end: int) -> typ.Iterator[tuple[int, str]]:
    """Yield the statements between *start* and *end* with their offsets.

    A statement ends at a `;` at the helper body's own depth. A `;` inside
    brackets belongs to the expression that holds it, and a method chain may
    pass a block argument that holds statements of its own
    (`target.arg("build").arg({ let flag = "--quiet"; flag }).status()`), so
    splitting at every `;` would cut the chain and lose the call on the
    command.

    Yields
    ------
    tuple[int, str]
        The statement's offset in *masked* and its text.
    """
    depth = 0
    opening = start
    for index in range(start, end):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        elif char == ";" and depth == 0:
            yield opening, masked[opening:index]
            opening = index + 1
    if masked[opening:end].strip():
        yield opening, masked[opening:end]


def _receiver_statements(
    text: str, masked: str, span: tuple[int, int], parameter: str, receiver: str
) -> Window:
    """Return the body statements that add arguments to *parameter*.

    A statement that calls the parameter is that command's argument, wherever
    the call sits (`if enabled { command.arg("build") }` included). Statement
    boundaries come from the masked text, so a `;` inside a string does not
    split a statement, while each statement is also kept as written so a flag
    in a string stays readable. The parameter's name is replaced by the
    caller's binding in both forms, because the probe consumer reads the
    helper's statements as part of the caller's window.

    Every statement's braces are kept, in place, because a later statement's
    block depth decides whether its probe flag is unconditional; a statement
    that follows the closing brace of a block keeps that brace in front of it.
    A later declaration of the parameter's name, a reassignment of it, or
    another command built in the helper, means the name no longer denotes the
    caller's command, so nothing after that statement is read. That declines a
    helper the scan cannot attribute, which can miss compiler work rather than
    invent it.

    A flag behind a short circuit
    (`query && command.arg("--version").get_args().next().is_some()`) is not on
    every path, so a statement with a `&&` or `||` before the call keeps a
    block open around the call; the caller's depth is closed again at the end,
    because the helper has returned by then. A closure parameter that names the
    command parameter (`|_: (), target: &mut Command| ..`) shadows it wherever
    in the header it sits, so nothing after that statement is read.

    A call written inside a closure body runs only when the closure is called,
    even when the header names no parameter (`|| { target.arg("build") }`), and
    a call inside a match arm whose pattern binds the parameter's name reaches
    the arm's value rather than the caller's command. Neither is the caller's
    work, so the body is declined from the statement that holds the call.

    A `return` ends the helper's path there, and the statements after it run
    only when the return was skipped. They are therefore kept conditional, and
    the keyword is blanked in the masked form so the caller's reader does not
    start a new run at them: the caller's own statements after the call run on
    both paths and stay in the run they belong to, which is what keeps a launch
    after the call on the returning path's argument list.

    Rewriting the parameter to the caller's binding is only sound while that
    name means nothing else in the helper. A helper that receives the command
    as `target` and names another of its own parameters `cmd` would, once
    spliced into a caller whose binding is `cmd`, read that other value's calls
    as the command's. The whole body is therefore declined when it mentions the
    caller's binding at all, since the splice cannot tell the two apart.

    Returns
    -------
    Window
        The statements that call the parameter, semicolon-separated, with the
        braces of the statements between them retained.
    """
    start, end = span
    collides = re.compile(rf"\b{re.escape(receiver)}\b")
    if receiver != parameter and collides.search(masked, start, end) is not None:
        return Window("", "")
    name = _identifier(re.escape(parameter))
    own = re.compile(rf"{name}\s*\.")
    shadow = re.compile(
        rf"\bfor\s+(?:mut\s+)?{name}\b"
        rf"|\blet\b[^=;]*\b{name}\b"
        rf"|{name}\s*=(?!=)"
        rf"|\|\s*(?:mut\s+)?{name}\s*(?:\||:)"
    )
    pieces: list[str] = []
    masked_pieces: list[str] = []
    for offset, statement in _statements(masked, start, end):
        if (
            _hidden_use(masked, statement, own, start, parameter, offset)
            or COMMAND_NEW.search(statement)
            or shadow.search(statement)
            or _closure_shadows(statement, parameter, own)
        ):
            break
        piece, masked_piece = _receiver_piece(
            text, statement, own, parameter, receiver, offset
        )
        pieces.append(piece)
        masked_pieces.append(masked_piece)
    if not pieces:
        return Window("", "")
    pieces, guarded = _returning_tail(pieces, masked_pieces)
    # The read may stop inside a block the helper still holds open (a
    # `Command::new` in the block ends it), so the window is closed before it
    # is spliced, and the caller's own depth resumes after the call.
    unclosed = sum(piece.count("{") - piece.count("}") for piece in guarded)
    closers = "}" * max(unclosed, 0)
    return Window(";\n".join([*pieces, closers]), ";\n".join([*guarded, closers]))


def _blanked_return(masked: str) -> str:
    """Return *masked* with the `return` keyword replaced by spaces.

    The blanked text keeps its length, so an offset taken from it still names
    the same character in the text as written.

    Returns
    -------
    str
        The text with every `return` blanked.
    """
    return _RETURN.sub(lambda found: " " * len(found[0]), masked)


def _returning_tail(
    pieces: list[str], masked_pieces: list[str]
) -> tuple[list[str], list[str]]:
    """Make a helper's statements after a `return` conditional for the caller.

    A `return` ends the helper's path where it is written, and the statements
    after it run only when the return was skipped. Left as they are, the masked
    form would make the caller's reader start a new run at them and lose the
    caller's own continuation from the returning path. Blanking the keyword in
    the masked form (same length, so the two forms still index each other) and
    wrapping only the statements after it in one block keeps them conditional,
    while the return statement itself runs whenever control reaches it: a call
    that is the return's own expression executes before control leaves, so it
    is not made conditional by the return that follows it. The caller's
    statements after the call stay in the run they belong to. The wrapper
    braces are added to both forms at the same offsets, so a literal in a
    guarded statement is still read from the text it was written in. A
    top-level `return` is guarded the same way; its tail is unreachable, so
    reading it as a path costs nothing.

    Returns
    -------
    tuple[list[str], list[str]]
        The statements as written and masked, with the returning path's tail in
        a block of its own.
    """
    for index, piece in enumerate(masked_pieces):
        if _RETURN.search(piece) is None:
            continue
        tail = [_blanked_return(rest) for rest in masked_pieces[index + 1 :]]
        if not tail:
            return [*pieces[: index + 1]], [
                *masked_pieces[:index],
                _blanked_return(piece),
            ]
        text_tail = list(pieces[index + 1 :])
        # The guard ends at the helper's last statement, so a launch earlier on
        # the returning path would sit in a block the guard's opening brace
        # changes the identity of; the caller's continuation after the call
        # would then be lost to that launch's path. Such a helper keeps the
        # reader's own block boundary and only has the keyword blanked.
        if not any(LAUNCH.search(before) for before in masked_pieces[: index + 1]):
            text_tail[0] = "{" + text_tail[0]
            tail[0] = "{" + tail[0]
            text_tail[-1] += "}"
            tail[-1] += "}"
        return [
            *pieces[: index + 1],
            *text_tail,
        ], [*masked_pieces[:index], _blanked_return(piece), *tail]
    return pieces, masked_pieces


def _receiver_piece(
    text: str,
    statement: str,
    own: re.Pattern[str],
    parameter: str,
    receiver: str,
    offset: int,
) -> tuple[str, str]:
    """Return one helper statement as written and masked, for the caller.

    A statement that calls the parameter is kept with its argument, rewritten to
    the caller's binding. Every use in the statement is rewritten, because a
    statement can read the parameter once and then add to it
    (`if target.get_program() != "x" { target.arg("build") }`). Any other
    statement keeps only its braces, because they move the block depth a later
    statement is judged at. A call behind `&&` or `||` may not run, so it is
    wrapped in a block of its own. A call after a `return` in the same
    statement is wrapped too, because the return ends the helper's path there;
    a call that is the return's own expression is not, because it runs before
    control leaves.

    Returns
    -------
    tuple[str, str]
        The statement as written and its masked twin, both with the braces that
        place it in the caller's depth.
    """
    found = own.search(statement)
    if found is None:
        # The braces place the statement in the caller's depth. They are kept
        # in the order they were written, with the `else` (and any `if` that
        # qualifies it) beside them, because a `else` written between them is
        # what pairs the arms of a choice; dropping it makes the arms read as
        # code that runs one after the other. A `return` is kept where it is
        # written, because it ends the helper's path there: after the block it
        # would end every path, and the caller would read the block's code as
        # unconditional.
        marker = "".join(token[0] for token in _BRACES_AND_ARMS.finditer(statement))
        return marker, marker
    piece = text[offset : offset + len(statement)]
    masked_piece = statement
    if receiver != parameter:
        # The uses are found in the masked statement and spliced into both
        # forms at the same offsets, so the two stay the same length and the
        # reader can still index one by the other.
        for use in reversed(list(own.finditer(masked_piece))):
            piece = f"{piece[: use.start()]}{receiver}.{piece[use.end() :]}"
            masked_piece = (
                f"{masked_piece[: use.start()]}{receiver}.{masked_piece[use.end() :]}"
            )
    if SHORT_CIRCUIT.search(masked_piece, 0, found.start()) is not None:
        return "{" + piece + "}", "{" + masked_piece + "}"
    returned = _RETURN.search(masked_piece, 0, found.start())
    between = "" if returned is None else masked_piece[returned.end() : found.start()]
    if between.strip():
        # A `return` before the call ends the helper's path, so the flag is
        # given only to the caller's path that reaches it. A call that is the
        # return's own expression runs before control leaves, so it stays
        # unconditional.
        return "{" + piece + "}", "{" + masked_piece + "}"
    return piece, masked_piece


def _callee_arguments(
    text: str,
    masked: str,
    closers: dict[int, int],
    call: re.Match[str],
    receiver: str,
    base: int,
    index: FileIndex,
) -> Window | None:
    """Return the statements a call hands to its helper's bound parameter.

    The helper must be defined exactly once in the file: with two definitions
    the caller's lexical scope is unknown, so no argument is read from either.
    A local binding of the helper's name before the call shadows it, so the
    call is declined rather than resolved by name alone; so is a name a `use`
    item could import, because the call then reaches another file and a local
    namesake is not what it runs. An `async fn` is declined as well: its body
    runs when the future is polled, not where the call is written, so its
    statements are not the call site's. The definition's text still stops at
    the next function item, so a helper at the end of the file cannot run into
    unrelated code.

    *base* is the file offset of the window *call* was matched in, used to find
    the caller's scope for the shadowing check.

    Returns
    -------
    Window | None
        The statements on the parameter that receives the command, rewritten to
        *receiver*, or None when the helper is undefined, duplicated, shadowed
        or shaped uncertainly.
    """
    if shadowed(masked, call["callee"], base + call.start(), index):
        return None
    if imported(masked, call["callee"]):
        return None
    definitions = index.definitions.get(call["callee"], [])
    if len(definitions) != 1:
        return None
    definition = definitions[0]
    if _ASYNC.search(masked, 0, definition.start()) is not None:
        return None
    position = _argument_position(call["before"])
    if position is None:
        return None
    found = _parameter_at(masked, definition.end(), position)
    if found is None:
        return None
    parameter, close_at = found
    span = _body_span(masked, closers, close_at + 1)
    if span is None:
        return None
    later = bisect.bisect_right(index.boundaries, definition.end())
    limit = index.boundaries[later] if later < len(index.boundaries) else len(masked)
    start, end = span
    end = min(end, limit)
    if end <= start:
        return None
    return _receiver_statements(text, masked, (start, end), parameter, receiver)


def _callee_bodies(
    text: str,
    masked: str,
    closers: dict[int, int],
    window: str,
    name: str,
    base: int,
    index: FileIndex,
) -> list[tuple[int, Window]]:
    """Return the helper statements each call adds, with the call's end offset.

    `let mut command = Command::new(cargo); add_build(&mut command)` adds its
    arguments in `add_build`, so those arguments are read from that function's
    body. Only statements on the parameter that receives the command count, so
    another command built in the helper does not contribute. A callee defined
    in another file is not seen, which errs towards not demanding
    configuration, and a call written as a path (`support::configure(..)`) or a
    method (`self.configure(..)`) names something else, so it is not read as
    this file's free function.

    Returns
    -------
    list[tuple[int, Window]]
        The offset in *window* just past each call, with the statements it adds;
        empty when no helper is read.
    """
    # Rust allows whitespace around `::` and `.`, so the regex can only rule
    # out a separator that touches the name; :func:`reaches_through` finds one
    # that sits a few spaces before it.
    passed = re.compile(
        rf"(?<![.:\w])(?P<callee>\w+)\s*\(\s*(?:(?P<before>[^()]*),\s*)?"
        rf"(?<![.:\w])(?:&(?:mut\s+)?)?{re.escape(name)}\s*[,)]"
    )
    bodies: list[tuple[int, Window]] = []
    for call in passed.finditer(window):
        if reaches_through(window, call.start()):
            continue
        # A call inside a closure body runs when the closure is called, not
        # where it is written, so the helper's statements are not the call
        # site's. `let deferred = |_| configure(&mut command);` adds nothing to
        # the command until `deferred` runs, which the scan does not follow.
        at = base + call.start()
        if inside_closure(masked, enclosing_start(masked, at, index), at):
            continue
        body = _callee_arguments(text, masked, closers, call, name, base, index)
        if body is not None:
            bodies.append((call.end(), body))
    return bodies


def command_window(
    text: str,
    masked: str,
    closers: dict[int, int],
    start: int,
    index: FileIndex,
) -> Window:
    """Return the statements of the command built at *start*, up to a likely end.

    A command's arguments follow its construction, so the window runs to the
    next `Command::new` (another command) or the next function item, whichever
    comes first. Both boundaries are found in the masked source, so a
    `Command::new(` inside a string is data and does not end the window early.
    A command bound to a name and handed to a function defined once in the same
    file is also read against that function's body, because the callee adds the
    arguments. Those statements are inserted at the call site, so a launch in
    either the helper or the caller ends the arguments in the order they run. A
    window that ends early can miss an argument, which errs towards not
    demanding configuration.

    Returns
    -------
    Window
        The statements from the spawn to the end of the window, in original and
        masked form, with each callee body spliced in at its call.
    """
    tail = masked[start + 1 :]
    ends = [len(tail)]
    ends.extend(
        found.start()
        for found in (COMMAND_NEW.search(tail), FUNCTION_BOUNDARY.search(tail))
        if found is not None
    )
    length = 1 + min(ends)
    window = text[start : start + length]
    masked_window = masked[start : start + length]
    bound = BINDING.search(text[max(0, start - 120) : start])
    if bound is None:
        return Window(window, masked_window)
    pieces: list[str] = []
    masked_pieces: list[str] = []
    previous = 0
    for at, body in _callee_bodies(
        text, masked, closers, masked_window, bound["name"], start, index
    ):
        pieces.extend((
            window[previous:at],
            _spliced(body.text, masked_window, at),
        ))
        masked_pieces.extend((
            masked_window[previous:at],
            _spliced(body.masked, masked_window, at),
        ))
        previous = at
    pieces.append(window[previous:])
    masked_pieces.append(masked_window[previous:])
    return Window("".join(pieces), "".join(masked_pieces))


def _spliced(body: str, masked_window: str, end: int) -> str:
    """Return a helper body to insert where the call ending at *end* runs.

    A call behind `&&` or `||` may not run at all, so its helper's statements
    are wrapped in a block of their own: a flag the helper adds there does not
    sit on every path to the caller's launch.

    Returns
    -------
    str
        The body, wrapped in braces when the call is short-circuited.
    """
    statement = masked_window.rfind(";", 0, end) + 1
    if SHORT_CIRCUIT.search(masked_window, statement, end) is None:
        return f";\n{body};"
    return f";\n{{{body}}};"


def _bound_name(text: str, start: int) -> str | None:
    """Return the name the command built at *start* is bound to, if any."""
    bound = BINDING.search(text[max(0, start - 120) : start])
    return None if bound is None else bound["name"]
