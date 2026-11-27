"""Build the `policy-input/rust-lint-tables` envelope.

The rule holds every Cargo package a checkout owns to the estate's Rust lint
baseline. The envelope records, per package, the lint levels it compiles under
(resolved the way Cargo does, in `lint_tables_manifests`), the two
configuration files that decide how cognitive complexity is gated
(`lint_tables_config`), and the gate facts two of the checks need
(`lint_tables_gate`). The policy decides what the facts mean; the baseline it
compares them with is data (`data/baseline.json` in the rule package).

The file readers are the shared ones, so no policy input is read from outside
the checkout, and a manifest that cannot be decoded is carried in `unreadable`
for the policy to report instead of being skipped. Gate lines the readers could
not place are kept apart (`gate_unreadable`): they matter only to the two gate
facts, not to whether a manifest can be judged.
"""

from __future__ import annotations

import typing as typ

from .lint_tables_config import ClippyConfig, DylintConfig, clippy_config, dylint_config
from .lint_tables_gate import MiriFact, WhitakerRun, read_gate
from .lint_tables_manifests import PackageFacts, read_packages
from .makefile_facts import MakefileInspector, inspect_makefile
from .markdown_envelope import (
    OPERATION_PROBE_PATH,
    OPERATION_READ_MAKEFILE,
    Repository,
    is_file,
    resolved_root,
    within_checkout,
)
from .nested_cargo import origin_slug
from .test_environment_types import Reader

if typ.TYPE_CHECKING:
    import pathlib

__all__ = [
    "ENVELOPE_KIND",
    "ENVELOPE_SCHEMA_VERSION",
    "LintTablesEnvelope",
    "build_lint_tables_envelope",
]

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/rust-lint-tables"


class PackageConfig(typ.TypedDict):
    """The configuration files that apply to one package."""

    clippy: ClippyConfig
    dylint: DylintConfig


class GovernedPackage(PackageFacts):
    """A package's lint facts together with the configuration that applies."""

    config: PackageConfig


class LintTablesEnvelope(typ.TypedDict):
    """The `policy-input/rust-lint-tables` document."""

    schema_version: int
    kind: str
    repository: Repository
    packages: list[GovernedPackage]
    miri: MiriFact
    whitaker_runs: list[WhitakerRun]
    unreadable: list[str]
    gate_unreadable: list[str]


def _has_root_makefile(checkout: pathlib.Path, root: pathlib.Path) -> bool:
    """Return whether the checkout has a regular root `Makefile` inside it."""
    makefile = checkout / "Makefile"
    return within_checkout(root, makefile, OPERATION_READ_MAKEFILE) and is_file(
        makefile, OPERATION_PROBE_PATH
    )


def _governed(reader: Reader, package: PackageFacts) -> GovernedPackage:
    """Return *package* with the configuration files that apply to it."""
    directory = reader.checkout / package["directory"]
    return {
        **package,
        "config": {
            "clippy": clippy_config(reader, directory),
            "dylint": dylint_config(reader, directory),
        },
    }


def build_lint_tables_envelope(
    checkout: pathlib.Path, *, inspect: MakefileInspector = inspect_makefile
) -> LintTablesEnvelope:
    """Assemble the rust-lint-tables policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    inspect:
        Reads the root Makefile's facts. The default is the pure query; the
        command boundary injects an observing one.

    An `OperationalRuleError` propagates if the root `Makefile` cannot be parsed
    by `makeutil`, a fact file exists but cannot be opened, a directory cannot
    be listed, or a fact file resolves to a location outside the checkout.

    Returns
    -------
    LintTablesEnvelope
        The policy input document assembled from the checkout.
    """
    root = resolved_root(checkout)
    reader = Reader(checkout, root)
    packages = [_governed(reader, p) for p in read_packages(reader)]
    gate = read_gate(
        checkout,
        root,
        inspect=inspect,
        has_makefile=_has_root_makefile(checkout, root),
    )
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": origin_slug(checkout)},
        "packages": packages,
        "miri": gate["miri"],
        "whitaker_runs": gate["whitaker_runs"],
        "unreadable": sorted(set(reader.unreadable)),
        "gate_unreadable": sorted(set(gate["unreadable"])),
    }
