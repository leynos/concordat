"""Build the workflow facts the pull-request cancellation policy evaluates.

The policy needs every root workflow document and the repository identity an
exemption is written against. The collector owns the decoding and the
containment checks; this module only names the envelope contract and reads the
`origin` remote.

The reader and JSON hooks are module-level so a test can substitute either
without changing the shared collector.
"""

from __future__ import annotations

import typing as typ

from .nested_cargo import origin_slug
from .workflow_evidence import OPERATION_READ_WORKFLOW as OPERATION_READ_WORKFLOW
from .workflow_evidence import WorkflowFile as WorkflowFile
from .workflow_evidence import json_safe as _json_safe
from .workflow_evidence import load_workflows as _load_root_workflows
from .workflow_evidence import read_text as _read_text

if typ.TYPE_CHECKING:
    import pathlib

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/pr-run-cancellation"


class Repository(typ.TypedDict):
    """Store the checkout identity carried by the policy envelope.

    Attributes
    ----------
    name:
        The repository's `owner/name` slug, read from the `origin` remote, or
        None when the checkout has no origin, its origin is not a GitHub URL,
        or git cannot open the repository. A checkout without a slug cannot
        match a named exemption and is fully audited.
    """

    name: str | None


class PrRunCancellationEnvelope(typ.TypedDict):
    """Store the workflow evidence evaluated by the cancellation rule.

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


def _load_workflows(checkout: pathlib.Path) -> list[WorkflowFile]:
    """Return every root workflow YAML file in stable filename order."""
    return _load_root_workflows(
        checkout,
        read_text=_read_text,
        json_safe=_json_safe,
    )


def build_pr_run_cancellation_envelope(
    checkout: pathlib.Path,
) -> PrRunCancellationEnvelope:
    """Assemble decoded workflow evidence for one checkout's cancellation rule.

    Parameters
    ----------
    checkout:
        The repository checkout to read workflows from.

    An `OperationalRuleError` reaches the caller when the workflow directory
    resolves outside the checkout, or when any part of reading it fails. An
    absent directory is not a failure: it is a repository with no workflows.

    Returns
    -------
    PrRunCancellationEnvelope
        Every root workflow document, decoded or carrying its content error,
        and the repository slug an exemption is matched against.
    """
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"name": origin_slug(checkout)},
        "workflows": _load_workflows(checkout),
    }


def build_rule_envelope(
    checkout: pathlib.Path,
    _parameters: object = None,
) -> PrRunCancellationEnvelope:
    """Build the envelope with the registry's builder signature.

    The rule reads its exemptions and severity through `data.parameters` in
    the policy, so the second argument exists only to give every registry
    builder one callable type.

    Returns
    -------
    PrRunCancellationEnvelope
        The `policy-input/pr-run-cancellation` document for *checkout*.
    """
    return build_pr_run_cancellation_envelope(checkout)
