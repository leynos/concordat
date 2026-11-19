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
  from the spawn to the next `Command::new` or the next function, so a
  compiling argument elsewhere in the file (a production `cargo build`, say)
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
  a local binding that shadows the helper's name, or a later declaration of
  the parameter's name inside the helper, declines the handoff rather than
  guessing. Compiler work in a declined shape can be missed.
- Python tests that spawn `cargo` are outside the scan: pytest, not nextest,
  bounds them.
- A file must name `Command`, so `env!("CARGO")` read for a path alone is not a
  spawn.

A test binary is a top-level `tests/<stem>.rs`; its test functions are the
`fn` items of that file. Helpers under `src/` or `tests/support/` are judged
at repository level only.
"""

from __future__ import annotations

import os
import pathlib
import re
import typing as typ
from itertools import starmap

from .nested_cargo_source import brace_pairs
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
# A `self` receiver makes the parameter the command reaches ambiguous, so the
# callee analysis is declined rather than guessed.
SELF_RECEIVER: typ.Final = re.compile(r"\s*(?:&\s*(?:'\w+\s*)?)?(?:mut\s+)?self\b")
# Only a plain `[mut] name: Type` parameter names a value the helper can use.
PARAMETER: typ.Final = re.compile(
    r"\s*(?:mut\s+)?(?P<name>[A-Za-z_]\w*)\s*:\s*\S[\s\S]*"
)
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


def _top_level_item(masked: str, start: int) -> tuple[int, str] | None:
    """Return the first `{` or `;` after *start* that sits outside any bracket.

    A signature can hold a `;` inside an array length (`[u8; 4]`) or a
    parenthesised type, so only a delimiter at depth zero ends the item. A
    stray closing bracket or the end of the file leaves the item unfinished.

    Returns
    -------
    tuple[int, str] | None
        The offset and the character found, or None when the item does not
        close cleanly.
    """
    depth = 0
    for index in range(start, len(masked)):
        match masked[index]:
            case "(" | "[":
                depth += 1
            case ")" | "]":
                depth -= 1
                if depth < 0:
                    return None
            case "}" if depth == 0:
                return None
            case "{" | ";" if depth == 0:
                return index, masked[index]
    return None


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
    item = _top_level_item(masked, marker.end())
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


def _parameter_open(masked: str, start: int) -> int | None:
    """Return the offset of the `(` that opens a parameter list after *start*.

    Generics may hold their own parentheses (`F: Fn()`) and return arrows
    (`F: Fn() -> ()`), so only a `(` at bracket depth zero opens the parameter
    list, and the `>` of an arrow does not close a generic.

    Returns
    -------
    int | None
        The offset of the `(`, or None when none follows at depth zero.
    """
    depth = 0
    index = start
    while index < len(masked):
        match masked[index]:
            case "(" if depth == 0:
                return index
            case "-" if masked.startswith("->", index):
                index += 2
                continue
            case "[" | "{" | "<":
                depth += 1
            case "]" | "}" | ">":
                depth = max(0, depth - 1)
        index += 1
    return None


def _parameter_close(masked: str, open_at: int) -> int | None:
    """Return the offset of the `)` that closes the parameter list at *open_at*.

    Returns
    -------
    int | None
        The offset of the closing `)`, or None when the list never closes.
    """
    depth = 0
    for index in range(open_at, len(masked)):
        match masked[index]:
            case "(":
                depth += 1
            case ")":
                depth -= 1
                if depth == 0:
                    return index
    return None


def _split_parameters(parameters: str) -> list[str]:
    """Split a parameter list on the commas that sit outside brackets.

    A generic type (`Result<A, B, E>`), an array length (`[u8; 4]`) or a
    function-pointer return (`fn() -> ()`) keeps its commas, so a position
    still indexes the parameter the caller passed. The `>` of a return arrow
    is not a generic closer.

    Returns
    -------
    list[str]
        The parameters in order; an empty trailing entry is kept so positions
        do not shift.
    """
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(parameters):
        char = parameters[index]
        if char == "-" and parameters.startswith("->", index):
            index += 2
            continue
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(parameters[start:index])
            start = index + 1
        index += 1
    parts.append(parameters[start:])
    return parts


def _parameter_at(
    masked: str, definition_end: int, position: int
) -> tuple[str, int] | None:
    """Return the parameter at *position* and the offset closing its list.

    Only a plain `[mut] name: Type` parameter names the command the helper
    receives. A `self` receiver or a more elaborate parameter hides which
    value the command reaches, so the callee is declined instead of guessed.

    Returns
    -------
    tuple[str, int] | None
        The parameter's name and the offset of the closing `)`, or None when
        the list cannot be read or the position holds no simple parameter.
    """
    open_at = _parameter_open(masked, definition_end)
    if open_at is None:
        return None
    close_at = _parameter_close(masked, open_at)
    if close_at is None:
        return None
    parameters = masked[open_at + 1 : close_at]
    if SELF_RECEIVER.match(parameters):
        return None
    parts = _split_parameters(parameters)
    if position >= len(parts):
        return None
    found = PARAMETER.fullmatch(parts[position])
    return None if found is None else (found["name"], close_at)


def _body_span(
    masked: str, closers: dict[int, int], start: int
) -> tuple[int, int] | None:
    """Return the body offsets of a function whose signature ends at *start*.

    The body is the matched brace pair, so a `;` in the signature or a brace
    in a nested item cannot end it early.

    Returns
    -------
    tuple[int, int] | None
        The offsets just inside the braces, or None for a declaration (`fn
        f(..);`) or an unbalanced body.
    """
    item = _top_level_item(masked, start)
    if item is None or item[1] != "{":
        return None
    end = closers.get(item[0])
    return None if end is None else (item[0] + 1, end - 1)


def _receiver_statements(
    text: str, masked: str, span: tuple[int, int], parameter: str, receiver: str
) -> str:
    """Return the body statements that add arguments to *parameter*.

    A statement that calls the parameter is that command's argument, wherever
    the call sits (`if enabled { command.arg("build") }` included). The
    statements are split on `;` in the masked text and read back from the
    original, so literal arguments survive. The parameter's name is replaced by
    the caller's binding, because the probe consumer reads the helper's
    statements as part of the caller's window.

    Every statement's braces are kept, in place, because a later statement's
    block depth decides whether its probe flag is unconditional; a statement
    that follows the closing brace of a block keeps that brace in front of it.
    A later declaration of the parameter's name, or another command built in
    the helper, means the name no longer denotes the caller's command, so
    nothing after that statement is read. That declines a helper the scan
    cannot attribute, which can miss compiler work rather than invent it.

    Returns
    -------
    str
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
    offset = start
    for statement in masked[start:end].split(";"):
        if COMMAND_NEW.search(statement) or shadow.search(statement):
            break
        found = own.search(statement)
        if found is None:
            pieces.append("{" * statement.count("{") + "}" * statement.count("}"))
        else:
            piece = text[offset : offset + len(statement)]
            if receiver != parameter:
                piece = f"{piece[: found.start()]}{receiver}.{piece[found.end() :]}"
            pieces.append(piece)
        offset += len(statement) + 1
    return ";\n".join(pieces)


def _shadowed(masked: str, name: str, at: int) -> bool:
    """Report whether *name* may be a local binding at *at* rather than a `fn`.

    A closure or binding with the helper's name shadows the free function, so
    the call reaches something the scan cannot read. Both are written so that
    the name is followed by `=`, `:` or `|` rather than `(`.

    Returns
    -------
    bool
        True when a binding of that name appears before *at*.
    """
    scope = [found.start() for found in FUNCTION_BOUNDARY.finditer(masked, 0, at)]
    start = scope[-1] if scope else 0
    binder = re.compile(
        rf"\b(?:let\s+(?:mut\s+)?|const\s+|static\s+){re.escape(name)}\b"
    )
    return binder.search(masked, start, at) is not None


def _callee_arguments(
    text: str,
    masked: str,
    closers: dict[int, int],
    call: re.Match[str],
    receiver: str,
    base: int,
) -> str:
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
    str
        The statements on the parameter that receives the command, rewritten to
        *receiver*, or an empty string when the helper is undefined,
        duplicated, shadowed or shaped uncertainly.
    """
    if _shadowed(masked, call["callee"], base + call.start()):
        return ""
    definitions = list(re.finditer(rf"\bfn\s+{re.escape(call['callee'])}\b", masked))
    if len(definitions) != 1:
        return ""
    definition = definitions[0]
    position = _argument_position(call["before"])
    if position is None:
        return ""
    found = _parameter_at(masked, definition.end(), position)
    if found is None:
        return ""
    parameter, close_at = found
    span = _body_span(masked, closers, close_at + 1)
    if span is None:
        return ""
    tail = masked[definition.end() :]
    boundary = FUNCTION_BOUNDARY.search(tail)
    limit = definition.end() + (boundary.start() if boundary else len(tail))
    start, end = span
    end = min(end, limit)
    if end <= start:
        return ""
    return _receiver_statements(text, masked, (start, end), parameter, receiver)


def _callee_bodies(
    text: str, masked: str, closers: dict[int, int], window: str, name: str, base: int
) -> list[tuple[int, str]]:
    """Return the helper statements each call adds, with the call's end offset.

    `let mut command = Command::new(cargo); add_build(&mut command)` adds its
    arguments in `add_build`, so those arguments are read from that function's
    body. Only statements on the parameter that receives the command count, so
    another command built in the helper does not contribute. A callee defined
    in another file is not seen, which errs towards not demanding
    configuration.

    Returns
    -------
    list[tuple[int, str]]
        The offset in *window* just past each call, with the statements it adds;
        empty when no helper is read.
    """
    passed = re.compile(
        rf"(?P<callee>\w+)\s*\(\s*(?:(?P<before>[^()]*),\s*)?"
        rf"(?:&(?:mut\s+)?)?{re.escape(name)}\s*[,)]"
    )
    bodies: list[tuple[int, str]] = []
    for call in passed.finditer(window):
        body = _callee_arguments(text, masked, closers, call, name, base)
        if body:
            bodies.append((call.end(), body))
    return bodies


def _command_window(text: str, masked: str, closers: dict[int, int], start: int) -> str:
    """Return the text of the command built at *start*, up to a likely end.

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
    str
        The text from the spawn to the end of the window, with each callee body
        spliced in at its call.
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
    bound = BINDING.search(text[max(0, start - 120) : start])
    if bound is None:
        return window
    masked_window = masked[start : start + length]
    pieces: list[str] = []
    previous = 0
    for at, body in _callee_bodies(
        text, masked, closers, masked_window, bound["name"], start
    ):
        pieces.extend((window[previous:at], f";\n{body};"))
        previous = at
    pieces.append(window[previous:])
    return "".join(pieces)


def _own_statements(text: str, start: int, window: str) -> list[tuple[str, int]]:
    """Return the statements of *window* that can add arguments to the command.

    A command built in one chain owns only its first statement. A command bound
    to a name owns the statements that start with that name (`command.arg(..)`),
    plus the binding itself, up to its first launch (`.status()`, `.output()`,
    `.spawn()`); another command's `.arg(..)`, or an argument added when the
    command is reused after a launch, is not its argument.

    Returns
    -------
    list[tuple[str, int]]
        Each statement, comments removed, with the number of blocks still open
        where it starts (the window's own braces, counted from the command).
    """
    statements = COMMENT.sub("", window).split(";")
    bound = BINDING.search(text[max(0, start - 120) : start])
    if bound is None:
        return [(statements[0], 0)]
    # A statement mentions the command when the name is followed by a method
    # call, wherever it sits (an `if` body included), so a launch in control
    # flow still ends the arguments.
    own = re.compile(rf"\b{re.escape(bound['name'])}\s*\.")
    found: list[tuple[str, int]] = []
    depth = 0
    for index, statement in enumerate(statements):
        if index == 0 or own.search(statement):
            found.append((statement, depth))
            # An argument added after the command has run belongs to a reuse.
            if LAUNCH.search(statement):
                break
        depth += statement.count("{") - statement.count("}")
    return found


def _is_probe(text: str, start: int, window: str) -> bool:
    """Report whether a `rustc` command only asks a question and compiles nothing.

    Only arguments added to this command count, so a `"--version"` in a comment,
    in an assertion after it or on another command is not its argument.

    Returns
    -------
    bool
        True when an argument of the command is a version or query flag.
    """
    return any(starmap(_unconditional_probe, _own_statements(text, start, window)))


def _unconditional_probe(statement: str, depth: int) -> bool:
    """Report whether a statement adds a probe flag on every path through it.

    A flag added inside a block (`if query_only { cmd.arg("--print=cfg") }`),
    whether in the same statement or in one that follows the opening brace, may
    not be there when the command runs, so it proves nothing.

    Returns
    -------
    bool
        True when the statement holds a probe flag outside any block.
    """
    found = RUSTC_PROBE.search(statement)
    return found is not None and depth == 0 and "{" not in statement[: found.start()]


def _spawn_kind(
    relative: str, text: str, masked: str, closers: dict[int, int]
) -> str | None:
    """Return `rustc` or `cargo` if the file spawns a compiler in test code."""
    if "Command" not in text:
        return None
    spans = _test_spans(masked, closers)
    for match in RUSTC_SPAWN.finditer(text):
        if _is_test_position(relative, spans, match.start()) and not _is_probe(
            text,
            match.start(),
            _command_window(text, masked, closers, match.start()),
        ):
            return "rustc"
    compiles = any(
        COMPILES.search(_command_window(text, masked, closers, match.start()))
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
