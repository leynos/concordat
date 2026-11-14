"""The fact types the `rust-prover-tooling` envelope is built from.

Each class is one object in the `policy-input/rust-prover-tooling` document; the
modules that read facts (`prover_make_facts`, `prover_workflow_facts`,
`prover_script_facts`, `prover_exceptions`) fill them and
`prover_tooling_envelope` assembles them.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    from .codescene_coverage_envelope import Repository


class Pin(typ.TypedDict):
    """Store one `rust-prover-tools` revision the checkout names.

    Attributes
    ----------
    value:
        The reference as written (a 40-hex revision when it is well formed).
    source:
        The file it was read from.
    """

    value: str
    source: str


class InstallerStep(typ.TypedDict):
    """Store one workflow step that installs a prover itself.

    Attributes
    ----------
    where:
        `<job>/<step index>`.
    command:
        The installer command as written, or the `make` target whose
        prerequisites reach one.
    """

    where: str
    command: str


class SharedUse(typ.TypedDict):
    """Store one use of the shared install action.

    Attributes
    ----------
    where:
        `<job>/<step index>`.
    ref:
        The text after the `@`, which should be a 40-hex SHA.
    """

    where: str
    ref: str


class WorkflowFacts(typ.TypedDict):
    """Store one workflow's prover facts, or the reason it could not be read.

    Attributes
    ----------
    path:
        The workflow's path relative to the checkout, in POSIX form.
    error:
        Why the workflow could not be decoded, or None.
    calls_make:
        Whether any `run` script calls `make`.
    installer_steps:
        Steps that install a prover, directly or through a Make prerequisite.
    shared_uses:
        Every use of the shared install action.
    """

    path: str
    error: str | None
    calls_make: bool
    installer_steps: list[InstallerStep]
    shared_uses: list[SharedUse]


class LocalInstaller(typ.TypedDict):
    """Store one file that installs a prover outside the shared action.

    Attributes
    ----------
    path:
        The file, relative to the checkout.
    reason:
        `name`, `command` or `download`.
    """

    path: str
    reason: str


class ProverException(typ.TypedDict):
    """Store one declared exception and the facts it is checked against.

    Attributes
    ----------
    clauses:
        The check identifiers the exception waives.
    pin:
        The revision (or its prefix) the repository stays on.
    record:
        The decision record's path.
    rationale:
        Why the repository stays.
    review_trigger:
        The event that sends it back for review.
    record_present:
        Whether the decision record is a regular file in the checkout.
    """

    clauses: list[str]
    pin: str
    record: str
    rationale: str
    review_trigger: str
    record_present: bool


class MakefileState(typ.TypedDict):
    """Store whether the Makefile was read.

    Attributes
    ----------
    state:
        `absent`, `ok` or `refused`.
    error:
        The refusal reason, or None.
    """

    state: str
    error: str | None


class ProverToolingEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the `rust-prover-tooling` rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug.
    makefile:
        Whether the root Makefile was read.
    version_files:
        The prover version pins present (`tools/kani/VERSION` and so on).
    pins:
        Every `rust-prover-tools` revision named, with its source.
    runs_prover:
        Whether any Makefile recipe, workflow step or script installs or runs a
        prover.
    workflows:
        Every root workflow's prover facts.
    local_installers:
        Files that install a prover outside the shared action.
    install_tests:
        Test files whose names say they test prover installation.
    unreadable:
        Files that could not be read, each with its reason.
    exceptions:
        The exceptions declared in `.concordat`.
    """

    schema_version: int
    kind: str
    repository: Repository
    makefile: MakefileState
    version_files: list[str]
    pins: list[Pin]
    runs_prover: bool
    workflows: list[WorkflowFacts]
    local_installers: list[LocalInstaller]
    install_tests: list[str]
    unreadable: list[str]
    exceptions: list[ProverException]
