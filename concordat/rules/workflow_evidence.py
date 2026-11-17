"""Read the root GitHub Actions workflow documents shared by several policies.

A repository's workflows are evidence for more than one canon rule package. The
reader here owns the part every such package must get right: enumerate only
`.github/workflows` itself, refuse a directory that resolves outside the
checkout, decode each document without following a symlink, and retain a
document that could not be decoded as a fact carrying its reason. A policy can
then return an indeterminate verdict for that one file instead of treating it
as absent.

The two callables that read a file and convert a decoded value to JSON are
parameters so a caller can keep its own module-level hooks. Existing policies
do that to substitute a reader in a test without changing the collector.
"""

from __future__ import annotations

import json
import os
import pathlib
import stat
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .fs_probe import probe_file

WORKFLOWS_DIRECTORY: typ.Final = pathlib.PurePosixPath(".github/workflows")
WORKFLOW_SUFFIXES: typ.Final = frozenset({".yml", ".yaml"})
OPERATION_READ_WORKFLOW: typ.Final = "read-workflow"
_READ_CHUNK: typ.Final = 65536

_yaml = YAML(typ="safe")


class WorkflowFile(typ.TypedDict):
    """Store one workflow document or the reason it could not be decoded.

    Exactly one of `parsed` and `error` carries the evidence. A file that
    could not be decoded stays in the envelope with its reason, so the policy
    returns an indeterminate verdict for that file rather than treating it as
    absent, which would clear every clause the file might have violated.

    Attributes
    ----------
    path:
        The workflow's path relative to the checkout, in POSIX form.
    parsed:
        The decoded YAML document, or None when it could not be decoded.
    error:
        Why the document could not be decoded, or None when it was.
    """

    path: str
    parsed: object | None
    error: str | None


if typ.TYPE_CHECKING:
    import collections.abc as cabc

    type ReadText = cabc.Callable[[pathlib.Path], str]
    type JsonSafe = cabc.Callable[[object], object]


def read_text(path: pathlib.Path) -> str:
    """Read a workflow as UTF-8 text without following a symlink, or raise.

    The entry's mode was checked before this read, but a checkout another
    process can write to may change between the check and the open.
    `O_NOFOLLOW` makes a swapped symlink fail the read instead of contributing
    another file's content to the workflow evidence. `O_NONBLOCK` keeps a
    swapped-in FIFO from waiting for a writer, and the opened descriptor is
    checked before any read so every other non-regular replacement is refused
    the same way.

    Returns
    -------
    str
        The file's decoded contents.

    Raises
    ------
    OperationalRuleError
        If the file cannot be opened or read, including when the final path
        component is a symlink or the opened entry is not a regular file.

    Notes
    -----
    A file that is not UTF-8 raises `UnicodeDecodeError`, which the caller
    records as that one file's content error rather than as an operational
    failure.
    """
    descriptor: int | None = None
    chunks: list[bytes] = []
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        _require_regular_entry(descriptor, path)
        while chunk := os.read(descriptor, _READ_CHUNK):
            chunks.append(chunk)
    except OSError as error:
        message = f"cannot read {path}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=path,
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return b"".join(chunks).decode("utf-8")


def _require_regular_entry(descriptor: int, path: pathlib.Path) -> None:
    """Refuse an opened entry that is not a regular file.

    The read is read-only, so a FIFO would block until a writer appeared and
    stall the whole audit. Checking the descriptor rather than the path also
    closes the window between the open and the check.

    Raises
    ------
    OperationalRuleError
        If the descriptor does not refer to a regular file.
    """
    if stat.S_ISREG(os.fstat(descriptor).st_mode):
        return
    message = f"cannot read {path}: not a regular file"
    raise OperationalRuleError(
        message,
        operation=OPERATION_READ_WORKFLOW,
        resource=path,
    )


def json_safe(value: object) -> object:
    """Convert YAML-only scalar values into JSON values for Conftest.

    `allow_nan` is off because Python's encoder emits `NaN` and `Infinity`
    for non-finite floats, which no JSON parser accepts. A workflow carrying
    `.nan` would otherwise be recorded as decoded and then fail the whole run
    when Conftest refused the document, turning one file's malformed content
    from evidence for an indeterminate verdict into an operational failure.

    A non-finite number, a circular reference and a value the encoder cannot
    reach through `str` all surface as the encoder's own `ValueError` or
    `TypeError`, which the caller records as that one file's content error.

    Returns
    -------
    object
        The value with YAML-only scalars converted to JSON ones.
    """
    return typ.cast(
        "object", json.loads(json.dumps(value, default=str, allow_nan=False))
    )


def load_workflow(
    checkout: pathlib.Path,
    relative: pathlib.PurePosixPath,
    *,
    is_link: bool,
    read_text: ReadText = read_text,
    json_safe: JsonSafe = json_safe,
) -> WorkflowFile:
    """Return one decoded workflow fact, retaining any content-level failure.

    *is_link* comes from the entry's one guarded `lstat` in
    `_workflow_entry_mode`. Asking the filesystem again here would be a second
    status query outside that guard, whose failure would escape as a raw
    `OSError` rather than the operational error the builder promises.

    Returns
    -------
    WorkflowFile
        The decoded document, or the content error that prevented decoding.
    """
    fact: WorkflowFile = {"path": str(relative), "parsed": None, "error": None}
    path = checkout / relative
    if is_link:
        fact["error"] = "workflow file is a symlink"
        return fact
    try:
        text = read_text(path)
    except UnicodeDecodeError as error:
        fact["error"] = f"not UTF-8 text: {error}"
        return fact
    try:
        parsed: object = _yaml.load(text)
    except (YAMLError, ValueError) as error:
        # An out-of-range timestamp scalar, such as `2026-02-30`, raises
        # `ValueError` from the safe loader's constructor rather than a
        # `YAMLError`. It is still a defect in this file's content, so it is
        # reported for this workflow instead of aborting the whole audit.
        fact["error"] = f"invalid YAML: {error}"
        return fact
    if not isinstance(parsed, dict):
        fact["error"] = "workflow document is not a mapping"
        return fact
    try:
        fact["parsed"] = json_safe(parsed)
    except (TypeError, ValueError) as error:
        fact["error"] = f"workflow document is not JSON-safe: {error}"
    return fact


def _list_workflow_directory(directory: pathlib.Path) -> list[pathlib.Path]:
    """Return the directory's entries in filename order, or raise.

    An absent directory is a repository with no workflows and yields an empty
    list. Every other failure — a path that is not a directory, an
    unreadable directory, an enumeration error, a symlink whose target is
    gone — is operational: reporting such a repository as compliant would
    clear the rule on the one shape it cannot see.

    A missing-file error is not by itself absence. A dangling symlink at
    `.github/workflows` raises the same error as no path at all, so the
    directory entry is checked without following it before absence is
    concluded.

    Returns
    -------
    list[pathlib.Path]
        Directory entries sorted by filename.

    Raises
    ------
    OperationalRuleError
        If the directory exists but cannot be enumerated.
    """
    try:
        return sorted(directory.iterdir(), key=lambda entry: entry.name)
    except FileNotFoundError as error:
        # The question here is whether anything is there, not whether it is
        # a file, so the probe's `read_error` decides rather than `present`.
        probe = probe_file(directory)
        if probe.read_error is None:
            return []
        message = f"cannot list {directory}: {probe.read_error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=directory,
        ) from error
    except OSError as error:
        message = f"cannot list {directory}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=directory,
        ) from error


def _workflow_entry_mode(entry: pathlib.Path) -> int | None:
    """Return a root workflow document's `lstat` mode, or None for any other entry.

    The mode is the entry's only status query: the caller reads whether it is
    a link from it rather than asking again.

    Returns
    -------
    int | None
        The mode of a regular file or symlink whose suffix is a workflow
        suffix, otherwise None.

    Raises
    ------
    OperationalRuleError
        If the entry's status cannot be read.
    """
    if entry.suffix not in WORKFLOW_SUFFIXES:
        return None
    try:
        mode = entry.lstat().st_mode
    except OSError as error:
        message = f"cannot stat {entry}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=entry,
        ) from error
    return mode if stat.S_ISREG(mode) or stat.S_ISLNK(mode) else None


def _contained_workflow_directory(checkout: pathlib.Path) -> pathlib.Path:
    """Return the checkout's workflow directory, refusing one outside it.

    A repository can ship `.github` or `.github/workflows` as a symlink. The
    audit reports on the checkout it was given, so a directory that resolves
    elsewhere is refused rather than read: its contents are not this
    repository's workflows, and recording them as such would let a link
    decide the verdict.

    Returns
    -------
    pathlib.Path
        The workflow directory, unresolved, for use in messages and paths.

    Raises
    ------
    OperationalRuleError
        If either path cannot be resolved, or the directory resolves outside
        the checkout.
    """
    directory = checkout / WORKFLOWS_DIRECTORY
    try:
        root = checkout.resolve(strict=False)
        resolved = directory.resolve(strict=False)
    except OSError as error:
        message = f"cannot resolve {directory}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=directory,
        ) from error
    if not resolved.is_relative_to(root):
        message = f"{directory} resolves outside the checkout, to {resolved}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=directory,
        )
    return directory


def load_workflows(
    checkout: pathlib.Path,
    *,
    read_text: ReadText = read_text,
    json_safe: JsonSafe = json_safe,
) -> list[WorkflowFile]:
    """Return every root workflow YAML file in stable filename order."""
    directory = _contained_workflow_directory(checkout)
    return [
        load_workflow(
            checkout,
            WORKFLOWS_DIRECTORY / entry.name,
            is_link=stat.S_ISLNK(mode),
            read_text=read_text,
            json_safe=json_safe,
        )
        for entry in _list_workflow_directory(directory)
        if (mode := _workflow_entry_mode(entry)) is not None
    ]
