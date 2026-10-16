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

from .exception_docs import sections_in_text
from .fs_probe import probe_file
from .interrogate_invocation import lex_segments
from .make_commands import invokes_script, is_cargo_clippy
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
# A script that is not shell builds the cargo command from a list
# (`["cargo", "clippy"]`, or `argv = ["clippy", ...]` run as `cargo *argv`), so it
# has to name both cargo and clippy as string literals.
_CARGO_LITERAL: typ.Final = re.compile(r"""["']cargo["']|\bCARGO\b""")
_CLIPPY_LITERAL: typ.Final = re.compile(r"""["']clippy["']""")


class ExceptionScript(typ.TypedDict):
    """A script an exception section names, and what the audit could confirm."""

    path: str
    exists: bool
    runs_clippy: bool
    reached: bool
    error: str | None


class ClippyExceptions(typ.TypedDict):
    """The recorded exceptions, and the documents the audit could not read."""

    records: list[ClippyException]
    errors: list[str]


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


def _runs_clippy(source: str) -> bool:
    """Return whether a script's text runs `cargo clippy`.

    A shell line is lexed and read as a command; any other script has to name
    cargo and clippy as string literals. A mention of clippy alone (a `print`)
    is not a run.

    Returns
    -------
    bool
        Whether the script appears to run clippy through cargo.
    """
    if any(
        is_cargo_clippy(words)
        for line in source.splitlines()
        for words in lex_segments(line) or []
    ):
        return True
    return bool(_CARGO_LITERAL.search(source) and _CLIPPY_LITERAL.search(source))


def _script_fact(
    checkout: pathlib.Path,
    root: pathlib.Path,
    relative: str,
    lines: cabc.Sequence[str],
) -> ExceptionScript:
    """Return what the audit can confirm about one named script.

    A script the filesystem refuses to describe, or that is not UTF-8, carries
    an ``error`` and is not read as simply absent or as not running clippy.

    Returns
    -------
    ExceptionScript
        The script's facts.
    """
    path = checkout / relative
    inside = ".." not in relative.split("/") and within_checkout(
        root, path, OPERATION_READ_SCRIPT
    )
    probe = probe_file(path) if inside else None
    exists = probe is not None and probe.present
    error = probe.read_error if probe is not None else None
    runs_clippy = False
    if exists:
        try:
            runs_clippy = _runs_clippy(read_text(path, OPERATION_READ_SCRIPT))
        except UnicodeDecodeError as decoding:
            error = f"not UTF-8 text: {decoding}"
    return {
        "path": relative,
        "exists": exists,
        "runs_clippy": runs_clippy,
        "reached": exists and any(invokes_script(line, relative) for line in lines),
        "error": error,
    }


def _read_document(
    checkout: pathlib.Path, root: pathlib.Path, document: str
) -> tuple[str | None, str | None]:
    """Return the guide's text, or an error string when it cannot be read.

    An absent guide is neither: it simply records no exception. A guide the
    filesystem refuses to describe, or that is not UTF-8, is an error, so the
    policy reports it instead of reading it as "no exception recorded".

    Returns
    -------
    tuple[str | None, str | None]
        The text (``None`` when there is none to read) and the error, if any.
    """
    path = checkout / document
    within_checkout(root, path, OPERATION_READ_SCRIPT)
    probe = probe_file(path)
    if probe.read_error is not None:
        return None, f"{document}: {probe.read_error}"
    if not probe.present:
        return None, None
    try:
        return read_text(path, OPERATION_READ_SCRIPT), None
    except UnicodeDecodeError as error:
        return None, f"{document}: not UTF-8 text: {error}"


def clippy_exceptions(
    checkout: pathlib.Path,
    root: pathlib.Path,
    lint_lines: cabc.Sequence[str],
) -> ClippyExceptions:
    """Return the recorded clippy-gate exceptions with their scripts' facts.

    *lint_lines* are the expanded recipe lines of the targets `lint` reaches; a
    script counts as reached when a command on one of them runs it. A guide that
    resolves outside the checkout is refused by the containment guard; one that
    cannot be read is carried in ``errors``.

    Returns
    -------
    ClippyExceptions
        One record per exception section, in document order, and the read errors.
    """
    records: list[ClippyException] = []
    errors: list[str] = []
    for document in EXCEPTION_DOCUMENTS:
        text, error = _read_document(checkout, root, document)
        if error is not None:
            errors.append(error)
        for heading, line, body in sections_in_text(text or "", EXCEPTION_KEYWORD):
            records.append({
                "path": document,
                "heading": heading,
                "line": line,
                "scripts": [
                    _script_fact(checkout, root, relative, lint_lines)
                    for relative in _candidate_paths(body)
                ],
            })
    return {"records": records, "errors": errors}
