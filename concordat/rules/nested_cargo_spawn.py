"""Find the Rust tests that spawn `cargo` or `rustc`, conservatively.

The nextest configuration rule (NC-004, NC-005) asks whether tests that start a
compiler are bounded. To ask it of the right repositories, this module scans
Rust test sources for a child process that compiles. The scan is textual and
deliberately narrow, because a false positive here would demand configuration
from a repository that needs none.

- Only test sources count: a file under a test directory (`tests/`,
  `test_support/`, `testing/`, `ui/`, or named `*_test(s).rs`), or the text of
  another `.rs` file after its first `#[cfg(test)]`. `build.rs` and `xtask` are
  not tests.
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
    rf"""\.args?\(\s*(?:&?\[\s*)?(?:\+\w+["']\s*,\s*["'])?["'](?:{SUBCOMMANDS})["']"""
)
COMMAND_NEW: typ.Final = re.compile(r"Command::new\(")
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


def _is_test_position(relative: str, text: str, position: int) -> bool:
    """Report whether a match at *position* sits in test code."""
    if TEST_PATH.search(relative):
        return True
    marker = text.find("#[cfg(test)]")
    return 0 <= marker < position


def _passed_on(text: str, start: int, window: str) -> bool:
    """Report whether a bound command is handed to another function.

    `let mut command = Command::new(cargo); build(&mut command)` adds its
    arguments in the callee, which may sit anywhere in the file, so a command
    that is bound and then passed on is read against the rest of the file.
    """
    bound = BINDING.search(text[max(0, start - 120) : start])
    if bound is None:
        return False
    name = re.escape(bound["name"])
    return re.search(rf"(?:&mut[ \t]+{name}\b|[(,][ \t]*{name}[ \t]*[,)])", window) is not None


def _command_window(text: str, start: int) -> str:
    """Return the text of the command built at *start*, up to a likely end.

    A command's arguments follow its construction, so the window runs to the
    next `Command::new` (another command) or the next function item, whichever
    comes first. A command bound to a name and handed to another function is
    read against the rest of the file, because the callee adds the arguments.
    A window that ends early can miss an argument, which errs towards not
    demanding configuration.

    Returns
    -------
    str
        The text from the spawn to the end of the window.
    """
    tail = text[start + 1 :]
    ends = [len(tail)]
    ends.extend(
        found.start()
        for found in (COMMAND_NEW.search(tail), FUNCTION_BOUNDARY.search(tail))
        if found is not None
    )
    window = text[start : start + 1 + min(ends)]
    return text[start:] if _passed_on(text, start, window) else window


def _spawn_kind(relative: str, text: str) -> str | None:
    """Return `rustc` or `cargo` if the file spawns a compiler in test code."""
    if "Command" not in text:
        return None
    for match in RUSTC_SPAWN.finditer(text):
        if _is_test_position(relative, text, match.start()):
            return "rustc"
    compiles = any(
        COMPILES.search(_command_window(text, match.start()))
        for match in CARGO_SPAWN.finditer(text)
        if _is_test_position(relative, text, match.start())
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
