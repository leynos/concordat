"""Build the `policy-input/gate-lint-coverage` envelope.

The rule holds a repository's lint gate to checking all of what it governs: every
target, every feature, with warnings denied, and with `cargo doc` as a gate over
every library. The envelope records the two halves the policy compares:

* the Cargo *surfaces* the repository has (`lint_coverage_surfaces`): each
  workspace root, and each package outside any workspace (femtologging's
  `rust_extension/`), with the packages, declared features and library status of
  each;
* the lint *runs* the gate makes (`lint_coverage_gate`): every Clippy, Whitaker
  and `cargo doc` command that `make lint` and the workflows reach, with what it
  checks, and the surface it targets. A script that builds the lanes is carried
  as an unresolved run, because the audit does not read it.

A command that names no manifest runs at the root, so it targets the root
manifest's surface; one with `--manifest-path`, or after a literal `cd`, targets
that manifest's. A command that targets a manifest no surface holds is unplaced.
"""

from __future__ import annotations

import typing as typ

from .lint_coverage_commands import Run
from .lint_coverage_gate import GateRuns, read_gate_runs
from .lint_coverage_surfaces import Surface, read_surfaces, surface_of
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
    "LintCoverageEnvelope",
    "build_lint_coverage_envelope",
]

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/gate-lint-coverage"


class PlacedRun(Run):
    """A lint run, with the surface it targets (None when unplaced)."""

    surface: str | None


class LintCoverageEnvelope(typ.TypedDict):
    """The `policy-input/gate-lint-coverage` document."""

    schema_version: int
    kind: str
    repository: Repository
    surfaces: list[Surface]
    runs: list[PlacedRun]
    makefile_resolved: bool
    make_environment: dict[str, object]
    cargo_config: dict[str, object]
    unreadable: list[str]


def _has_root_makefile(checkout: pathlib.Path, root: pathlib.Path) -> bool:
    """Return whether the checkout has a regular root `Makefile` inside it."""
    makefile = checkout / "Makefile"
    return within_checkout(root, makefile, OPERATION_READ_MAKEFILE) and is_file(
        makefile, OPERATION_PROBE_PATH
    )


def _placed(run: Run, surfaces: list[Surface]) -> PlacedRun:
    """Return *run* with the surface it targets.

    A run that names no manifest targets the root manifest. A run the reader
    could not place, or one that names a manifest no surface holds, has no
    surface and is reported as unplaced.

    Returns
    -------
    PlacedRun
        The run with its `surface`.
    """
    manifest = run["manifest"] if run["manifest"] is not None else "Cargo.toml"
    surface = surface_of(manifest, surfaces) if run["is_placed"] else None
    return {**run, "surface": surface}


def build_lint_coverage_envelope(
    checkout: pathlib.Path, *, inspect: MakefileInspector = inspect_makefile
) -> LintCoverageEnvelope:
    """Assemble the gate-lint-coverage policy input for one local checkout.

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
    LintCoverageEnvelope
        The policy input document assembled from the checkout.
    """
    root = resolved_root(checkout)
    reader = Reader(checkout, root)
    surfaces = read_surfaces(reader)
    gate: GateRuns = read_gate_runs(
        checkout, root, inspect=inspect, has_makefile=_has_root_makefile(checkout, root)
    )
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": origin_slug(checkout)},
        "surfaces": surfaces,
        "runs": [_placed(run, surfaces) for run in gate["runs"]],
        "makefile_resolved": gate["makefile_resolved"],
        "make_environment": dict(gate["make_environment"]),
        "cargo_config": dict(gate["cargo_config"]),
        "unreadable": sorted({*reader.unreadable, *gate["unreadable"]}),
    }
