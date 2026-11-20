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
  arguments are read with comments blanked and literal contents kept, so a
  flag in a comment is not an argument while a `//` inside a string does not
  hide the rest of the line;
  `cargo metadata` or `cargo config` do no compilation. The arguments are read
  from the spawn to the next `Command::new` or the next function, and only up
  to the command's first launch, so a compiling argument elsewhere in the file
  (a production `cargo build`, say) or one added after the command has run
  does not turn a test's `cargo metadata` into compiler work. A `rustc` spawn
  counts unless one of its own arguments asks who the compiler is (`--version`,
  `-V`, `-vV`) or for a table that needs no compile (`--print` of `sysroot`,
  `cfg`, `target-list`, ...): those compile nothing. `--print=link-args` and
  `--print=native-static-libs` build the crate, so they still count. A flag
  added on only some paths, behind a `&&`/`||` short circuit or inside a block
  that may not run, does not make the command a probe. A command must reach a
  launch inside its read to count at all, for `cargo` and `rustc` alike: a
  command that is built and given words but never run starts no child process,
  and a launch beyond the read is declined rather than guessed at.
- A command bound to a name and handed to a helper is read against the
  helper's body, so
  `let mut command = Command::new(cargo); add_build(&mut command)` sees
  `add_build`'s `arg("build")`. The call may span lines. Only statements on
  the parameter that receives the command count, so another command built in
  the helper does not contribute; those statements are read at the call site,
  and the parameter's name is taken to be the caller's binding. A launch in
  the helper or the caller therefore ends the arguments in the order they run,
  and a version flag added through a renamed parameter still probes the
  caller's command. Each argument and launch is attributed to the receiver it
  is called on, so `other.arg("build")` and `command.env("P", other.output())`
  are not this command's. A helper read that stops inside a block is closed
  before it is spliced, so a block the helper leaves open cannot hide a later
  unconditional flag. The helper must be defined exactly once in the file and
  receive the command at a position whose parameter is a plain `name: Type`;
  a binding in the caller's function (its own parameters included, even a
  method's after its receiver) that shadows the helper's name, a call written
  as a path or method (`support::configure(..)`, however it is spaced around
  the separator), a reassignment of the parameter (`command = other`), or a
  later declaration of its name inside the helper, declines the handoff
  rather than guessing. Compiler work in a declined shape can be missed.
- Python tests that spawn `cargo` are outside the scan: pytest, not nextest,
  bounds them.
- A file must name `Command`, so `env!("CARGO")` read for a path alone is not a
  spawn.

A test binary is a top-level `tests/<stem>.rs`; its test functions are the
`fn` items of that file. Helpers under `src/` or `tests/support/` are judged
at repository level only.

This module decides which files spawn a compiler; what each command does with
its arguments is read in :mod:`concordat.rules.nested_cargo_arguments`.
"""

from __future__ import annotations

import os
import pathlib
import re
import typing as typ

from .nested_cargo_arguments import compiles, is_probe, launches
from .nested_cargo_handoffs import command_window
from .nested_cargo_shadows import file_index
from .nested_cargo_source import brace_pairs
from .nested_cargo_syntax import top_level_item
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
CFG_TEST: typ.Final = re.compile(r"#\[cfg\(test\)\]")
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


def _spawn_kind(
    relative: str, text: str, masked: str, closers: dict[int, int]
) -> str | None:
    """Return `rustc` or `cargo` if the file spawns a compiler in test code.

    A command that never reaches a launch starts nothing, so its construction
    alone is not a spawn: its arguments are no evidence of compiler work, and
    guessing that the child process starts somewhere the read cannot see would
    demand configuration a test may not need.

    Returns
    -------
    str | None
        `rustc` or `cargo` when the file spawns that compiler in test code,
        else None.
    """
    if "Command" not in text:
        return None
    index = file_index(masked)
    spans = _test_spans(masked, closers)
    for match in RUSTC_SPAWN.finditer(text):
        if not _is_test_position(relative, spans, match.start()):
            continue
        window = command_window(text, masked, closers, match.start(), index)
        if launches(text, match.start(), window) and not is_probe(
            text, match.start(), window
        ):
            return "rustc"
    compiling = any(
        compiles(text, masked, closers, match.start(), index)
        for match in CARGO_SPAWN.finditer(text)
        if _is_test_position(relative, spans, match.start())
    )
    return "cargo" if compiling else None


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
