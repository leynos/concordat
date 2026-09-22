"""Build decoded GitHub Actions workflow facts for a coverage policy.

The policy evaluates the same finite workflow documents an operator can see in
a checkout. A document that cannot be decoded remains a fact with its error,
so the policy can produce an indeterminate verdict instead of silently treating
the workflow as absent.
"""

from __future__ import annotations

import json
import pathlib
import stat
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .fs_probe import probe_file

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/main-owned-codescene-coverage"
WORKFLOWS_DIRECTORY: typ.Final = pathlib.PurePosixPath(".github/workflows")
WORKFLOW_SUFFIXES: typ.Final = frozenset({".yml", ".yaml"})
OPERATION_READ_WORKFLOW: typ.Final = "read-workflow"

_yaml = YAML(typ="safe")


class Repository(typ.TypedDict):
    """Store the checkout identity carried by a policy envelope.

    Attributes
    ----------
    path:
        The audited checkout's path, as the caller supplied it.
    name:
        The repository's name where one is known, otherwise None. A local
        audit is given a directory rather than a slug, so this is None today
        and reserved for a caller that knows better.
    """

    path: str
    name: str | None


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


class CoverageEnvelope(typ.TypedDict):
    """Store the workflow evidence evaluated by the CodeScene coverage rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version. The policy refuses an envelope whose
        version it does not know rather than reading it optimistically.
    kind:
        The policy-input identifier, which the policy checks so another
        package's facts cannot be evaluated by this one.
    repository:
        The audited checkout's identity.
    workflows:
        Every root workflow document, in filename order, each decoded or
        carrying the reason it could not be.
    """

    schema_version: int
    kind: str
    repository: Repository
    workflows: list[WorkflowFile]


def _read_text(path: pathlib.Path) -> str:
    """Read a workflow as UTF-8 text, or raise an operational error."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        message = f"cannot read {path}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=path,
        ) from error


def _json_safe(value: object) -> object:
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


def _load_workflow(
    checkout: pathlib.Path, relative: pathlib.PurePosixPath
) -> WorkflowFile:
    """Return one decoded workflow fact, retaining any content-level failure."""
    fact: WorkflowFile = {"path": str(relative), "parsed": None, "error": None}
    path = checkout / relative
    if path.is_symlink():
        fact["error"] = "workflow file is a symlink"
        return fact
    try:
        text = _read_text(path)
    except UnicodeDecodeError as error:
        fact["error"] = f"not UTF-8 text: {error}"
        return fact
    try:
        parsed: object = _yaml.load(text)
    except YAMLError as error:
        fact["error"] = f"invalid YAML: {error}"
        return fact
    if not isinstance(parsed, dict):
        fact["error"] = "workflow document is not a mapping"
        return fact
    try:
        fact["parsed"] = _json_safe(parsed)
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


def _is_workflow_file(entry: pathlib.Path) -> bool:
    """Return whether one directory entry is a root workflow document.

    Returns
    -------
    bool
        True for a regular file or symlink whose suffix is a workflow suffix.

    Raises
    ------
    OperationalRuleError
        If the entry's status cannot be read.
    """
    if entry.suffix not in WORKFLOW_SUFFIXES:
        return False
    try:
        mode = entry.lstat().st_mode
    except OSError as error:
        message = f"cannot stat {entry}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_READ_WORKFLOW,
            resource=entry,
        ) from error
    return stat.S_ISREG(mode) or stat.S_ISLNK(mode)


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


def _load_workflows(checkout: pathlib.Path) -> list[WorkflowFile]:
    """Return every root workflow YAML file in stable filename order."""
    directory = _contained_workflow_directory(checkout)
    return [
        _load_workflow(checkout, WORKFLOWS_DIRECTORY / entry.name)
        for entry in _list_workflow_directory(directory)
        if _is_workflow_file(entry)
    ]


def build_codescene_coverage_envelope(checkout: pathlib.Path) -> CoverageEnvelope:
    """Assemble decoded workflow evidence for one checkout's coverage policy.

    Parameters
    ----------
    checkout:
        The repository checkout to read workflows from.

    An `OperationalRuleError` reaches the caller when the workflow directory
    resolves outside the checkout, or when any part of reading it fails. An
    absent directory is not a failure: it is a repository with no workflows.

    Returns
    -------
    CoverageEnvelope
        Every root workflow document, decoded or carrying its content error.
    """
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "workflows": _load_workflows(checkout),
    }
