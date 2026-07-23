"""Build the `policy-input/v1` envelope evaluated by rule-package policies."""

from __future__ import annotations

import tomllib
import typing as typ

from concordat.errors import OperationalRuleError

from .makefile_facts import MakeutilReport, inspect_makefile

if typ.TYPE_CHECKING:
    import pathlib

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/rust-makefile-baseline"

# The parsed Cargo manifest is opaque to the policy (only its presence matters),
# so it is modelled as an arbitrary TOML table rather than a fixed schema.
CargoManifest = dict[str, object]


class CargoPayload(typ.TypedDict):
    """The `cargo` section: the parsed root manifest, or ``None`` when absent."""

    parsed: CargoManifest | None


class Applicability(typ.TypedDict):
    """Provisional evidence that a rule package applies to the checkout."""

    root_cargo_toml: bool
    root_makefile: bool


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


def _parse_cargo(cargo_path: pathlib.Path, checkout: pathlib.Path) -> CargoManifest:
    """Parse the root Cargo manifest, rejecting unreadable or non-table input."""
    try:
        loaded: object = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        message = f"cannot parse {cargo_path.name} in {checkout}: {error}"
        raise OperationalRuleError(
            message,
            operation="parse-cargo-toml",
            resource=cargo_path,
        ) from error
    if not isinstance(loaded, dict):
        message = f"{cargo_path.name} in {checkout} did not parse to a table"
        raise OperationalRuleError(
            message,
            operation="parse-cargo-toml",
            resource=cargo_path,
        )
    return typ.cast("CargoManifest", loaded)


def build_envelope(checkout: pathlib.Path) -> PolicyEnvelope:
    """Assemble the policy input document for one local checkout.

    Root `Cargo.toml` presence is provisional evidence of Rust
    applicability; the `.concordat` manifest remains the eventual
    authority (see the Parabellum ExecPlan decision log).
    """
    cargo_path = checkout / "Cargo.toml"
    makefile_path = checkout / "Makefile"

    root_cargo_toml = cargo_path.is_file()
    cargo_parsed: CargoManifest | None = None
    if root_cargo_toml:
        cargo_parsed = _parse_cargo(cargo_path, checkout)

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
        },
        "cargo": {"parsed": cargo_parsed},
        "makefile": makefile_report,
    }
    return envelope
