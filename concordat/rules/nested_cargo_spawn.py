"""Find the Rust tests that spawn `cargo` or `rustc`, conservatively.

The nextest configuration rule (NC-004, NC-005) asks whether tests that start a
compiler are bounded. To ask it of the right repositories, this module scans
Rust test sources for a child process that compiles. The scan is textual and
deliberately narrow, because a false positive here would demand configuration
from a repository that needs none.

- Only test sources count: a file under a test directory (`tests/`,
  `test_support/`, `testing/`, `ui/`, or named `*_test(s).rs`), or the text of
  another `.rs` file inside an item marked `#[cfg(test)]`, for that item's
  braces only. The item's first `{` or `;` is read at bracket depth zero, so a
  `;` inside a signature (`[u8; 4]`, a parenthesised type) does not end it
  early. `build.rs` and `xtask` are not tests.
- A `cargo` spawn counts only with a compiling subcommand in the arguments of
  that same command (`build`, `check`, `test`, `run`, `package`, ...);
  `cargo metadata` or `cargo config` do no compilation. The arguments are read
  from the spawn to the next `Command::new` or the next function, and only up
  to the command's first launch, so a compiling argument elsewhere in the file
  (a production `cargo build`, say) or one added after the command has run
  does not turn a test's `cargo metadata` into compiler work. A `rustc` spawn
  counts unless one of its own arguments asks who the compiler is (`--version`,
  `-V`, `-vV`) or for a table that needs no compile (`--print` of `sysroot`,
  `cfg`, `target-list`, ...): those compile nothing. `--print=link-args` and
  `--print=native-static-libs` build the crate, so they still count.
- A command bound to a name and handed to a helper is read against the
  helper's body, so
  `let mut command = Command::new(cargo); add_build(&mut command)` sees
  `add_build`'s `arg("build")`. The call may span lines. Only statements on
  the parameter that receives the command count, so another command built in
  the helper does not contribute; those statements are read at the call site,
  and the parameter's name is taken to be the caller's binding. A launch in
  the helper or the caller therefore ends the arguments in the order they run,
  and a version flag added through a renamed parameter still probes the
  caller's command. The helper must be defined exactly once in the file and
  receive the command at a position whose parameter is a plain `name: Type`;
  a binding in the caller's function (its own parameters included) that
  shadows the helper's name, a call written as a path or method
  (`support::configure(..)`), or a later declaration of the parameter's name
  inside the helper, declines the handoff rather than guessing. Compiler work
  in a declined shape can be missed.
- Python tests that spawn `cargo` are outside the scan: pytest, not nextest,
  bounds them.
- A file must name `Command`, so `env!("CARGO")` read for a path alone is not a
  spawn.

A test binary is a top-level `tests/<stem>.rs`; its test functions are the
`fn` items of that file. Helpers under `src/` or `tests/support/` are judged
at repository level only.
"""

from __future__ import annotations

import bisect
import os
import pathlib
import re
import typing as typ
from itertools import starmap

from .nested_cargo_source import brace_pairs
from .nested_cargo_syntax import (
    _body_span,
    _parameter_at,
    parameter_names,
    top_level_item,
)
from .rust_source import mask_literals

TEST_PATH: typ.Final = re.compile(
    r"(^|/)(tests?|test_support|test-support|testing|ui)/|_tests?\.rs$"
)
SKIPPED_FILES: typ.Final = ("build.rs",)
SKIPPED_DIRECTORIES: typ.Final = frozenset({
    "node_modules",
    "target",
    "venv",
    "build",
    "dist",
    "vendor",
    "xtask",
})
CARGO_SPAWN: typ.Final = re.compile(
    r'Command::new\(\s*(?:"cargo"|&?cargo(?:_binary|_path|_exe)?\b|env!\("CARGO"\))'
    r"|env!\(\"CARGO\"\)|env::var(?:_os)?\(\s*\"CARGO\"\s*\)"
)
RUSTC_SPAWN: typ.Final = re.compile(
    r'Command::new\(\s*(?:"rustc"|&?\w*rustc\w*\b|env!\("RUSTC"\))'
)
# The `--print` requests that read a table or a path and exit without compiling
# an input. `link-args` and `native-static-libs` are deliberately absent: they
# build the crate first.
QUERY_PRINTS: typ.Final = (
    "sysroot|target-libdir|host-tuple|cfg|target-list|target-cpus|target-features"
    "|code-models|relocation-models|tls-models"
)
# A version or query flag given as a plain string literal: either the sole
# argument of `.arg("..")`, or an item of an array literal of string literals
# passed to `.args([..])`. Anything computed (`.arg(if c { "--version" } ..)`)
# is not a known value and is not a probe.
_LITERAL: typ.Final = r'"[^"]*"'
_SINGLE_FLAG: typ.Final = rf'"(?:--version|-V|-vV|-Vv|--print=(?:{QUERY_PRINTS}))"'
_FLAG: typ.Final = rf'{_SINGLE_FLAG}|"--print"\s*,\s*"(?:{QUERY_PRINTS})"'
RUSTC_PROBE: typ.Final = re.compile(
    rf"""\.arg\(\s*&?{_SINGLE_FLAG}\s*\)"""
    rf"""|\.args\(\s*&?\[\s*(?:{_LITERAL}\s*,\s*)*(?:{_FLAG})\s*"""
    rf"""(?:,\s*{_LITERAL}\s*)*,?\s*\]\s*\)"""
)
LAUNCH: typ.Final = re.compile(r"\.(?:status|output|spawn)\(")
COMMENT: typ.Final = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
SUBCOMMANDS: typ.Final = (
    "build|check|test|run|package|clippy|doc|rustc|install|publish|bench|miri"
    "|nextest|llvm-cov|rustdoc"
)
COMPILES: typ.Final = re.compile(
    rf"""\.args?\(\s*(?:&?\[\s*)?(?:["']\+\w+["']\s*,\s*)?["'](?:{SUBCOMMANDS})["']"""
)
COMMAND_NEW: typ.Final = re.compile(r"Command::new\(")
CFG_TEST: typ.Final = re.compile(r"#\[cfg\(test\)\]")
BINDING: typ.Final = re.compile(
    r"let[ \t]+(?:mut[ \t]+)?(?P<name>\w+)[ \t]*(?::[^=;]+)?=[ \t]*$"
)
FUNCTION_BOUNDARY: typ.Final = re.compile(
    r"\n[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:async[ \t]+)?(?:const[ \t]+)?fn[ \t]+\w+"
)
BINARY: typ.Final = re.compile(r"(^|/)tests/([^/]+)\.rs$")
FUNCTION: typ.Final = re.compile(r"fn\s+(\w+)\s*\(")
MAX_BYTES: typ.Final = 1_048_576


class Spawner(typ.TypedDict):
    """Store one test source that spawns a compiler.

    Attributes
    ----------
    path:
        The file's path relative to the checkout, in POSIX form.
    kind:
        `rustc` for a direct compiler spawn, else `cargo`.
    binary:
        The test binary's name for a top-level `tests/<stem>.rs`, else None.
    test_functions:
        The `fn` names in the file, which a nextest filter may name.
    """

    path: str
    kind: str
    binary: str | None
    test_functions: list[str]


def _marked_block(
    masked: str, marker: re.Match[str], closers: dict[int, int]
) -> tuple[int, int] | None:
    """Return the braced block a `#[cfg(test)]` marker applies to, if it has one.

    The item's first `{` or `;` is read at bracket depth zero, so a `;` inside
    a signature (`fn marked(len: [u8; 4])`) does not end the item early. A
    marker on a semicolon-only item (`mod tests;`, `use ..;`) scopes nothing.

    Returns
    -------
    tuple[int, int] | None
        The block as `(start, end)`, or None for a marked item with no braces
        (`mod tests;`, `use ..;`) or with unbalanced ones.
    """
    item = top_level_item(masked, marker.end())
    if item is None or item[1] != "{":
        return None
    end = closers.get(item[0])
    return None if end is None else (marker.start(), end)


def _test_spans(masked: str, closers: dict[int, int]) -> list[tuple[int, int]]:
    """Return the offset ranges of the items marked `#[cfg(test)]`.

    A marked item is test code only for its own braces, so production code
    after a closed `mod tests { .. }` is not. The structure is read from the
    masked source (same offsets, comments and literal contents blanked), so a
    marker in a comment or a brace in a string moves nothing.

    Returns
    -------
    list[tuple[int, int]]
        Each marked block as `(start, end)`.
    """
    blocks = (
        _marked_block(masked, found, closers) for found in CFG_TEST.finditer(masked)
    )
    return [block for block in blocks if block is not None]


def _is_test_position(
    relative: str, spans: list[tuple[int, int]], position: int
) -> bool:
    """Report whether a match at *position* sits in test code."""
    if TEST_PATH.search(relative):
        return True
    return any(start <= position < end for start, end in spans)


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


class _Window(typ.NamedTuple):
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


def _receiver_statements(
    text: str, masked: str, span: tuple[int, int], parameter: str, receiver: str
) -> _Window:
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
    A later declaration of the parameter's name, or another command built in
    the helper, means the name no longer denotes the caller's command, so
    nothing after that statement is read. That declines a helper the scan
    cannot attribute, which can miss compiler work rather than invent it.

    Returns
    -------
    _Window
        The statements that call the parameter, semicolon-separated, with the
        braces of the statements between them retained.
    """
    start, end = span
    own = re.compile(rf"\b{re.escape(parameter)}\s*\.")
    shadow = re.compile(
        rf"\bfor\s+(?:mut\s+)?{re.escape(parameter)}\b"
        rf"|\blet\b[^=;]*\b{re.escape(parameter)}\b"
        rf"|\|\s*(?:mut\s+)?{re.escape(parameter)}\s*(?:\||:)"
    )
    pieces: list[str] = []
    masked_pieces: list[str] = []
    offset = start
    for statement in masked[start:end].split(";"):
        if COMMAND_NEW.search(statement) or shadow.search(statement):
            break
        found = own.search(statement)
        if found is None:
            braces = "{" * statement.count("{") + "}" * statement.count("}")
            piece = masked_piece = braces
        else:
            piece = text[offset : offset + len(statement)]
            masked_piece = statement
            if receiver != parameter:
                piece = f"{piece[: found.start()]}{receiver}.{piece[found.end() :]}"
                masked_piece = (
                    f"{statement[: found.start()]}{receiver}.{statement[found.end() :]}"
                )
        pieces.append(piece)
        masked_pieces.append(masked_piece)
        offset += len(statement) + 1
    return _Window(";\n".join(pieces), ";\n".join(masked_pieces))


class _FileIndex(typ.NamedTuple):
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


def _file_index(masked: str) -> _FileIndex:
    """Index the `fn` definitions and item boundaries of *masked* once.

    Each handoff in a file asks the same questions about the same text, so the
    names and boundaries are collected once rather than rescanned per call.

    Returns
    -------
    _FileIndex
        The definitions by name and the ordered boundary offsets.
    """
    definitions: dict[str, list[re.Match[str]]] = {}
    for found in re.finditer(r"\bfn\s+(?P<name>\w+)", masked):
        definitions.setdefault(found["name"], []).append(found)
    return _FileIndex(
        definitions, [found.start() for found in FUNCTION_BOUNDARY.finditer(masked)]
    )


def _shadowed(masked: str, name: str, at: int, index: _FileIndex) -> bool:
    """Report whether *name* may be a local binding at *at* rather than a `fn`.

    A parameter of the caller's own function, a closure, loop variable, `let`,
    `const` or `static` with the helper's name shadows the free function, so
    the call reaches something the scan cannot read. The caller's parameter
    list is read because `fn run(configure: fn(&mut Command))` calls the
    argument, not the free function.

    Returns
    -------
    bool
        True when a binding of that name is in scope at *at*.
    """
    at = min(at, len(masked))
    boundary = bisect.bisect_left(index.boundaries, at) - 1
    start = index.boundaries[boundary] if boundary >= 0 else 0
    pattern = rf"\b{re.escape(name)}\b"
    binder = re.compile(
        rf"\b(?:let\s+(?:mut\s+)?|const\s+|static\s+|for\s+(?:mut\s+)?){pattern}"
        rf"|\|\s*(?:mut\s+)?{pattern}\s*(?:\||:)"
    )
    if binder.search(masked, start, at) is not None:
        return True
    return name in parameter_names(masked, start)


def _callee_arguments(
    text: str,
    masked: str,
    closers: dict[int, int],
    call: re.Match[str],
    receiver: str,
    base: int,
    index: _FileIndex,
) -> _Window | None:
    """Return the statements a call hands to its helper's bound parameter.

    The helper must be defined exactly once in the file: with two definitions
    the caller's lexical scope is unknown, so no argument is read from either.
    A local binding of the helper's name before the call shadows it, so the
    call is declined rather than resolved by name alone. The definition's text
    still stops at the next function item, so a helper at the end of the file
    cannot run into unrelated code.

    *base* is the file offset of the window *call* was matched in, used to find
    the caller's scope for the shadowing check.

    Returns
    -------
    _Window | None
        The statements on the parameter that receives the command, rewritten to
        *receiver*, or None when the helper is undefined, duplicated, shadowed
        or shaped uncertainly.
    """
    if _shadowed(masked, call["callee"], base + call.start(), index):
        return None
    definitions = index.definitions.get(call["callee"], [])
    if len(definitions) != 1:
        return None
    definition = definitions[0]
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
    index: _FileIndex,
) -> list[tuple[int, _Window]]:
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
    list[tuple[int, _Window]]
        The offset in *window* just past each call, with the statements it adds;
        empty when no helper is read.
    """
    passed = re.compile(
        rf"(?<![.:\w])(?P<callee>\w+)\s*\(\s*(?:(?P<before>[^()]*),\s*)?"
        rf"(?:&(?:mut\s+)?)?{re.escape(name)}\s*[,)]"
    )
    bodies: list[tuple[int, _Window]] = []
    for call in passed.finditer(window):
        body = _callee_arguments(text, masked, closers, call, name, base, index)
        if body is not None:
            bodies.append((call.end(), body))
    return bodies


def _command_window(
    text: str,
    masked: str,
    closers: dict[int, int],
    start: int,
    index: _FileIndex,
) -> _Window:
    """Return the statements of the command built at *start*, up to a likely end.

    A command's arguments follow its construction, so the window runs to the
    next `Command::new` (another command) or the next function item, whichever
    comes first. A command bound to a name and handed to a function defined
    once in the same file is also read against that function's body, because
    the callee adds the arguments. Those statements are inserted at the call
    site, so a launch in either the helper or the caller ends the arguments in
    the order they run. A window that ends early can miss an argument, which
    errs towards not demanding configuration.

    Returns
    -------
    _Window
        The statements from the spawn to the end of the window, in original and
        masked form, with each callee body spliced in at its call.
    """
    tail = text[start + 1 :]
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
        return _Window(window, masked_window)
    pieces: list[str] = []
    masked_pieces: list[str] = []
    previous = 0
    for at, body in _callee_bodies(
        text, masked, closers, masked_window, bound["name"], start, index
    ):
        pieces.extend((window[previous:at], f";\n{body.text};"))
        masked_pieces.extend((masked_window[previous:at], f";\n{body.masked};"))
        previous = at
    pieces.append(window[previous:])
    masked_pieces.append(masked_window[previous:])
    return _Window("".join(pieces), "".join(masked_pieces))


def _own_statements(
    text: str, start: int, window: _Window
) -> list[tuple[str, str, int]]:
    """Return the statements of *window* that can add arguments to the command.

    A command built in one chain owns only its first statement. A command bound
    to a name owns the statements that start with that name (`command.arg(..)`),
    plus the binding itself, up to its first launch (`.status()`, `.output()`,
    `.spawn()`); another command's `.arg(..)`, or an argument added when the
    command is reused after a launch, is not its argument.

    Statement boundaries and block depth come from the masked window, so a
    brace in a string literal does not make a later flag look conditional,
    while each statement is also returned as written so a flag in a string can
    still be read.

    Returns
    -------
    list[tuple[str, str, int]]
        Each statement as written, its masked twin, and the number of blocks
        still open where it starts (the window's own braces, counted from the
        command).
    """
    statements: list[tuple[str, str]] = []
    offset = 0
    for at, char in enumerate(window.masked):
        if char == ";":
            statements.append((
                _blank_comments(window.text[offset:at]),
                window.masked[offset:at],
            ))
            offset = at + 1
    statements.append((_blank_comments(window.text[offset:]), window.masked[offset:]))
    bound = BINDING.search(text[max(0, start - 120) : start])
    if bound is None:
        first, masked_first = statements[0]
        return [(first, masked_first, 0)]
    # A statement mentions the command when the name is followed by a method
    # call, wherever it sits (an `if` body included), so a launch in control
    # flow still ends the arguments.
    own = re.compile(rf"\b{re.escape(bound['name'])}\s*\.")
    found: list[tuple[str, str, int]] = []
    depth = 0
    for index, (statement, masked_statement) in enumerate(statements):
        if index == 0 or own.search(masked_statement):
            found.append((statement, masked_statement, depth))
            # An argument added after the command has run belongs to a reuse.
            if LAUNCH.search(masked_statement):
                break
        depth += masked_statement.count("{") - masked_statement.count("}")
    return found


def _blank_comments(text: str) -> str:
    """Return *text* with every comment blanked, keeping offsets and literals.

    A flag written in a comment is not an argument, but a flag in a string is,
    so comments become spaces of the same length rather than being removed.

    Returns
    -------
    str
        The text with the same length and line structure as *text*.
    """
    return COMMENT.sub(lambda found: re.sub(r"[^\n]", " ", found.group()), text)


def _is_probe(text: str, start: int, window: _Window) -> bool:
    """Report whether a `rustc` command only asks a question and compiles nothing.

    Only arguments added to this command count, so a `"--version"` in a comment,
    in an assertion after it or on another command is not its argument.

    Returns
    -------
    bool
        True when an argument of the command is a version or query flag.
    """
    return any(starmap(_unconditional_probe, _own_statements(text, start, window)))


def _unconditional_probe(statement: str, masked_statement: str, depth: int) -> bool:
    """Report whether a statement adds a probe flag on every path through it.

    A flag added inside a block (`if query_only { cmd.arg("--print=cfg") }`),
    whether in the same statement or in one that follows the opening brace, may
    not be there when the command runs, so it proves nothing. A block may also
    close before the flag in the same statement, so the braces before the flag
    are counted against the depth the statement starts at. The braces are
    counted in the masked statement, so one in a string is not a block.

    Returns
    -------
    bool
        True when the statement holds a probe flag outside any block.
    """
    found = RUSTC_PROBE.search(statement)
    if found is None:
        return False
    before = masked_statement[: found.start()]
    return depth + before.count("{") - before.count("}") == 0


def _compiles(
    text: str,
    masked: str,
    closers: dict[int, int],
    start: int,
    index: _FileIndex,
) -> bool:
    """Report whether a cargo command adds compiling work before it runs.

    Only statements that own the command count, and only up to its first
    launch: an argument added after the command has run belongs to a reuse,
    which compiles nothing. The statements include a helper's body, spliced in
    at the call site, so a launch in either place ends the arguments.

    Returns
    -------
    bool
        True when an argument before the first launch selects a compiling
        subcommand.
    """
    window = _command_window(text, masked, closers, start, index)
    return any(
        COMPILES.search(statement)
        for statement, _masked, _depth in _own_statements(text, start, window)
    )


def _spawn_kind(
    relative: str, text: str, masked: str, closers: dict[int, int]
) -> str | None:
    """Return `rustc` or `cargo` if the file spawns a compiler in test code."""
    if "Command" not in text:
        return None
    index = _file_index(masked)
    spans = _test_spans(masked, closers)
    for match in RUSTC_SPAWN.finditer(text):
        if _is_test_position(relative, spans, match.start()) and not _is_probe(
            text,
            match.start(),
            _command_window(text, masked, closers, match.start(), index),
        ):
            return "rustc"
    compiles = any(
        _compiles(text, masked, closers, match.start(), index)
        for match in CARGO_SPAWN.finditer(text)
        if _is_test_position(relative, spans, match.start())
    )
    return "cargo" if compiles else None


def _binary_name(relative: str) -> str | None:
    """Return the test binary a top-level `tests/<stem>.rs` builds, else None.

    `main.rs` and `mod.rs` under `tests/` are modules of other binaries as often
    as binaries, so they are judged at repository level.

    Returns
    -------
    str | None
        The binary's name, or None when the file is not a named test binary.
    """
    match = BINARY.search(relative)
    if match is None or match.group(2) in {"main", "mod"}:
        return None
    return match.group(2)


def _rust_files(checkout: pathlib.Path, problems: list[str]) -> list[pathlib.Path]:
    """Return every `.rs` file below the checkout, skipping non-source trees."""

    def unreadable(error: OSError) -> None:
        """Record an unreadable directory as a problem, once per message."""
        message = f"cannot scan {error.filename} for Rust tests: {error.strerror}"
        if message not in problems:
            problems.append(message)

    found: list[pathlib.Path] = []
    for directory, subdirectories, files in os.walk(checkout, onerror=unreadable):
        subdirectories[:] = [
            name
            for name in subdirectories
            if not name.startswith(".") and name not in SKIPPED_DIRECTORIES
        ]
        found.extend(
            pathlib.Path(directory) / name
            for name in files
            if name.endswith(".rs") and name not in SKIPPED_FILES
        )
    return sorted(found)


def find_spawners(checkout: pathlib.Path, problems: list[str]) -> list[Spawner]:
    """Return every Rust test source that spawns `cargo build`-like work or `rustc`.

    A file that cannot be read, or is larger than 1 MiB, is a problem fact and
    never a silent skip.

    Returns
    -------
    list[Spawner]
        The spawning sources in path order.
    """
    spawners: list[Spawner] = []
    for path in _rust_files(checkout, problems):
        relative = path.relative_to(checkout).as_posix()
        try:
            if path.stat().st_size > MAX_BYTES:
                problems.append(f"{relative} is larger than {MAX_BYTES} bytes")
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"{relative} cannot be read: {error}")
            continue
        if "Command" not in text:
            continue
        masked = mask_literals(text)
        closers = dict(brace_pairs(masked))
        kind = _spawn_kind(relative, text, masked, closers)
        if kind is None:
            continue
        spawners.append({
            "path": relative,
            "kind": kind,
            "binary": _binary_name(relative),
            "test_functions": sorted(set(FUNCTION.findall(text))),
        })
    return spawners
