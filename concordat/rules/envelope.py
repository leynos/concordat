"""Build the policy-input envelopes evaluated by rule-package policies.

One builder per rule package that needs different facts. `rust-makefile-
baseline` reads the Makefile through the pinned `makeutil`; `rust-build-
defaults` reads the files Cargo and rustup auto-discover, the document a
repository records its codegen-backend exception in, and the Makefile and
workflows whose builds assign `RUSTFLAGS` or select a backend.
"""

from __future__ import annotations

import logging
import typing as typ

from .cargo_config import CargoConfigFacts, inspect_cargo_config
from .exception_docs import DocumentScan, find_exception_sections
from .fs_probe import regular_file_exists
from .makefile_facts import (
    OPERATION_PARSE_MAKEFILE,
    MakefileRefusedError,
    MakeutilReport,
)
from .makefile_observed import inspect_makefile_observed
from .markdown_envelope import (
    is_file,
    load_workflows,
    resolved_root,
    within_checkout,
)
from .rust_surfaces import (
    CargoManifest,
    CargoSurface,
    resolve_rust_surfaces,
    root_cargo_toml_exists,
)
from .toolchain import ToolchainFacts, inspect_toolchain

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

_logger = logging.getLogger(__name__)

ENVELOPE_SCHEMA_VERSION: typ.Final = 1

# Prefixes of the shared reader's `WorkflowFile.error` text, in match order.
_DECODE_CATEGORIES: typ.Final = (
    ("not UTF-8 text", "not UTF-8 text"),
    ("invalid YAML", "invalid YAML"),
    ("workflow document is not a mapping", "not a mapping"),
)
ENVELOPE_KIND: typ.Final = "policy-input/rust-makefile-baseline"


class CargoPayload(typ.TypedDict):
    """The root compatibility marker and the resolved governed surfaces."""

    parsed: CargoManifest | None
    surfaces: list[CargoSurface]


class Applicability(typ.TypedDict):
    """Provisional evidence that a rule package applies to the checkout."""

    root_cargo_toml: bool
    root_makefile: bool
    rust_surfaces_declared: bool


class Repository(typ.TypedDict):
    """Repository identity carried by the envelope."""

    path: str
    name: str | None


class PolicyEnvelope(typ.TypedDict):
    """The `policy-input/rust-makefile-baseline` document sent to Conftest."""

    schema_version: int
    kind: str
    repository: Repository
    applicability: Applicability
    cargo: CargoPayload
    makefile: MakeutilReport | None


def build_envelope(checkout: pathlib.Path) -> PolicyEnvelope:
    """Assemble the policy input document for one local checkout.

    A `.concordat` `language.rust.surfaces` declaration is authoritative,
    including an empty list.  Repositories without that declaration retain
    the historic root-`Cargo.toml` compatibility fallback.

    Returns
    -------
    PolicyEnvelope
        The policy input document assembled from the checkout.
    """
    cargo_path = checkout / "Cargo.toml"
    makefile_path = checkout / "Makefile"

    root_cargo_toml = root_cargo_toml_exists(cargo_path)
    resolution = resolve_rust_surfaces(checkout)
    cargo_parsed = next(
        (
            surface["parsed"]
            for surface in resolution.surfaces
            if surface["path"] == "Cargo.toml"
        ),
        None,
    )

    makefile_report: MakeutilReport | None = None
    if regular_file_exists(makefile_path, operation=OPERATION_PARSE_MAKEFILE):
        makefile_report = inspect_makefile_observed(makefile_path).report

    envelope: PolicyEnvelope = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "root_cargo_toml": root_cargo_toml,
            "root_makefile": makefile_report is not None,
            "rust_surfaces_declared": resolution.declared,
        },
        "cargo": {"parsed": cargo_parsed, "surfaces": list(resolution.surfaces)},
        "makefile": makefile_report,
    }
    return envelope


BUILD_DEFAULTS_ENVELOPE_KIND: typ.Final = "policy-input/rust-build-defaults"

# Where a repository records a codegen-backend exception, and the word its
# heading has to contain. Both are rule parameters; these are the fallbacks
# used when a caller supplies none.
DEFAULT_EXCEPTION_DOCUMENTS: typ.Final = ("docs/developers-guide.md",)
DEFAULT_EXCEPTION_KEYWORD: typ.Final = "Cranelift"


class BuildDefaultsApplicability(typ.TypedDict):
    """Which of the build-standard's files the checkout actually has."""

    root_cargo_toml: bool
    rust_surfaces_declared: bool
    cargo_config: bool
    toolchain_file: bool


class BuildWorkflowFile(typ.TypedDict):
    """A workflow fact plus the fixed word for why it did not decode."""

    path: str
    parsed: object | None
    error: str | None
    decode_category: str | None


class BuildDefaultsEnvelope(typ.TypedDict):
    """The `policy-input/rust-build-defaults` document sent to Conftest.

    BD-001 to BD-006 read only what Cargo and rustup auto-discover: the
    standard is a default because a bare `cargo build` gets it. BD-007 to
    BD-009 read the builds that replace that default, because an assigned
    `RUSTFLAGS` replaces every `rustflags` source and a coverage build cannot
    use the Cranelift default at all. So the envelope also carries the root
    Makefile's `makeutil` report and the decoded workflows. A Makefile
    `makeutil` refuses is carried as `makefile_error` rather than raised, so
    the clauses that never read it still run.
    """

    schema_version: int
    kind: str
    repository: Repository
    applicability: BuildDefaultsApplicability
    cargo: CargoPayload
    toolchain: ToolchainFacts | None
    cargo_config: CargoConfigFacts | None
    exceptions: list[DocumentScan]
    makefile: MakeutilReport | None
    makefile_error: str | None
    workflows: list[BuildWorkflowFile]


def _decode_category(error: str | None) -> str | None:
    """Return a fixed word for why a workflow did not decode, or ``None``.

    The policy reports this word, never the parser's own message, which can
    quote workflow content. Mapping it here keeps the policy independent of
    how the shared workflow reader words its errors.

    Returns
    -------
    str | None
        ``None`` for a decoded workflow, else one of the category words.
    """
    if error is None:
        return None
    for prefix, category in _DECODE_CATEGORIES:
        if error.startswith(prefix):
            return category
    return "unreadable"


def _build_workflows(
    checkout: pathlib.Path, root: pathlib.Path
) -> list[BuildWorkflowFile]:
    """Return the checkout's workflow facts, each tagged with its decode category.

    Pure: nothing is logged here. The builder reports undecodable files through
    its injected reporter.

    Returns
    -------
    list[BuildWorkflowFile]
        One fact per workflow file, in the shared reader's order.
    """
    return [
        {**fact, "decode_category": _decode_category(fact["error"])}
        for fact in load_workflows(checkout, root)
    ]


def log_undecodable_workflow(path: str, category: str) -> None:
    """Log that a workflow did not decode, by path and fixed category word."""
    _logger.warning(
        "workflow did not decode", extra={"path": path, "category": category}
    )


def _read_makefile(
    checkout: pathlib.Path, root: pathlib.Path
) -> tuple[MakeutilReport | None, str | None]:
    """Return the root Makefile's `makeutil` report, or why there is none.

    A Makefile that resolves outside the checkout still raises, as does any
    `makeutil` failure other than a refusal; a refusal is returned as its
    reason.

    Returns
    -------
    tuple[MakeutilReport | None, str | None]
        ``(report, None)``, ``(None, reason)`` when `makeutil` refused the
        file, or ``(None, None)`` when there is no Makefile.
    """
    path = checkout / "Makefile"
    if not within_checkout(root, path, OPERATION_PARSE_MAKEFILE) or not is_file(
        path, OPERATION_PARSE_MAKEFILE
    ):
        return None, None
    try:
        return inspect_makefile_observed(path).report, None
    except MakefileRefusedError as error:
        return None, str(error)


def _exception_documents(parameters: cabc.Mapping[str, object]) -> list[str]:
    """Return the documents to scan for a recorded exception."""
    declared = parameters.get("exception_documents")
    if isinstance(declared, list):
        return [
            item for item in typ.cast("list[object]", declared) if isinstance(item, str)
        ]
    return list(DEFAULT_EXCEPTION_DOCUMENTS)


def _exception_keyword(parameters: cabc.Mapping[str, object]) -> str:
    """Return the word an exception heading has to contain."""
    declared = parameters.get("exception_keyword")
    return declared if isinstance(declared, str) else DEFAULT_EXCEPTION_KEYWORD


def build_build_defaults_envelope(
    checkout: pathlib.Path,
    parameters: cabc.Mapping[str, object] | None = None,
    *,
    report_undecodable: cabc.Callable[[str, str], None] = log_undecodable_workflow,
) -> BuildDefaultsEnvelope:
    """Assemble the build-defaults policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the local checkout to audit.
    parameters:
        The rule manifest's parameter defaults. Only the exception-document
        list and keyword are read here; every other parameter is a policy
        decision and reaches Conftest through `data.parameters`.
    report_undecodable:
        Called with the path and decode category of each workflow that did not
        decode. The default logs a warning; the fact gathering stays pure.

    Returns
    -------
    BuildDefaultsEnvelope
        The policy input document assembled from the checkout.
    """
    resolved = parameters if parameters is not None else {}
    resolution = resolve_rust_surfaces(checkout)
    cargo_parsed = next(
        (
            surface["parsed"]
            for surface in resolution.surfaces
            if surface["path"] == "Cargo.toml"
        ),
        None,
    )
    cargo_config = inspect_cargo_config(checkout)
    toolchain = inspect_toolchain(checkout)
    root = resolved_root(checkout)
    makefile, makefile_error = _read_makefile(checkout, root)
    workflows = _build_workflows(checkout, root)
    for workflow in workflows:
        if workflow["decode_category"] is not None:
            report_undecodable(workflow["path"], workflow["decode_category"])
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": BUILD_DEFAULTS_ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "root_cargo_toml": root_cargo_toml_exists(checkout / "Cargo.toml"),
            "rust_surfaces_declared": resolution.declared,
            "cargo_config": cargo_config is not None,
            "toolchain_file": toolchain is not None,
        },
        "cargo": {"parsed": cargo_parsed, "surfaces": list(resolution.surfaces)},
        "toolchain": toolchain,
        "cargo_config": cargo_config,
        "exceptions": find_exception_sections(
            checkout,
            _exception_documents(resolved),
            _exception_keyword(resolved),
        ),
        "makefile": makefile,
        "makefile_error": makefile_error,
        "workflows": workflows,
    }
