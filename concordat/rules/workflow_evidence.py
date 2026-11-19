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

from .fs_probe import probe_any

WORKFLOWS_DIRECTORY: typ.Final = pathlib.PurePosixPath(".github/workflows")
WORKFLOW_SUFFIXES: typ.Final = frozenset({".yml", ".yaml"})
OPERATION_READ_WORKFLOW: typ.Final = "read-workflow"
_READ_CHUNK: typ.Final = 65536
# These flags exist only on POSIX. The rule registry imports every rule module
# on every platform the package ships to, so a direct attribute read here
# would fail the import on Windows and take the whole registry with it. The
# missing flags become zero, and `_require_supported_platform` refuses a host
# that lacks them before any of the reader's guarantees depend on them.
_O_NOFOLLOW: typ.Final = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY: typ.Final = getattr(os, "O_DIRECTORY", 0)
_O_NONBLOCK: typ.Final = getattr(os, "O_NONBLOCK", 0)
_O_PATH: typ.Final = getattr(os, "O_PATH", 0)
_ENTRY_FLAGS: typ.Final = os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK
_DIRECTORY_FLAGS: typ.Final = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW
# An intermediate component is only traversed, never listed or read. `O_PATH`
# needs no read permission and cannot be used for I/O, which is exactly that
# role. A platform without it falls back to an ordinary directory open.
_SEARCH_FLAGS: typ.Final = (_O_PATH or os.O_RDONLY) | _O_DIRECTORY | _O_NOFOLLOW

# Captured once, while `os` still holds the real functions: tests and callers
# replace `os.open` or `os.stat`, and a membership test after that substitution
# would misread the platform as unsupported.
_SUPPORTS_DESCRIPTOR_WALK: typ.Final = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.listdir in os.supports_fd
    and not any(value == 0 for value in (_O_NOFOLLOW, _O_DIRECTORY, _O_NONBLOCK))
)


def _require_supported_platform(operation: str) -> None:
    """Refuse a host without the POSIX calls this reader's guarantees need.

    Every read, status query and directory listing is made relative to a
    descriptor so that a checkout changed during the audit cannot decide what
    the rule sees. A platform without `dir_fd` support, or without
    `O_NOFOLLOW`, `O_DIRECTORY` and `O_NONBLOCK`, can only offer the weaker
    pathname walk, so the audit fails as unsupported instead of silently
    dropping the guarantee.

    Raises
    ------
    OperationalRuleError
        If the host cannot make descriptor-relative filesystem calls.
    """
    if _SUPPORTS_DESCRIPTOR_WALK:
        return
    message = (
        "cannot read workflows: this platform lacks the POSIX "
        "descriptor-relative calls and open flags that keep the walk inside "
        "the checkout"
    )
    raise OperationalRuleError(
        message,
        operation=operation,
        resource=str(WORKFLOWS_DIRECTORY),
    )


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


class WorkflowReaders(typ.NamedTuple):
    """The two hooks a caller may substitute when decoding one workflow.

    They travel together because every caller that overrides one keeps its own
    module-level pair, and bundling them keeps this loader's parameters within
    the argument budget the code-health gate allows.
    """

    read_text: ReadText
    json_safe: JsonSafe


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
    _require_supported_platform(OPERATION_READ_WORKFLOW)
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


WORKFLOW_READERS: typ.Final = WorkflowReaders(read_text=read_text, json_safe=json_safe)


def load_workflow(
    checkout: pathlib.Path,
    relative: pathlib.PurePosixPath,
    *,
    is_link: bool,
    readers: WorkflowReaders = WORKFLOW_READERS,
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
        text = readers.read_text(path)
    except UnicodeDecodeError as error:
        fact["error"] = f"not UTF-8 text: {error}"
        return fact
    return _decode_into(fact, text, readers.json_safe)


def _decode_into(fact: WorkflowFile, text: str, json_safe: JsonSafe) -> WorkflowFile:
    """Fill *fact* from decoded YAML text, recording any content failure.

    Returns
    -------
    WorkflowFile
        The same fact, carrying the document or the reason it was rejected.
    """
    try:
        # ruamel does not guarantee that a loader which raised can handle
        # further input, so each document gets its own. A shared instance would
        # let the first malformed file decide every later file's verdict.
        parsed: object = YAML(typ="safe").load(text)
    except (YAMLError, ValueError, TypeError) as error:
        # An out-of-range timestamp scalar, such as `2026-02-30`, raises
        # `ValueError` from the safe loader's constructor rather than a
        # `YAMLError`. An `!!omap` whose key is a sequence raises `TypeError`,
        # because the loader tries to build a Python mapping keyed by a list.
        # Both are still defects in this file's content, so they are reported
        # for this workflow instead of aborting the whole audit.
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
    identity:
        The checkout root's `(device, inode)` pair recorded while the
        containment was checked, or None when the root was already absent.
        The walk compares it with the descriptor it opens, so a root replaced
        between the check and the open is refused rather than read.
    """

    path: pathlib.Path
    root: pathlib.Path
    parts: tuple[str, ...]
    identity: tuple[int, int] | None


def _entry_identity(path: pathlib.Path) -> tuple[int, int] | None:
    """Return *path*'s `(device, inode)` pair, or None when it cannot be read.

    A status failure is not reported here. The open that follows makes the
    same call against the same path, so an unreadable checkout still fails
    with the operation and resource its caller expects; answering None only
    skips the race check for this one walk rather than treating an
    unreadable tree as empty.

    Returns
    -------
    tuple[int, int] | None
        The entry's device and inode numbers, or None when it cannot be
        stat'ed.
    """
    try:
        status = path.stat()
    except OSError:
        return None
    return (status.st_dev, status.st_ino)


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
        identity = _entry_identity(root)
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
    return _ContainedDirectory(
        directory,
        root,
        resolved.relative_to(root).parts,
        identity,
    )


def _require_contained_root(location: _ContainedDirectory, descriptor: int) -> None:
    """Refuse a checkout root replaced after its containment was checked.

    The containment decision compares the resolved workflow directory with the
    resolved checkout, and both come from pathnames. Opening the checkout
    afterwards can land on a different tree if the path was replaced in
    between, and the decision would then describe a tree the audit never
    opened. Comparing the opened descriptor with the identity recorded during
    resolution binds the decision and the walk to the same object.

    Raises
    ------
    OperationalRuleError
        If the opened root is not the checkout that was resolved.
    """
    if location.identity is None:
        return
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) == location.identity:
        return
    message = (
        f"{location.path}: the checkout was replaced after its containment was "
        "checked, so the workflows found there are not the ones that were checked"
    )
    raise OperationalRuleError(
        message,
        operation=OPERATION_READ_WORKFLOW,
        resource=location.path,
    )


def _open_workflow_directory(location: _ContainedDirectory) -> int | None:
    """Open the contained workflow directory, or report it as absent.

    The descriptor is acquired by walking from the resolved checkout, opening
    each component relative to the one before it. The opened root is first
    matched against the identity recorded when it was resolved, and nothing
    afterwards resolves the directory's pathname again, so a checkout that
    replaces it cannot redirect enumeration or a read into another tree.

    Returns
    -------
    int | None
        A descriptor for the directory, or None when the directory does not
        exist. A component that cannot be opened, or a missing one that is a
        broken link rather than absence, raises from `_absent_or_refused`.

    Raises
    ------
    OperationalRuleError
        If a component resolves outside the checkout or the opened root is no
        longer the directory that was resolved.
    """
    flags = _SEARCH_FLAGS if location.parts else _DIRECTORY_FLAGS
    try:
        descriptor = os.open(location.root, flags)
    except OSError as error:
        return _absent_or_refused(location, error)
    try:
        _require_contained_root(location, descriptor)
        last = len(location.parts) - 1
        for index, part in enumerate(location.parts):
            descriptor = _descend(descriptor, part, is_target=index == last)
    except OSError as error:
        os.close(descriptor)
        return _absent_or_refused(location, error)
    except OperationalRuleError:
        os.close(descriptor)
        raise
    return descriptor


def _descend(parent: int, part: str, *, is_target: bool) -> int:
    """Open *part* below *parent*, closing the parent once the child is open.

    Only the final component is listed, so only it needs a readable
    descriptor. `.github` and any other intermediate component are opened
    search-only, which neither requires nor grants read permission on them.

    Closing the parent only after the child is open leaves the caller exactly
    one descriptor to release when the open fails.

    Returns
    -------
    int
        A descriptor for the opened component.
    """
    flags = _DIRECTORY_FLAGS if is_target else _SEARCH_FLAGS
    child = os.open(part, flags, dir_fd=parent)
    os.close(parent)
    return child


def _absent_or_refused(location: _ContainedDirectory, error: OSError) -> None:
    """Return None for genuine absence, or raise for a directory that is there.

    A missing-file error is not by itself absence. A dangling symlink at
    `.github/workflows`, or at an ancestor, raises the same error as no path
    at all, so the directory entry is probed without following it before
    absence is concluded. The probe runs after the open failed, so a checkout
    another process can write to may have occupied the path in between; only
    a probe that finds nothing at all is an absence.

    Raises
    ------
    OperationalRuleError
        If the directory is present but cannot be opened, is a link that does
        not resolve, or was occupied after the open failed.
    """
    if not isinstance(error, FileNotFoundError):
        message = f"cannot list {location.path}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=location.path,
        ) from error
    # The question here is whether anything is there, not whether it is a
    # directory, so `probe_any` answers it: absence is the case where nothing
    # is present and nothing refused to be described. A regular file that
    # appeared after the open failed is present, and reporting it as absence
    # would call a repository with unreadable workflows compliant.
    probe = probe_any(location.path)
    if probe.read_error is None and not probe.present:
        return
    reason = probe.read_error or f"{location.path}: an entry is present"
    message = f"cannot list {location.path}: {reason}"
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


class _RootWorkflowEntry(typ.NamedTuple):
    """One enumerated root workflow entry: its name, path and lstat mode."""

    name: str
    path: pathlib.Path
    mode: int


def _root_workflow_entries(
    directory_fd: int, path: pathlib.Path
) -> cabc.Iterator[_RootWorkflowEntry]:
    """Yield the directory's root workflow entries in filename order.

    Selection is separated from fact construction so `load_workflows` can
    consume the eligible entries without a mutable accumulator. Each entry's
    status is read once, here, and travels with the entry.

    Yields
    ------
    _RootWorkflowEntry
        Each eligible root entry, in filename order.
    """
    for name in _list_entries(directory_fd, path):
        entry_path = path / name
        mode = _workflow_entry_mode(directory_fd, name, entry_path)
        if mode is not None:
            yield _RootWorkflowEntry(name=name, path=entry_path, mode=mode)


def _root_workflow_fact(
    directory_fd: int,
    entry: _RootWorkflowEntry,
    json_safe: JsonSafe,
) -> WorkflowFile:
    """Return one root workflow fact, retaining any content-level failure."""
    fact: WorkflowFile = {
        "path": str(WORKFLOWS_DIRECTORY / entry.name),
        "parsed": None,
        "error": None,
    }
    if stat.S_ISLNK(entry.mode):
        fact["error"] = "workflow file is a symlink"
        return fact
    try:
        text = _read_entry(directory_fd, entry.name, entry.path)
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
    _require_supported_platform(OPERATION_READ_WORKFLOW)
    location = _contained_workflow_directory(checkout)
    with _bound_workflow_directory(location) as directory_fd:
        if directory_fd is None:
            return []
        return [
            _root_workflow_fact(directory_fd, entry, json_safe)
            for entry in _root_workflow_entries(directory_fd, location.path)
        ]
