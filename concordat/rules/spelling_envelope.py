"""Build the `policy-input/spelling-config-baseline` envelope.

The spelling rule package reasons over five kinds of fact: the root
`Makefile` (parsed by the pinned `makeutil`, as the Markdown and Rust
envelopes do), every GitHub Actions workflow under `.github/workflows`, the
`typos.local.toml` overlay, the root `.gitignore`, and the paths of any
vendored spelling machinery the checkout still carries. Each fact is recorded
as it was found: a file that exists but cannot be decoded is carried with its
`error`, so the policy reports an indeterminate verdict rather than reading
the file as absent.

The file readers are `markdown_envelope`'s, so both envelopes share one
containment guard: no policy input is read from outside the checkout.
"""

from __future__ import annotations

import fnmatch
import os
import pathlib
import tomllib
import typing as typ

from .makefile_facts import MakeutilReport, inspect_makefile
from .markdown_envelope import (
    OPERATION_PROBE_PATH,
    OPERATION_READ_MAKEFILE,
    PRUNED_DIRECTORIES,
    Repository,
    WorkflowFile,
    _is_file,
    _load_workflows,
    _raise_walk_error,
    _read_text,
    _resolved_root,
    _within_checkout,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/spelling-config-baseline"
TYPOS_LOCAL_FILENAME: typ.Final = "typos.local.toml"
TYPOS_CONFIG_FILENAME: typ.Final = "typos.toml"
GITIGNORE_FILENAME: typ.Final = ".gitignore"
# Paths a legacy spelling setup vendored into consumers; the builder now owns
# all of it. Repository-relative globs, matched against POSIX paths.
DEFAULT_VENDORED_PATTERNS: typ.Final = (
    "scripts/generate_typos_config.py",
    "scripts/typos_rollout*.py",
    "scripts/tests/test_generate_typos_config.py",
    "scripts/tests/test_typos_rollout*.py",
    "scripts/*phrase*check*.py",
    "scripts/tests/test_*phrase*check*.py",
)

OPERATION_READ_TYPOS_LOCAL: typ.Final = "read-typos-local"
OPERATION_READ_GITIGNORE: typ.Final = "read-gitignore"


class DecodedFile(typ.TypedDict):
    """One decoded input file, or the file with the reason it was not decoded."""

    path: str
    parsed: object | None
    error: str | None


class GitignoreFile(typ.TypedDict):
    """The root `.gitignore`, as its stripped lines, or carrying its error."""

    path: str
    lines: list[str]
    error: str | None


class SpellingApplicability(typ.TypedDict):
    """Evidence that the spelling rule package applies to the checkout."""

    root_makefile: bool
    typos_config: bool
    typos_local: bool


class SpellingEnvelope(typ.TypedDict):
    """The `policy-input/spelling-config-baseline` document."""

    schema_version: int
    kind: str
    repository: Repository
    applicability: SpellingApplicability
    makefile: MakeutilReport | None
    workflows: list[WorkflowFile]
    typos_local: DecodedFile | None
    gitignore: GitignoreFile | None
    vendored: list[str]


def _read_input(
    checkout: pathlib.Path, root: pathlib.Path, name: str, operation: str
) -> tuple[str | None, str | None] | None:
    """Return one root file's text or its decoding error, or None when absent.

    An `OperationalRuleError` propagates from the containment guard, or when
    the file exists but cannot be opened.

    Returns
    -------
    tuple[str | None, str | None] | None
        ``(text, None)``, ``(None, error)`` for undecodable bytes, or ``None``
        when the file does not exist.
    """
    path = checkout / name
    if not _within_checkout(root, path, operation) or not _is_file(path, operation):
        return None
    try:
        return _read_text(path, operation), None
    except UnicodeDecodeError as error:
        return None, f"not UTF-8 text: {error}"


def _load_typos_local(checkout: pathlib.Path, root: pathlib.Path) -> DecodedFile | None:
    """Return the decoded `typos.local.toml`, or ``None`` when it is absent.

    Returns
    -------
    DecodedFile | None
        The overlay decoded as TOML, or carrying its decoding error.
    """
    read = _read_input(checkout, root, TYPOS_LOCAL_FILENAME, OPERATION_READ_TYPOS_LOCAL)
    if read is None:
        return None
    text, error = read
    fact: DecodedFile = {"path": TYPOS_LOCAL_FILENAME, "parsed": None, "error": error}
    if text is None:
        return fact
    try:
        fact["parsed"] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as decode_error:
        fact["error"] = f"invalid TOML: {decode_error}"
    return fact


def _load_gitignore(checkout: pathlib.Path, root: pathlib.Path) -> GitignoreFile | None:
    """Return the root `.gitignore` as stripped lines, or ``None`` when absent.

    Returns
    -------
    GitignoreFile | None
        The file's non-empty lines with surrounding whitespace removed, or
        carrying its decoding error.
    """
    read = _read_input(checkout, root, GITIGNORE_FILENAME, OPERATION_READ_GITIGNORE)
    if read is None:
        return None
    text, error = read
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return {"path": GITIGNORE_FILENAME, "lines": lines, "error": error}


def _vendored_paths(checkout: pathlib.Path, patterns: cabc.Sequence[str]) -> list[str]:
    """Return every file whose repository-relative path matches a pattern.

    The walk skips the pruned tool and dependency directories and never
    follows symbolic links. A directory that cannot be listed raises rather
    than being skipped, through `markdown_envelope`'s walk-error handler.

    Returns
    -------
    list[str]
        The matching POSIX paths, sorted.
    """
    found: list[str] = []
    for current, directories, files in os.walk(checkout, onerror=_raise_walk_error):
        directories[:] = sorted(
            name for name in directories if name not in PRUNED_DIRECTORIES
        )
        base = pathlib.Path(current).relative_to(checkout)
        for name in files:
            relative = (base / name).as_posix()
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
                found.append(relative)
    return sorted(found)


def build_spelling_envelope(
    checkout: pathlib.Path,
    vendored_patterns: cabc.Sequence[str] = DEFAULT_VENDORED_PATTERNS,
) -> SpellingEnvelope:
    """Assemble the spelling policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    vendored_patterns:
        Repository-relative globs naming legacy spelling machinery.

    An `OperationalRuleError` propagates from the fact readers if the root
    `Makefile` cannot be parsed by `makeutil`, a fact file exists but cannot
    be opened, or a fact file resolves to a location outside the checkout.

    Returns
    -------
    SpellingEnvelope
        The policy input document assembled from the checkout.
    """
    root = _resolved_root(checkout)
    makefile_path = checkout / "Makefile"
    makefile_report: MakeutilReport | None = None
    if _within_checkout(root, makefile_path, OPERATION_READ_MAKEFILE) and _is_file(
        makefile_path, OPERATION_READ_MAKEFILE
    ):
        makefile_report = inspect_makefile(makefile_path).report
    typos_local = _load_typos_local(checkout, root)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "root_makefile": makefile_report is not None,
            "typos_config": _is_file(
                checkout / TYPOS_CONFIG_FILENAME, OPERATION_PROBE_PATH
            ),
            "typos_local": typos_local is not None,
        },
        "makefile": makefile_report,
        "workflows": _load_workflows(checkout, root),
        "typos_local": typos_local,
        "gitignore": _load_gitignore(checkout, root),
        "vendored": _vendored_paths(checkout, vendored_patterns),
    }
