"""Build the facts for the `rust-prover-tooling` rule (PT-001 to PT-005).

A repository that uses Kani or Verus is held to one standard: it pins
`rust-prover-tools` to a single commit, installs provers in continuous
integration only through the shared `install-prover-tools` action (pinned by
SHA), and carries no local installer scripts or tests of prover installation.
A named exception is recorded as data in `.concordat`.

This builder assembles the facts the policy needs, each read from a parser and
never from a text search:

- the root `Makefile`, through the pinned `makeutil` (`prover_make_facts`), for
  its rules, so a CI step that runs `make X` can be followed through X's
  prerequisites, and for the revisions its variables and recipes name;
- each root workflow's decoded document (`prover_workflow_facts`), for the
  steps' `uses` and the commands in their `run` scripts, split and classified
  by `shell_commands` and `prover_commands`;
- `tools/rust-prover-tools/REF` and the prover version files (`prover_pins`);
- scripts and tests (`prover_script_facts`) and the exceptions declared in
  `.concordat` (`prover_exceptions`).

A workflow, script or Makefile that cannot be read stays a fact with its
reason, so the policy answers indeterminate rather than reporting a broken
checkout as compliant.
"""

from __future__ import annotations

import typing as typ

from .codescene_coverage_envelope import _load_workflows
from .fs_probe import regular_file_exists
from .makefile_facts import MakefileInspector, inspect_makefile
from .markdown_envelope import resolved_root
from .nested_cargo import origin_slug
from .prover_commands import is_installer, is_runner
from .prover_exceptions import declared_exceptions
from .prover_make_facts import EMPTY_RULES, all_commands, make_rules, read_makefile
from .prover_pins import OPERATION, command_pins, makefile_pins, ref_file_pins
from .prover_script_facts import install_tests, scan_scripts
from .prover_workflow_facts import Context, workflow_facts

if typ.TYPE_CHECKING:
    import pathlib

    from .prover_tooling_types import Pin, ProverToolingEnvelope, WorkflowFacts

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/rust-prover-tooling"
VERSION_FILES: typ.Final = ("tools/kani/VERSION", "tools/verus/VERSION")


def _version_files(checkout: pathlib.Path) -> list[str]:
    """Return the prover version pins present in the checkout."""
    return [
        path
        for path in VERSION_FILES
        if regular_file_exists(checkout / path, operation=OPERATION)
    ]


def _workflows(
    checkout: pathlib.Path, context: Context
) -> tuple[list[WorkflowFacts], bool, list[Pin]]:
    """Return every workflow's facts, whether any runs a prover, and their pins."""
    facts: list[WorkflowFacts] = []
    runs = False
    pins: list[Pin] = []
    for workflow in _load_workflows(checkout):
        found, workflow_runs, workflow_pins = workflow_facts(workflow, context)
        facts.append(found)
        runs = runs or workflow_runs
        pins += workflow_pins
    return facts, runs, pins


def build_prover_tooling_envelope(
    checkout: pathlib.Path,
    *,
    inspect: MakefileInspector = inspect_makefile,
) -> ProverToolingEnvelope:
    """Assemble the prover tooling evidence for one checkout.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.
    inspect:
        Reads the root Makefile's facts. The default is the pure query; the
        command boundary injects an observing one.

    An `OperationalRuleError` reaches the caller when the workflow directory,
    the Makefile, the REF file or a declared record resolves outside the
    checkout, when the `.concordat` declaration is malformed, or when
    `makeutil` fails other than by refusing the file.

    Returns
    -------
    ProverToolingEnvelope
        The prover facts the policy judges.
    """
    root = resolved_root(checkout)
    state, report = read_makefile(checkout, root, inspect)
    rules = make_rules(report) if report is not None else EMPTY_RULES
    scripts = scan_scripts(checkout)
    context = Context(rules, frozenset(item["path"] for item in scripts.installers))
    workflows, workflows_run, workflow_pins = _workflows(checkout, context)
    pins = ref_file_pins(checkout, root) + workflow_pins + scripts.pins
    pins += makefile_pins(report) if report is not None else []
    pins += command_pins(all_commands(rules), "Makefile recipe")
    makefile_runs = any(_runs(argv) for argv in all_commands(rules))
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "makefile": state,
        "version_files": _version_files(checkout),
        "pins": pins,
        "runs_prover": makefile_runs or workflows_run or scripts.runs,
        "workflows": workflows,
        "local_installers": scripts.installers,
        "install_tests": install_tests(checkout),
        "unreadable": scripts.unreadable,
        "exceptions": declared_exceptions(checkout),
    }


def _runs(argv: list[str]) -> bool:
    """Return whether a command installs or runs a prover."""
    return is_runner(argv) or is_installer(argv)
