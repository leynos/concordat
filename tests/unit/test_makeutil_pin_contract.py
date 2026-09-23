"""Contract tests binding CI's makeutil pin to makeutil's main branch.

CI installs makeutil from ``MAKEUTIL_REVISION``. The reachability check in
``scripts/check_makeutil_pin.py`` refuses a pin that makeutil's main does not
contain, but only if CI runs it unconditionally, before the install, against
the same revision every workflow installs. These tests hold those three facts.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

REPOSITORY_ROOT: typ.Final = Path(__file__).parents[2]
_CHECK_STEP: typ.Final = "Check makeutil pin is on main"
_INSTALL_STEP: typ.Final = "Install Makefile parser"
_CHECK_COMMAND: typ.Final = "uv run scripts/check_makeutil_pin.py"
_PINNED_JOBS: typ.Final = (
    (".github/workflows/ci.yml", "lint-test"),
    (".github/workflows/coverage-main.yml", "coverage-upload"),
)


def _job(workflow: str, job: str) -> dict[str, typ.Any]:
    """Load one job mapping from a workflow file."""
    document = YAML(typ="safe").load(REPOSITORY_ROOT / workflow)
    return document["jobs"][job]


def _step_names(job: dict[str, typ.Any]) -> list[str]:
    """Return the job's step names in order, with unnamed steps as ``""``."""
    return [step.get("name", "") for step in job["steps"]]


def test_pull_request_ci_checks_the_pin_before_installing() -> None:
    """The check runs as its own unguarded step ahead of the install.

    A step guarded by ``if:`` or allowed to fail would leave the check in the
    file while it protects nothing, so both are refused.
    """
    job = _job(".github/workflows/ci.yml", "lint-test")
    names = _step_names(job)
    assert names.count(_CHECK_STEP) == 1, names
    check_index = names.index(_CHECK_STEP)
    assert check_index < names.index(_INSTALL_STEP), names

    step = job["steps"][check_index]
    assert step.get("run") == _CHECK_COMMAND, step
    assert "if" not in step, step
    assert "continue-on-error" not in step, step
    # The script reads MAKEUTIL_REVISION; a step-level override would check a
    # different revision from the one the install step uses.
    assert "MAKEUTIL_REVISION" not in step.get("env", {}), step


def test_every_workflow_installs_the_checked_revision() -> None:
    """Every job that installs makeutil pins the revision CI checks.

    coverage-main.yml runs only on pushes to main, after the pull request's
    check has passed, so it is held to the same pin rather than re-checked.
    """
    revisions = {
        workflow: _job(workflow, job)["env"]["MAKEUTIL_REVISION"]
        for workflow, job in _PINNED_JOBS
    }
    assert len(set(revisions.values())) == 1, revisions
    for workflow, job in _PINNED_JOBS:
        assert _INSTALL_STEP in _step_names(_job(workflow, job)), workflow
