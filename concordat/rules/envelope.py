"""Build the policy-input envelopes evaluated by rule-package policies.

One builder per rule package that needs different facts. `rust-makefile-
baseline` reads the Makefile through the pinned `makeutil`; `rust-build-
defaults` reads the files Cargo and rustup auto-discover, and the document a
repository records its codegen-backend exception in.
"""

from __future__ import annotations

import typing as typ

from .cargo_config import CargoConfigFacts, inspect_cargo_config
from .exception_docs import DocumentScan, find_exception_sections
from .makefile_facts import MakeutilReport, inspect_makefile
from .rust_surfaces import CargoManifest, CargoSurface, resolve_rust_surfaces
from .toolchain import ToolchainFacts, inspect_toolchain

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
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

    root_cargo_toml = cargo_path.is_file()
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
    if makefile_path.is_file():
        makefile_report = inspect_makefile(makefile_path).report

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


class BuildDefaultsEnvelope(typ.TypedDict):
    """The `policy-input/rust-build-defaults` document sent to Conftest.

    Deliberately carries no Makefile facts. The build standard is a default
    only because Cargo auto-discovers `.cargo/config.toml`; a repository whose
    flags live behind a Make target has no such file and fails on that alone.
    Reading the Makefile as well would add nothing this policy can decide, and
    would make the rule unrunnable against any checkout the pinned `makeutil`
    cannot parse.
    """

    schema_version: int
    kind: str
    repository: Repository
    applicability: BuildDefaultsApplicability
    cargo: CargoPayload
    toolchain: ToolchainFacts | None
    cargo_config: CargoConfigFacts | None
    exceptions: list[DocumentScan]


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
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": BUILD_DEFAULTS_ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "root_cargo_toml": (checkout / "Cargo.toml").is_file(),
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
    }
