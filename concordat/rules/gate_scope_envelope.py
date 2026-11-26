"""Build the `policy-input/gate-workspace-coverage` envelope.

Four tools silently narrow to less than the workspace: `cargo clippy`,
`cargo nextest run`, `cargo test` (with and without `--doc`) and Whitaker. In a
workspace with a root package, a bare command selects the root package alone;
`workspace.default-members` and a `-p` list narrow further. A gate that reads
green over a third of a workspace is worse than no gate, so this rule asks of
every package-selecting command the gate reaches whether it selects the whole
workspace, or names what it leaves out and why.

The envelope records the facts and decides nothing:

- the root workspace's members and its default selection
  (`gate_scope_workspace`);
- every gate-tool command the Makefile's `lint` and `test` targets reach and
  every `run` step of every workflow and composite action, with each matrix
  entry read as its own command (`gate_scope_sources`, `gate_scope_commands`);
- the fenced prose examples that show those commands, so a finding can name
  the documents a fix must update;
- every file or line the audit could not read, which the policy reports as
  indeterminate instead of guessing.

The file readers are `markdown_envelope`'s, so every envelope shares one
containment guard: no policy input is read from outside the checkout.
"""

from __future__ import annotations

import typing as typ

from .gate_scope_sources import (
    GATE_TARGETS,
    GateLines,
    makefile_lines,
    markdown_examples,
    workflow_lines,
)
from .gate_scope_workspace import WorkspaceFact, workspace_fact
from .makefile_facts import MakefileInspector, inspect_makefile
from .markdown_envelope import (
    OPERATION_PROBE_PATH,
    OPERATION_READ_MAKEFILE,
    Repository,
    is_file,
    load_workflows,
    resolved_root,
    within_checkout,
)
from .test_environment_types import CARGO_MANIFEST, Reader
from .uv_gate_envelope import load_actions

if typ.TYPE_CHECKING:
    import pathlib

    from .gate_scope_commands import Invocation

__all__ = [
    "ENVELOPE_KIND",
    "ENVELOPE_SCHEMA_VERSION",
    "GateScopeEnvelope",
    "build_gate_scope_envelope",
]

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/gate-workspace-coverage"
OPERATION_PROBE_MANIFEST: typ.Final = "probe-gate-scope-manifest"


class GateScopeApplicability(typ.TypedDict):
    """Evidence that the gate-workspace-coverage rule may apply."""

    cargo_manifest: bool
    root_makefile: bool


class GateScopeMakefile(typ.TypedDict):
    """What the walk from the gate targets found in the root Makefile."""

    targets: list[str]
    is_resolved: bool


class GateScopeEnvelope(typ.TypedDict):
    """The `policy-input/gate-workspace-coverage` document."""

    schema_version: int
    kind: str
    repository: Repository
    applicability: GateScopeApplicability
    workspace: WorkspaceFact | None
    makefile: GateScopeMakefile
    invocations: list[Invocation]
    examples: list[Invocation]
    unreadable: list[str]


def _has_file(
    checkout: pathlib.Path, root: pathlib.Path, name: str, operation: str
) -> bool:
    """Return whether *name* is a regular file at the checkout root, inside it."""
    path = checkout / name
    return within_checkout(root, path, operation) and is_file(
        path, OPERATION_PROBE_PATH
    )


def _makefile_lines(
    checkout: pathlib.Path, inspect: MakefileInspector, *, has_makefile: bool
) -> GateLines:
    """Return the gate commands in the Makefile, or none without one."""
    if not has_makefile:
        return GateLines([], [], [], is_resolved=True)
    return makefile_lines(inspect(checkout / "Makefile").report, starts=GATE_TARGETS)


def build_gate_scope_envelope(
    checkout: pathlib.Path, *, inspect: MakefileInspector = inspect_makefile
) -> GateScopeEnvelope:
    """Assemble the gate-workspace-coverage policy input for one checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    inspect:
        Reads the root Makefile's facts. The default is the pure query; the
        command boundary injects an observing one.

    An `OperationalRuleError` propagates if the root `Makefile` cannot be
    parsed by `makeutil`, a fact file exists but cannot be opened, or a fact
    file resolves to a location outside the checkout.

    Returns
    -------
    GateScopeEnvelope
        The policy input document assembled from the checkout.
    """
    root = resolved_root(checkout)
    has_makefile = _has_file(checkout, root, "Makefile", OPERATION_READ_MAKEFILE)
    has_manifest = _has_file(checkout, root, CARGO_MANIFEST, OPERATION_PROBE_MANIFEST)
    reader = Reader(checkout, root)
    workspace = workspace_fact(reader) if has_manifest else None
    from_make = _makefile_lines(checkout, inspect, has_makefile=has_makefile)
    from_ci = workflow_lines([
        *load_workflows(checkout, root),
        *load_actions(checkout, root),
    ])
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "cargo_manifest": has_manifest,
            "root_makefile": has_makefile,
        },
        "workspace": workspace,
        "makefile": {
            "targets": from_make.targets,
            "is_resolved": from_make.is_resolved,
        },
        "invocations": [*from_make.invocations, *from_ci.invocations],
        "examples": markdown_examples(checkout, root) if has_manifest else [],
        "unreadable": [*reader.unreadable, *from_make.unreadable, *from_ci.unreadable],
    }
