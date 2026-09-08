"""Build the `policy-input/v1` envelope evaluated by rule-package policies."""

from __future__ import annotations

import typing as typ

from .makefile_facts import MakeutilReport, inspect_makefile
from .rust_surfaces import CargoManifest, CargoSurface, resolve_rust_surfaces

if typ.TYPE_CHECKING:
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
