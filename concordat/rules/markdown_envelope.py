"""Build the `policy-input/markdown-formatting-baseline` envelope.

The Markdown formatting rule package reasons over three kinds of fact: the
root `Makefile` (parsed by the pinned `makeutil`, exactly as the Rust
envelope does), the `.markdownlint-cli2.jsonc` configuration, and every
GitHub Actions workflow under `.github/workflows`. Each fact is recorded as it
was found: a file that exists but cannot be decoded is carried with its
`error` rather than dropped, so the policy can report an indeterminate
verdict instead of silently treating the file as absent.
"""

from __future__ import annotations

import json
import os
import pathlib
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .jsonc import JsoncError, loads_jsonc
from .makefile_facts import MakeutilReport, inspect_makefile

if typ.TYPE_CHECKING:
    import collections.abc as cabc

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/markdown-formatting-baseline"

MARKDOWNLINT_CONFIG_FILENAME: typ.Final = ".markdownlint-cli2.jsonc"
# Every other configuration file name markdownlint-cli2 would honour. The
# baseline mandates the JSONC spelling, so these are reported as facts for the
# policy to name in its finding rather than silently accepted as equivalent.
ALTERNATE_CONFIG_FILENAMES: typ.Final = (
    ".markdownlint-cli2.yaml",
    ".markdownlint-cli2.cjs",
    ".markdownlint-cli2.mjs",
    ".markdownlint.jsonc",
    ".markdownlint.json",
    ".markdownlint.yaml",
    ".markdownlint.yml",
)
WORKFLOWS_DIRECTORY: typ.Final = pathlib.PurePosixPath(".github/workflows")
WORKFLOW_SUFFIXES: typ.Final = frozenset({".yml", ".yaml"})
# Extensions mdtablefix selects by default under `--git`.
MARKDOWN_SUFFIXES: typ.Final = frozenset({".md", ".mdc", ".markdown"})
# Directories that never hold governed prose: version-control internals,
# virtual environments, dependency and build trees, and tool caches.
PRUNED_DIRECTORIES: typ.Final = frozenset({
    ".git",
    ".venv",
    "node_modules",
    "target",
    ".uv-cache",
    ".uv-tools",
    ".terraform",
    ".vtcode",
    "memories",
    "__pycache__",
    ".pytest_cache",
})

OPERATION_READ_MARKDOWNLINT_CONFIG: typ.Final = "read-markdownlint-config"
OPERATION_READ_WORKFLOW: typ.Final = "read-workflow"
OPERATION_SCAN_MARKDOWN: typ.Final = "scan-markdown-files"
OPERATION_RESOLVE_CHECKOUT: typ.Final = "resolve-checkout"
OPERATION_LIST_WORKFLOWS: typ.Final = "list-workflows"
OPERATION_PROBE_PATH: typ.Final = "probe-path"
OPERATION_READ_MAKEFILE: typ.Final = "read-makefile"

_yaml = YAML(typ="safe")


class MarkdownlintConfig(typ.TypedDict):
    """The `.markdownlint-cli2.jsonc` file, decoded or carrying its error."""

    path: str
    parsed: object | None
    error: str | None


class WorkflowFile(typ.TypedDict):
    """One GitHub Actions workflow file, decoded or carrying its error."""

    path: str
    parsed: object | None
    error: str | None


class MarkdownApplicability(typ.TypedDict):
    """Evidence that the Markdown formatting rule package applies."""

    markdown_files: bool
    root_makefile: bool
    markdownlint_config: bool
    workflows_dir: bool


class Repository(typ.TypedDict):
    """Repository identity carried by the envelope."""

    path: str
    name: str | None


class MarkdownEnvelope(typ.TypedDict):
    """The `policy-input/markdown-formatting-baseline` document."""

    schema_version: int
    kind: str
    repository: Repository
    applicability: MarkdownApplicability
    makefile: MakeutilReport | None
    markdownlint: MarkdownlintConfig | None
    alternate_markdownlint_configs: list[str]
    workflows: list[WorkflowFile]


def _resolved_root(checkout: pathlib.Path) -> pathlib.Path:
    """Return the checkout's fully resolved path.

    Returns
    -------
    pathlib.Path
        The checkout with every symbolic link in its own path resolved.

    Raises
    ------
    OperationalRuleError
        If the checkout path itself cannot be resolved.
    """
    try:
        return checkout.resolve(strict=True)
    except OSError as error:
        message = f"cannot resolve checkout {checkout}: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION_RESOLVE_CHECKOUT, resource=checkout
        ) from error


def _within_checkout(root: pathlib.Path, path: pathlib.Path, operation: str) -> bool:
    """Return whether *path* resolves to a location inside *root*.

    Every policy input is read through this guard. The readers follow
    symbolic links, so without it a checkout could aim its `Makefile`,
    markdownlint configuration, or a workflow file at a readable file
    elsewhere on the machine and carry that file's contents into the
    envelope, which the audit then reports and may publish.

    Returns
    -------
    bool
        Whether the path exists and resolves inside the checkout. A path
        that does not exist at all returns ``False``.

    Raises
    ------
    OperationalRuleError
        If the path exists but resolves outside the checkout.
    """
    if not _probe(path.exists, path, operation):
        return False
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        message = f"cannot resolve {path}: {error}"
        raise OperationalRuleError(
            message, operation=operation, resource=path
        ) from error
    if resolved == root or resolved.is_relative_to(root):
        return True
    message = (
        f"{path} resolves to {resolved}, outside the checkout {root}; "
        "refusing to read a policy input from outside the audited tree"
    )
    raise OperationalRuleError(message, operation=operation, resource=path)


def _probe(
    predicate: cabc.Callable[[], bool], path: pathlib.Path, operation: str
) -> bool:
    """Return the result of a `pathlib` existence test, or raise.

    `Path.is_file`, `Path.is_dir`, and `Path.exists` answer ``False`` for an
    unreadable path as readily as for an absent one. An audit that cannot
    stat its own inputs must say so rather than record their absence, so the
    few `OSError`s those methods do propagate, and any they would swallow on
    a future Python, are translated here.

    Returns
    -------
    bool
        The predicate's answer for *path*.

    Raises
    ------
    OperationalRuleError
        If the path cannot be examined.
    """
    try:
        return predicate()
    except OSError as error:
        message = f"cannot examine {path}: {error}"
        raise OperationalRuleError(
            message, operation=operation, resource=path
        ) from error


def _raise_walk_error(error: OSError) -> typ.NoReturn:
    """Re-raise a directory-scan failure as an operational audit failure.

    `os.walk` swallows every `OSError` by default. A directory the audit
    cannot list would then contribute no Markdown files, and an unreadable
    tree would read as a repository with no governed prose: the rule would
    report `not-applicable` rather than admitting it could not look.

    Raises
    ------
    OperationalRuleError
        Always; the scan cannot continue over a directory it cannot read.
    """
    resource = pathlib.Path(error.filename) if error.filename else pathlib.Path()
    message = f"cannot scan {resource} for Markdown files: {error}"
    raise OperationalRuleError(
        message, operation=OPERATION_SCAN_MARKDOWN, resource=resource
    ) from error


def _has_markdown_files(checkout: pathlib.Path) -> bool:
    """Return whether any Markdown file exists outside the pruned directories.

    The walk stops at the first match, and never follows symbolic links, so a
    checkout whose only Markdown is a link to another file (netsuke's
    `CRUSH.md`) is judged by the link's target being present in its own right.
    A directory that cannot be listed raises rather than being skipped: an
    `OperationalRuleError` propagates from the walk's error callback.

    Returns
    -------
    bool
        Whether a governed Markdown file was found.
    """
    for root, directories, files in os.walk(checkout, onerror=_raise_walk_error):
        directories[:] = sorted(
            name for name in directories if name not in PRUNED_DIRECTORIES
        )
        for name in files:
            candidate = pathlib.Path(root) / name
            if candidate.suffix.lower() not in MARKDOWN_SUFFIXES:
                continue
            if not _probe(candidate.is_file, candidate, OPERATION_SCAN_MARKDOWN):
                continue
            if _probe(candidate.is_symlink, candidate, OPERATION_SCAN_MARKDOWN):
                continue
            return True
    return False


def _read_text(path: pathlib.Path, operation: str) -> str:
    """Return the file's text, or raise an operational error if unreadable.

    A text-decoding failure (`UnicodeDecodeError`) is deliberately left to
    propagate untranslated: undecodable bytes are a property of the file's
    content, which the caller records as a fact, whereas a file that cannot
    be opened at all is an audit failure.

    Returns
    -------
    str
        The file's text.

    Raises
    ------
    OperationalRuleError
        If the file cannot be opened or read.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        message = f"cannot read {path}: {error}"
        raise OperationalRuleError(
            message, operation=operation, resource=path
        ) from error


def _load_markdownlint_config(
    checkout: pathlib.Path, root: pathlib.Path
) -> MarkdownlintConfig | None:
    """Return the decoded markdownlint configuration, or ``None`` if absent.

    An `OperationalRuleError` propagates from the containment guard if the
    file resolves outside the checkout.

    Returns
    -------
    MarkdownlintConfig | None
        The configuration fact, or ``None`` when the file does not exist.
    """
    path = checkout / MARKDOWNLINT_CONFIG_FILENAME
    if not _within_checkout(root, path, OPERATION_READ_MARKDOWNLINT_CONFIG):
        return None
    if not _probe(path.is_file, path, OPERATION_READ_MARKDOWNLINT_CONFIG):
        return None
    fact: MarkdownlintConfig = {
        "path": MARKDOWNLINT_CONFIG_FILENAME,
        "parsed": None,
        "error": None,
    }
    try:
        text = _read_text(path, OPERATION_READ_MARKDOWNLINT_CONFIG)
    except UnicodeDecodeError as error:
        fact["error"] = f"not UTF-8 text: {error}"
        return fact
    try:
        fact["parsed"] = loads_jsonc(text)
    except JsoncError as error:
        fact["error"] = str(error)
    return fact


def _alternate_configs(checkout: pathlib.Path) -> list[str]:
    """Return the other markdownlint configuration file names present.

    An `OperationalRuleError` propagates if one of the candidate paths cannot
    be examined.

    Returns
    -------
    list[str]
        The alternate configuration file names the checkout carries.
    """
    present: list[str] = []
    for name in ALTERNATE_CONFIG_FILENAMES:
        candidate = checkout / name
        if _probe(candidate.is_file, candidate, OPERATION_PROBE_PATH):
            present.append(name)
    return present


def _json_safe(value: object) -> object:
    """Return *value* with any non-JSON scalar (a YAML timestamp) stringified."""
    return typ.cast("object", json.loads(json.dumps(value, default=str)))


def _load_workflow(
    checkout: pathlib.Path, root: pathlib.Path, relative: pathlib.PurePosixPath
) -> WorkflowFile:
    """Return one decoded workflow file, or the file with its decoding error.

    An `OperationalRuleError` propagates from the containment guard if the
    file resolves outside the checkout.

    Returns
    -------
    WorkflowFile
        The workflow fact, decoded or carrying its decoding error.
    """
    fact: WorkflowFile = {"path": str(relative), "parsed": None, "error": None}
    path = checkout / relative
    _within_checkout(root, path, OPERATION_READ_WORKFLOW)
    try:
        text = _read_text(path, OPERATION_READ_WORKFLOW)
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
    fact["parsed"] = _json_safe(parsed)
    return fact


def _load_workflows(checkout: pathlib.Path, root: pathlib.Path) -> list[WorkflowFile]:
    """Return every workflow file under `.github/workflows`, sorted by name.

    An `OperationalRuleError` propagates from the containment guard if the
    directory or any workflow file resolves outside the checkout.

    Returns
    -------
    list[WorkflowFile]
        One fact per workflow file, ordered by file name.

    Raises
    ------
    OperationalRuleError
        If the workflows directory exists but cannot be listed.
    """
    directory = checkout / WORKFLOWS_DIRECTORY
    if not _within_checkout(root, directory, OPERATION_READ_WORKFLOW):
        return []
    if not _probe(directory.is_dir, directory, OPERATION_LIST_WORKFLOWS):
        return []
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as error:
        message = f"cannot list {directory}: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION_LIST_WORKFLOWS, resource=directory
        ) from error
    return [
        _load_workflow(checkout, root, WORKFLOWS_DIRECTORY / entry.name)
        for entry in entries
        if _probe(entry.is_file, entry, OPERATION_LIST_WORKFLOWS)
        and entry.suffix in WORKFLOW_SUFFIXES
    ]


def build_markdown_envelope(checkout: pathlib.Path) -> MarkdownEnvelope:
    """Assemble the Markdown formatting policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.

    An `OperationalRuleError` propagates from the fact readers if the root
    `Makefile` cannot be parsed by `makeutil`, a fact file exists but cannot
    be opened, or a fact file resolves to a location outside the checkout.

    Returns
    -------
    MarkdownEnvelope
        The policy input document assembled from the checkout.
    """
    root = _resolved_root(checkout)
    workflows_dir = checkout / WORKFLOWS_DIRECTORY
    makefile_path = checkout / "Makefile"
    makefile_report: MakeutilReport | None = None
    if _within_checkout(root, makefile_path, OPERATION_READ_MAKEFILE) and _probe(
        makefile_path.is_file, makefile_path, OPERATION_READ_MAKEFILE
    ):
        makefile_report = inspect_makefile(makefile_path).report
    markdownlint = _load_markdownlint_config(checkout, root)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "markdown_files": _has_markdown_files(checkout),
            "root_makefile": makefile_report is not None,
            "markdownlint_config": markdownlint is not None,
            "workflows_dir": _probe(
                workflows_dir.is_dir, workflows_dir, OPERATION_PROBE_PATH
            ),
        },
        "makefile": makefile_report,
        "markdownlint": markdownlint,
        "alternate_markdownlint_configs": _alternate_configs(checkout),
        "workflows": _load_workflows(checkout, root),
    }
