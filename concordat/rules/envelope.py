"""Build the `policy-input/v1` envelope evaluated by rule-package policies."""

from __future__ import annotations

import tomllib
import typing as typ

from concordat.errors import OperationalRuleError

from .makefile_facts import inspect_makefile

if typ.TYPE_CHECKING:
    import pathlib

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/rust-makefile-baseline"


def build_envelope(checkout: pathlib.Path) -> dict[str, typ.Any]:
    """Assemble the policy input document for one local checkout.

    Root `Cargo.toml` presence is provisional evidence of Rust
    applicability; the `.concordat` manifest remains the eventual
    authority (see the Parabellum ExecPlan decision log).
    """
    cargo_path = checkout / "Cargo.toml"
    makefile_path = checkout / "Makefile"

    root_cargo_toml = cargo_path.is_file()
    cargo_parsed: dict[str, typ.Any] | None = None
    if root_cargo_toml:
        try:
            cargo_parsed = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
            message = f"cannot parse {cargo_path.name} in {checkout}: {error}"
            raise OperationalRuleError(
                message,
                operation="parse-cargo-toml",
                resource=cargo_path,
            ) from error

    makefile_report: dict[str, typ.Any] | None = None
    if makefile_path.is_file():
        makefile_report = inspect_makefile(makefile_path).report

    return {
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
