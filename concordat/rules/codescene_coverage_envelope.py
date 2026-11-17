"""Build decoded GitHub Actions workflow facts for a coverage policy.

The policy evaluates the same finite workflow documents an operator can see in
a checkout. A document that cannot be decoded remains a fact with its error,
so the policy can produce an indeterminate verdict instead of silently treating
the workflow as absent.

The collector itself lives in `workflow_evidence`, shared with the other rule
packages that read workflows. The wrappers below keep this module's historical
private names, which other envelopes import, and route them through the
module-level reader and JSON hooks so a test can substitute either here.
"""

from __future__ import annotations

import typing as typ

from .workflow_evidence import OPERATION_READ_WORKFLOW as OPERATION_READ_WORKFLOW
from .workflow_evidence import WorkflowFile as WorkflowFile
from .workflow_evidence import json_safe as _json_safe
from .workflow_evidence import load_workflow as _load_single_workflow
from .workflow_evidence import load_workflows as _load_root_workflows
from .workflow_evidence import read_text as _read_text

if typ.TYPE_CHECKING:
    import pathlib

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/main-owned-codescene-coverage"


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


def _load_workflow(
    checkout: pathlib.Path, relative: pathlib.PurePosixPath, *, is_link: bool
) -> WorkflowFile:
    """Return one decoded workflow fact through this module's reader hooks.

    Returns
    -------
    WorkflowFile
        The decoded document, or the content error that prevented decoding.
    """
    return _load_single_workflow(
        checkout,
        relative,
        is_link=is_link,
        read_text=_read_text,
        json_safe=_json_safe,
    )


def _load_workflows(checkout: pathlib.Path) -> list[WorkflowFile]:
    """Return every root workflow YAML file in stable filename order."""
    return _load_root_workflows(
        checkout,
        read_text=_read_text,
        json_safe=_json_safe,
    )


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
