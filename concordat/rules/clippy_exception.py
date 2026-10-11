"""Read a repository's recorded clippy-gate exception.

DG-004 asks `make lint` to reach a `cargo clippy` command written in a recipe.
A repository can have a reason to run clippy through a wrapper script instead
(femtologging's lint lanes exist for PyO3 and the toolchain), and a wrapper is
text the rule cannot see inside. The estate's exception idiom, set by
rust-toolchain-policy, is a reasoned record in the consumer repository: a
section of `docs/developers-guide.md` whose heading contains the keyword and
whose body names the script in backticks.

This module records the facts and decides nothing. For each section it lists the
scripts named, and for each script whether it exists inside the checkout,
whether it mentions clippy as a cargo subcommand, and whether a recipe `lint`
reaches names it. The policy turns those into a verdict.
"""

from __future__ import annotations

import re
import typing as typ

from .exception_docs import section_bodies
from .fs_probe import probe_file
from .markdown_envelope import read_text, within_checkout

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

EXCEPTION_DOCUMENTS: typ.Final = ("docs/developers-guide.md",)
EXCEPTION_KEYWORD: typ.Final = "Clippy gate"
OPERATION_READ_SCRIPT: typ.Final = "read-clippy-exception-script"

_BACKTICKED: typ.Final = re.compile(r"`([^`\s]+)`")
_SCRIPT_SUFFIXES: typ.Final = (
    ".py",
    ".sh",
    ".bash",
    ".js",
    ".mjs",
    ".ts",
    ".rb",
    ".pl",
)
# `cargo clippy` written out, or clippy handed to cargo as an argument: a script
# builds the command from a list (`["cargo", "clippy"]` or `argv = ["clippy"]`).
_RUNS_CLIPPY: typ.Final = re.compile(r"cargo(?:\s+\+\S+)?\s+clippy\b|[\"']clippy[\"']")


class ExceptionScript(typ.TypedDict):
    """A script an exception section names, and what the audit could confirm."""

    path: str
    exists: bool
    runs_clippy: bool
    reached: bool


class ClippyException(typ.TypedDict):
    """One recorded clippy-gate exception section."""

    path: str
    heading: str
    line: int
    scripts: list[ExceptionScript]


def _is_script_path(token: str) -> bool:
    """Return whether a backticked token names a script inside the checkout."""
    if token.startswith(("/", "-")):
        return False
    return "/" in token or token.endswith(_SCRIPT_SUFFIXES)


def _candidate_paths(body: str) -> list[str]:
    """Return the backticked tokens of *body* that look like script paths."""
    return list(
        dict.fromkeys(t for t in _BACKTICKED.findall(body) if _is_script_path(t))
    )


def _script_fact(
    checkout: pathlib.Path,
    root: pathlib.Path,
    relative: str,
    lines: cabc.Sequence[str],
) -> ExceptionScript:
    """Return what the audit can confirm about one named script."""
    path = checkout / relative
    inside = ".." not in relative.split("/") and within_checkout(
        root, path, OPERATION_READ_SCRIPT
    )
    exists = inside and probe_file(path).present
    runs_clippy = False
    if exists:
        try:
            runs_clippy = (
                _RUNS_CLIPPY.search(read_text(path, OPERATION_READ_SCRIPT)) is not None
            )
        except UnicodeDecodeError:
            runs_clippy = False
    return {
        "path": relative,
        "exists": exists,
        "runs_clippy": runs_clippy,
        "reached": exists and any(relative in line for line in lines),
    }


def clippy_exceptions(
    checkout: pathlib.Path,
    root: pathlib.Path,
    lint_lines: cabc.Sequence[str],
) -> list[ClippyException]:
    """Return the recorded clippy-gate exceptions with their scripts' facts.

    *lint_lines* are the expanded recipe lines of the targets `lint` reaches; a
    script counts as reached when its path appears in one of them.

    Returns
    -------
    list[ClippyException]
        One entry per exception section, in document order.
    """
    return [
        {
            "path": document,
            "heading": heading,
            "line": line,
            "scripts": [
                _script_fact(checkout, root, relative, lint_lines)
                for relative in _candidate_paths(body)
            ],
        }
        for document in EXCEPTION_DOCUMENTS
        for heading, line, body in section_bodies(checkout, document, EXCEPTION_KEYWORD)
    ]
