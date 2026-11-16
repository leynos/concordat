"""Find the Rust tests that spawn `cargo` or `rustc`, conservatively.

The nextest configuration rule (NC-004, NC-005) asks whether tests that start a
compiler are bounded. To ask it of the right repositories, this module scans
Rust test sources for a child process that compiles. The scan is textual and
deliberately narrow, because a false positive here would demand configuration
from a repository that needs none.

- Only test sources count: a file under a test directory (`tests/`,
  `test_support/`, `testing/`, `ui/`, or named `*_test(s).rs`), or the text of
  another `.rs` file inside an item marked `#[cfg(test)]`, for that item's
  braces only. `build.rs` and `xtask` are not tests.
- A `cargo` spawn counts only with a compiling subcommand in the arguments of
  that same command (`build`, `check`, `test`, `run`, `package`, ...);
  `cargo metadata` or `cargo config` do no compilation. The arguments are read
  from the spawn to the next `Command::new` or the next function, so a
  compiling argument elsewhere in the file (a production `cargo build`, say)
  does not turn a test's `cargo metadata` into compiler work. A `rustc` spawn
  always counts.
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

from .nested_cargo_commands import ARGUMENT_METHODS, UNSEEN_CALL
from .nested_cargo_source import brace_pairs
from .nested_cargo_text import argument_items
from .rust_source import mask_literals

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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
SUBCOMMANDS: typ.Final = (
    "build|check|test|run|package|clippy|doc|rustc|install|publish|bench|miri"
    "|nextest|llvm-cov|rustdoc"
)
COMPILES: typ.Final = re.compile(
    rf"""\.args?\(\s*(?:&?\[\s*)?(?:["']\+\w+["']\s*,\s*)?["'](?:{SUBCOMMANDS})["']"""
)
COMMAND_NEW: typ.Final = re.compile(r"Command::new\(")
CFG_TEST: typ.Final = re.compile(r"#\[cfg\(test\)\]")
ITEM_START: typ.Final = re.compile(r"[{;]")
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

    Returns
    -------
    tuple[int, int] | None
        The block as `(start, end)`, or None for a marked item with no braces
        (`mod tests;`, `use ..;`) or with unbalanced ones.
    """
    item = ITEM_START.search(masked, marker.end())
    if item is None or item.group() != "{":
        return None
    end = closers.get(item.start())
    return None if end is None else (marker.start(), end)


COMPILING_SUBCOMMANDS: typ.Final = frozenset(SUBCOMMANDS.split("|"))
# Options cargo reads one value for before the subcommand, so the value is not
# mistaken for it (`cargo --color never build`).
_LEADING_VALUE_FLAGS: typ.Final = frozenset({
    "--color",
    "--manifest-path",
    "--config",
    "-C",
    "-Z",
})
_NON_COMPILING_FLAG: typ.Final = "--unit-graph"
COMPILES_YES: typ.Final = "yes"
COMPILES_NO: typ.Final = "no"
COMPILES_UNKNOWN: typ.Final = "unknown"


_LITERAL: typ.Final = re.compile(r'"(?:[^"\\]|\\.)*"')


def _argument_words(calls: cabc.Iterable[tuple[str, str]]) -> list[str | None]:
    """Return a command's arguments in order, one word per argument.

    Each argument is read on its own, so `.args(["config", "get", query])`
    yields `config` and `get` before the word it cannot read. A call the reader
    could not see through is an unreadable word.

    Returns
    -------
    list[str | None]
        The literal text of each argument, None where it is not a literal.
    """
    words: list[str | None] = []
    for method, payload in calls:
        if method == UNSEEN_CALL:
            words.append(None)
        elif method in ARGUMENT_METHODS:
            words.extend(
                item[1:-1] if _LITERAL.fullmatch(item) else None
                for item in argument_items(payload)
            )
    return words


def compiles_state(calls: cabc.Iterable[tuple[str, str]]) -> str:
    """Say whether a cargo command's arguments start a compile.

    `cargo metadata`, `cargo fetch` and `cargo package --list` read a manifest
    but build nothing, so they pay for no cold build. An argument list that
    hides its subcommand behind a word the reader could not see may build or
    not, and that is reported as such rather than guessed.

    Returns
    -------
    str
        `yes` for a compiling subcommand, `no` for any other literal one, and
        `unknown` when an unreadable word comes before the subcommand.
    """
    words = _argument_words(calls)
    # `--unit-graph` prints the units cargo would build and builds none, so it
    # settles the answer even behind an argument the reader cannot see.
    cargo_words = words[: words.index("--")] if "--" in words else words
    if _NON_COMPILING_FLAG in cargo_words:
        return COMPILES_NO
    skip_value = False
    for index, word in enumerate(words):
        if word is None:
            return COMPILES_UNKNOWN
        if skip_value:
            skip_value = False
        elif word in _LEADING_VALUE_FLAGS:
            skip_value = True
        elif not word.startswith(("-", "+")):
            if word not in COMPILING_SUBCOMMANDS:
                return COMPILES_NO
            is_listing = word == "package" and any(
                rest in {"--list", "-l"} for rest in words[index + 1 :]
            )
            return COMPILES_NO if is_listing else COMPILES_YES
    return COMPILES_NO


def _test_spans(text: str) -> list[tuple[int, int]]:
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
    masked = mask_literals(text)
    closers = dict(brace_pairs(masked))
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


def _callee_text(text: str, window: str, name: str) -> str:
    """Return the bodies of the functions in *text* that receive a bound command.

    `let mut command = Command::new(cargo); add_build(&mut command)` adds its
    arguments in `add_build`, so the arguments are read from that function's
    body. A callee defined in another file is not seen, which errs towards not
    demanding configuration.

    Returns
    -------
    str
        The callee bodies, joined; empty when none is defined in this file.
    """
    passed = re.compile(
        rf"(?P<callee>\w+)[ \t]*\([ \t]*(?:[^()]*,[ \t]*)?(?:&(?:mut[ \t]+)?)?"
        rf"{re.escape(name)}[ \t]*[,)]"
    )
    bodies: list[str] = []
    for call in passed.finditer(window):
        definition = re.search(rf"\bfn[ \t]+{re.escape(call['callee'])}\b", text)
        if definition is None:
            continue
        tail = text[definition.end() :]
        boundary = FUNCTION_BOUNDARY.search(tail)
        end = definition.end() + (boundary.start() if boundary else len(tail))
        bodies.append(text[definition.start() : end])
    return "\n".join(bodies)


def _command_window(text: str, start: int) -> str:
    """Return the text of the command built at *start*, up to a likely end.

    A command's arguments follow its construction, so the window runs to the
    next `Command::new` (another command) or the next function item, whichever
    comes first. A command bound to a name and handed to a function defined in
    the same file is also read against that function's body, because the
    callee adds the arguments. A window that ends early can miss an argument,
    which errs towards not demanding configuration.

    Returns
    -------
    str
        The text from the spawn to the end of the window, with any callee body.
    """
    tail = text[start + 1 :]
    ends = [len(tail)]
    ends.extend(
        found.start()
        for found in (COMMAND_NEW.search(tail), FUNCTION_BOUNDARY.search(tail))
        if found is not None
    )
    window = text[start : start + 1 + min(ends)]
    bound = BINDING.search(text[max(0, start - 120) : start])
    if bound is None:
        return window
    return f"{window}\n{_callee_text(text, window, bound['name'])}"


def _spawn_kind(relative: str, text: str) -> str | None:
    """Return `rustc` or `cargo` if the file spawns a compiler in test code."""
    if "Command" not in text:
        return None
    spans = _test_spans(text)
    for match in RUSTC_SPAWN.finditer(text):
        if _is_test_position(relative, spans, match.start()):
            return "rustc"
    compiles = any(
        COMPILES.search(_command_window(text, match.start()))
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
        kind = _spawn_kind(relative, text)
        if kind is None:
            continue
        spawners.append({
            "path": relative,
            "kind": kind,
            "binary": _binary_name(relative),
            "test_functions": sorted(set(FUNCTION.findall(text))),
        })
    return spawners
