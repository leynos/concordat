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


def _has_markdown_files(checkout: pathlib.Path) -> bool:
    """Return whether any Markdown file exists outside the pruned directories.

    The walk stops at the first match, and never follows symbolic links, so a
    checkout whose only Markdown is a link to another file (netsuke's
    `CRUSH.md`) is judged by the link's target being present in its own right.

    Returns
    -------
    bool
        Whether a governed Markdown file was found.
    """
    for root, directories, files in os.walk(checkout):
        directories[:] = sorted(
            name for name in directories if name not in PRUNED_DIRECTORIES
        )
        for name in files:
            candidate = pathlib.Path(root) / name
            if candidate.suffix.lower() in MARKDOWN_SUFFIXES and (
                candidate.is_file() and not candidate.is_symlink()
            ):
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


def _load_markdownlint_config(checkout: pathlib.Path) -> MarkdownlintConfig | None:
    """Return the decoded markdownlint configuration, or ``None`` if absent."""
    path = checkout / MARKDOWNLINT_CONFIG_FILENAME
    if not path.is_file():
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
    """Return the other markdownlint configuration file names present."""
    return [name for name in ALTERNATE_CONFIG_FILENAMES if (checkout / name).is_file()]


def _json_safe(value: object) -> object:
    """Return *value* with any non-JSON scalar (a YAML timestamp) stringified."""
    return typ.cast("object", json.loads(json.dumps(value, default=str)))


def _load_workflow(
    checkout: pathlib.Path, relative: pathlib.PurePosixPath
) -> WorkflowFile:
    """Return one decoded workflow file, or the file with its decoding error."""
    fact: WorkflowFile = {"path": str(relative), "parsed": None, "error": None}
    path = checkout / relative
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


def _load_workflows(checkout: pathlib.Path) -> list[WorkflowFile]:
    """Return every workflow file under `.github/workflows`, sorted by name."""
    directory = checkout / WORKFLOWS_DIRECTORY
    if not directory.is_dir():
        return []
    return [
        _load_workflow(checkout, WORKFLOWS_DIRECTORY / entry.name)
        for entry in sorted(directory.iterdir(), key=lambda entry: entry.name)
        if entry.is_file() and entry.suffix in WORKFLOW_SUFFIXES
    ]


def build_markdown_envelope(checkout: pathlib.Path) -> MarkdownEnvelope:
    """Assemble the Markdown formatting policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.

    An `OperationalRuleError` propagates from the fact readers if the root
    `Makefile` cannot be parsed by `makeutil`, or a fact file exists but
    cannot be opened.

    Returns
    -------
    MarkdownEnvelope
        The policy input document assembled from the checkout.
    """
    makefile_path = checkout / "Makefile"
    makefile_report: MakeutilReport | None = None
    if makefile_path.is_file():
        makefile_report = inspect_makefile(makefile_path).report
    markdownlint = _load_markdownlint_config(checkout)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "markdown_files": _has_markdown_files(checkout),
            "root_makefile": makefile_report is not None,
            "markdownlint_config": markdownlint is not None,
            "workflows_dir": (checkout / WORKFLOWS_DIRECTORY).is_dir(),
        },
        "makefile": makefile_report,
        "markdownlint": markdownlint,
        "alternate_markdownlint_configs": _alternate_configs(checkout),
        "workflows": _load_workflows(checkout),
    }
