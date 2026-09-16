"""Build decoded GitHub Actions workflow facts for a coverage policy.

The policy evaluates the same finite workflow documents an operator can see in
a checkout. A document that cannot be decoded remains a fact with its error,
so the policy can produce an indeterminate verdict instead of silently treating
the workflow as absent.
"""

from __future__ import annotations

import json
import pathlib
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/main-owned-codescene-coverage"
WORKFLOWS_DIRECTORY: typ.Final = pathlib.PurePosixPath(".github/workflows")
WORKFLOW_SUFFIXES: typ.Final = frozenset({".yml", ".yaml"})
OPERATION_READ_WORKFLOW: typ.Final = "read-workflow"

_yaml = YAML(typ="safe")


class Repository(typ.TypedDict):
    """Store the checkout identity carried by a policy envelope."""

    path: str
    name: str | None


class WorkflowFile(typ.TypedDict):
    """Store one workflow document or the reason it could not be decoded."""

    path: str
    parsed: object | None
    error: str | None


class CoverageEnvelope(typ.TypedDict):
    """Store the workflow evidence evaluated by the CodeScene coverage rule."""

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
    """Convert YAML-only scalar values into JSON values for Conftest."""
    return typ.cast("object", json.loads(json.dumps(value, default=str)))


def _load_workflow(
    checkout: pathlib.Path, relative: pathlib.PurePosixPath
) -> WorkflowFile:
    """Return one decoded workflow fact, retaining any content-level failure."""
    fact: WorkflowFile = {"path": str(relative), "parsed": None, "error": None}
    path = checkout / relative
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
    fact["parsed"] = _json_safe(parsed)
    return fact


def _load_workflows(checkout: pathlib.Path) -> list[WorkflowFile]:
    """Return every root workflow YAML file in stable filename order."""
    directory = checkout / WORKFLOWS_DIRECTORY
    if not directory.is_dir():
        return []
    return [
        _load_workflow(checkout, WORKFLOWS_DIRECTORY / entry.name)
        for entry in sorted(directory.iterdir(), key=lambda entry: entry.name)
        if entry.is_file() and entry.suffix in WORKFLOW_SUFFIXES
    ]


def build_codescene_coverage_envelope(checkout: pathlib.Path) -> CoverageEnvelope:
    """Assemble decoded workflow evidence for one checkout's coverage policy."""
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "workflows": _load_workflows(checkout),
    }
