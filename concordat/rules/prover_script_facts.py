"""Read the scripts and tests that would install a prover (PT-004, PT-005).

A local installer is a file under a script directory whose name pairs
`install`, `setup` or `bootstrap` with `kani`, `verus` or `z3`; a shell script
containing an installer command; or a Python script holding a string constant
that begins with a Kani or Verus release-download URL, read from the `ast`, so a
comment or a homepage link does not count. A test of installation is found by
file name only: a file under `tests/` pairing `install` with a prover word.
"""

from __future__ import annotations

import ast
import dataclasses
import typing as typ

from .prover_commands import is_installer, is_runner
from .shell_commands import commands

if typ.TYPE_CHECKING:
    import pathlib

    from .prover_tooling_types import LocalInstaller

SCRIPT_DIRECTORIES: typ.Final = ("scripts", ".github/scripts", "tools")
TEST_DIRECTORY: typ.Final = "tests"
SKIPPED_DIRECTORIES: typ.Final = frozenset({
    "target",
    "node_modules",
    ".git",
    ".venv",
    "fixtures",
    "fixture",
    "tests",
    "test",
})
SHELL_SUFFIXES: typ.Final = frozenset({".sh", ".bash"})
READ_SUFFIXES: typ.Final = frozenset({".sh", ".bash", ".py"})
INSTALL_WORDS: typ.Final = ("install", "setup", "bootstrap")
TOOL_WORDS: typ.Final = ("kani", "verus", "z3")
TEST_TOOL_WORDS: typ.Final = (*TOOL_WORDS, "prover")
DOWNLOAD_PREFIXES: typ.Final = (
    "https://github.com/model-checking/kani",
    "https://github.com/verus-lang/verus",
)
RELEASE_PATHS: typ.Final = ("/releases/download/", "/releases/latest/download/")


@dataclasses.dataclass(slots=True)
class ScriptFacts:
    """Local installers, whether a script runs a prover, and unreadable files."""

    installers: list[LocalInstaller] = dataclasses.field(default_factory=list)
    runs: bool = False
    unreadable: list[str] = dataclasses.field(default_factory=list)


def _children(directory: pathlib.Path) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """Return a directory's subdirectories to walk and its regular files."""
    entries = [e for e in sorted(directory.iterdir()) if not e.is_symlink()]
    folders = [e for e in entries if e.is_dir() and e.name not in SKIPPED_DIRECTORIES]
    return folders, [e for e in entries if e.is_file()]


def _walk(base: pathlib.Path) -> list[pathlib.Path]:
    """Return the regular files under *base*, skipping vendored and test trees."""
    found: list[pathlib.Path] = []
    stack = [base]
    while stack:
        folders, files = _children(stack.pop())
        stack.extend(folders)
        found.extend(files)
    return found


def _script_files(checkout: pathlib.Path) -> list[pathlib.Path]:
    """Return the files under the script directories, in stable order."""
    bases = [checkout / name for name in SCRIPT_DIRECTORIES]
    real = [b for b in bases if b.is_dir() and not b.is_symlink()]
    return sorted(path for base in real for path in _walk(base))


def _has_download_constant(source: str) -> bool:
    """Return whether Python *source* holds a prover release-download URL."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    values = (n.value for n in ast.walk(tree) if isinstance(n, ast.Constant))
    return any(_is_release_download(v) for v in values if isinstance(v, str))


def _is_release_download(value: str) -> bool:
    """Return whether *value* is a Kani or Verus release-download URL."""
    if not value.startswith(DOWNLOAD_PREFIXES):
        return False
    return any(path in value for path in RELEASE_PATHS)


def _has_installer_command(source: str) -> bool:
    """Return whether shell *source* runs an installer command."""
    return any(is_installer(argv) for argv in commands(source))


def _is_named_installer(path: pathlib.Path) -> bool:
    """Return whether the file's name pairs an install word with a tool."""
    stem = path.stem.lower()
    names_install = any(word in stem for word in INSTALL_WORDS)
    return names_install and any(word in stem for word in TOOL_WORDS)


def _source_reason(path: pathlib.Path, source: str) -> str | None:
    """Return why a readable script is an installer by what it contains."""
    if path.suffix in SHELL_SUFFIXES and _has_installer_command(source):
        return "command"
    if path.suffix == ".py" and _has_download_constant(source):
        return "download"
    return None


def installer_reason(path: pathlib.Path, source: str | None) -> str | None:
    """Return why *path* is a local installer, or None when it is not.

    Returns
    -------
    str | None
        `name`, `command` or `download`.
    """
    if _is_named_installer(path):
        return "name"
    return None if source is None else _source_reason(path, source)


def _read(path: pathlib.Path, relative: str, facts: ScriptFacts) -> str | None:
    """Return a script's text, recording why it could not be read."""
    if path.suffix not in READ_SUFFIXES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        facts.unreadable.append(f"{relative}: {error}")
        return None


def _runs_prover(path: pathlib.Path, source: str | None) -> bool:
    """Return whether a shell script runs a prover."""
    if source is None or path.suffix not in SHELL_SUFFIXES:
        return False
    return any(is_runner(argv) for argv in commands(source))


def scan_scripts(checkout: pathlib.Path) -> ScriptFacts:
    """Return the local installers, prover runs and unreadable files in scripts.

    Returns
    -------
    ScriptFacts
        What the script directories hold.
    """
    facts = ScriptFacts()
    for path in _script_files(checkout):
        relative = path.relative_to(checkout).as_posix()
        source = _read(path, relative, facts)
        reason = installer_reason(path, source)
        if reason is not None:
            facts.installers.append({"path": relative, "reason": reason})
        facts.runs = facts.runs or _runs_prover(path, source)
    return facts


def _is_regular(path: pathlib.Path) -> bool:
    """Return whether *path* is a regular file that is not a symbolic link."""
    return path.is_file() and not path.is_symlink()


def _is_install_test(path: pathlib.Path) -> bool:
    """Return whether a file's name says it tests prover installation."""
    name = path.name.lower()
    if "install" not in name:
        return False
    return any(word in name for word in TEST_TOOL_WORDS)


def install_tests(checkout: pathlib.Path) -> list[str]:
    """Return test files whose names say they test prover installation.

    Returns
    -------
    list[str]
        Their paths relative to the checkout, sorted.
    """
    base = checkout / TEST_DIRECTORY
    if not base.is_dir() or base.is_symlink():
        return []
    found = [p for p in base.rglob("*") if _is_regular(p)]
    return sorted(
        p.relative_to(checkout).as_posix() for p in found if _is_install_test(p)
    )
