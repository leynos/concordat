"""Read the root GitHub Actions workflow documents shared by several policies.

A repository's workflows are evidence for more than one canon rule package. The
reader here owns the part every such package must get right: enumerate only
`.github/workflows` itself, refuse a directory that resolves outside the
checkout, decode each document without following a symlink, and retain a
document that could not be decoded as a fact carrying its reason. A policy can
then return an indeterminate verdict for that one file instead of treating it
as absent.

Root workflows are enumerated and opened through a descriptor acquired once,
so a checkout that changes while an audit runs cannot substitute another tree
for the one the containment check accepted.

The callable that reads a single workflow and the one that converts a decoded
value to JSON are parameters so a caller can keep its own module-level hooks.
Existing policies do that to substitute a reader in a test without changing
the collector.
"""

from __future__ import annotations

import contextlib
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
_ENTRY_FLAGS: typ.Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_DIRECTORY_FLAGS: typ.Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

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
    try:
        descriptor = os.open(path, _ENTRY_FLAGS)
    except OSError as error:
        message = f"cannot read {path}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=path,
        ) from error
    return _read_open_entry(descriptor, path)


def _read_entry(directory_fd: int, name: str, path: pathlib.Path) -> str:
    """Read one workflow entry relative to its open directory descriptor.

    Opening through the descriptor keeps the read inside the directory the
    containment check accepted. Reopening the directory's pathname here would
    let a checkout that is replaced after that check decide what is read.

    Parameters
    ----------
    directory_fd:
        The descriptor of the directory holding the entry.
    name:
        The entry's filename within that directory.
    path:
        The entry's checkout-relative path, for messages.

    Returns
    -------
    str
        The entry's decoded contents.

    Raises
    ------
    OperationalRuleError
        If the entry cannot be opened or read, including when it is a symlink
        or the opened entry is not a regular file.

    Notes
    -----
    A file that is not UTF-8 raises `UnicodeDecodeError`, as `read_text` does.
    """
    try:
        descriptor = os.open(name, _ENTRY_FLAGS, dir_fd=directory_fd)
    except OSError as error:
        message = f"cannot read {path}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=path,
        ) from error
    return _read_open_entry(descriptor, path)


def _read_open_entry(descriptor: int, path: pathlib.Path) -> str:
    """Read an opened workflow entry, refusing a non-regular file.

    The descriptor is always closed, including when the read is refused.

    Returns
    -------
    str
        The entry's decoded contents.

    Raises
    ------
    OperationalRuleError
        If the entry cannot be read, including when it is not a regular file.
    """
    chunks: list[bytes] = []
    try:
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
    return _decode_into(fact, text, json_safe)


def _decode_into(fact: WorkflowFile, text: str, json_safe: JsonSafe) -> WorkflowFile:
    """Fill *fact* from decoded YAML text, recording any content failure.

    Returns
    -------
    WorkflowFile
        The same fact, carrying the document or the reason it was rejected.
    """
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


class _ContainedDirectory(typ.NamedTuple):
    """Locate the workflow directory and the route to it from the checkout.

    Attributes
    ----------
    path:
        The directory as the checkout names it, for messages and fact paths.
    root:
        The checkout's resolved path, which the walk to the directory starts
        from.
    parts:
        The resolved directory's components below *root*, in order.
    """

    path: pathlib.Path
    root: pathlib.Path
    parts: tuple[str, ...]


def _contained_workflow_directory(checkout: pathlib.Path) -> _ContainedDirectory:
    """Return the checkout's workflow directory, refusing one outside it.

    A repository can ship `.github` or `.github/workflows` as a symlink. The
    audit reports on the checkout it was given, so a directory that resolves
    elsewhere is refused rather than read: its contents are not this
    repository's workflows, and recording them as such would let a link
    decide the verdict.

    Returns
    -------
    _ContainedDirectory
        The directory's logical path, the resolved checkout, and the resolved
        components below it.

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
    return _ContainedDirectory(directory, root, resolved.relative_to(root).parts)


def _open_workflow_directory(location: _ContainedDirectory) -> int | None:
    """Open the contained workflow directory, or report it as absent.

    The descriptor is acquired by walking from the resolved checkout, opening
    each component relative to the one before it. Nothing afterwards resolves
    the directory's pathname again, so a checkout that replaces it cannot
    redirect enumeration or a read into another tree.

    Returns
    -------
    int | None
        A descriptor for the directory, or None when the directory does not
        exist. A component that cannot be opened, or a missing one that is a
        broken link rather than absence, raises from `_absent_or_refused`.
    """
    try:
        descriptor = os.open(location.root, _DIRECTORY_FLAGS)
    except OSError as error:
        return _absent_or_refused(location, error)
    try:
        for part in location.parts:
            descriptor = _descend(descriptor, part)
    except OSError as error:
        os.close(descriptor)
        return _absent_or_refused(location, error)
    return descriptor


def _descend(parent: int, part: str) -> int:
    """Open *part* below *parent*, closing the parent once the child is open.

    Closing the parent only after the child is open leaves the caller exactly
    one descriptor to release when the open fails.

    Returns
    -------
    int
        A descriptor for the opened component.
    """
    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=parent)
    os.close(parent)
    return child


def _absent_or_refused(location: _ContainedDirectory, error: OSError) -> None:
    """Return None for genuine absence, or raise for a directory that is there.

    A missing-file error is not by itself absence. A dangling symlink at
    `.github/workflows`, or at an ancestor, raises the same error as no path
    at all, so the directory entry is probed without following it before
    absence is concluded.

    Raises
    ------
    OperationalRuleError
        If the directory is present but cannot be opened, or is a link that
        does not resolve.
    """
    if not isinstance(error, FileNotFoundError):
        message = f"cannot list {location.path}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=location.path,
        ) from error
    # The question here is whether anything is there, not whether it is a
    # file, so the probe's `read_error` decides rather than `present`.
    probe = probe_file(location.path)
    if probe.read_error is None:
        return
    message = f"cannot list {location.path}: {probe.read_error}"
    raise OperationalRuleError(
        message,
        operation=OPERATION_READ_WORKFLOW,
        resource=location.path,
    ) from error


@contextlib.contextmanager
def _bound_workflow_directory(
    location: _ContainedDirectory,
) -> cabc.Iterator[int | None]:
    """Yield the open workflow directory descriptor, or None when absent."""
    descriptor = _open_workflow_directory(location)
    try:
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _list_entries(directory_fd: int, path: pathlib.Path) -> list[str]:
    """Return the directory's entry names in filename order, or raise.

    Returns
    -------
    list[str]
        The directory's entry names, sorted.

    Raises
    ------
    OperationalRuleError
        If the directory cannot be enumerated.
    """
    try:
        return sorted(os.listdir(directory_fd))
    except OSError as error:
        message = f"cannot list {path}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=path,
        ) from error


def _workflow_entry_mode(
    directory_fd: int, name: str, path: pathlib.Path
) -> int | None:
    """Return a root workflow document's status mode, or None for any other entry.

    The status is read relative to the open directory without following the
    entry, so the mode describes the one entry the listing named. It is the
    entry's only status query: the caller reads whether it is a link from it
    rather than asking again.

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
    if path.suffix not in WORKFLOW_SUFFIXES:
        return None
    try:
        mode = os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
    except OSError as error:
        message = f"cannot stat {path}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=path,
        ) from error
    return mode if stat.S_ISREG(mode) or stat.S_ISLNK(mode) else None


def _root_workflow_fact(
    directory_fd: int,
    name: str,
    path: pathlib.Path,
    mode: int,
    json_safe: JsonSafe,
) -> WorkflowFile:
    """Return one root workflow fact, retaining any content-level failure."""
    fact: WorkflowFile = {
        "path": str(WORKFLOWS_DIRECTORY / name),
        "parsed": None,
        "error": None,
    }
    if stat.S_ISLNK(mode):
        fact["error"] = "workflow file is a symlink"
        return fact
    try:
        text = _read_entry(directory_fd, name, path)
    except UnicodeDecodeError as error:
        fact["error"] = f"not UTF-8 text: {error}"
        return fact
    return _decode_into(fact, text, json_safe)


def load_workflows(
    checkout: pathlib.Path,
    *,
    json_safe: JsonSafe = json_safe,
) -> list[WorkflowFile]:
    """Return every root workflow YAML file in stable filename order.

    The directory is opened once, after the containment check, and every entry
    is then listed, classified and read relative to that descriptor. Resolving
    or reopening the pathname later would let a checkout that changes during
    the audit decide what the rule reads.

    Returns
    -------
    list[WorkflowFile]
        Every root workflow document, decoded or carrying its content error,
        in filename order.
    """
    location = _contained_workflow_directory(checkout)
    with _bound_workflow_directory(location) as directory_fd:
        if directory_fd is None:
            return []
        facts: list[WorkflowFile] = []
        for name in _list_entries(directory_fd, location.path):
            path = location.path / name
            mode = _workflow_entry_mode(directory_fd, name, path)
            if mode is not None:
                facts.append(
                    _root_workflow_fact(directory_fd, name, path, mode, json_safe)
                )
        return facts
