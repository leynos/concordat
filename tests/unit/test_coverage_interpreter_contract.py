"""Contract: every coverage lane measures on the interpreter it declares.

The push-to-main publisher writes the ratchet baseline and the pull-request
lane is measured against it, so both must measure with one interpreter.
Declaring `python-version` to `setup-python` does not ensure that: the shared
coverage action builds its environment with `uv venv`, which takes the newest
interpreter uv can find. The pull-request lane's tool installs leave a managed
3.14 behind, so that lane measured on 3.14 while the publisher measured on
3.13. Slipcover counts about 1,300 fewer valid lines on 3.14, so every pull
request read roughly 2.5 points below the baseline and failed the ratchet
without any change in coverage.

Each coverage step must therefore set `UV_PYTHON` (at the step, job or
workflow scope) to the version its job gives `setup-python`.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from ruamel.yaml import YAML

REPOSITORY_ROOT: typ.Final = Path(__file__).parents[2]
WORKFLOW_DIRECTORY: typ.Final = REPOSITORY_ROOT / ".github/workflows"

_COVERAGE_ACTION: typ.Final = "generate-coverage@"
_SETUP_PYTHON: typ.Final = "actions/setup-python@"
_INTERPRETER_VARIABLE: typ.Final = "UV_PYTHON"


class CoverageLane(typ.NamedTuple):
    """One coverage step, with the interpreter its job declares and selects."""

    name: str
    declared: str | None
    selected: str | None


def _mapping(value: object) -> dict[str, object]:
    """Return ``value`` as a mapping, or an empty one when it is absent."""
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _uses(step: dict[str, object], action: str) -> bool:
    """Return whether ``step`` invokes ``action``."""
    uses = step.get("uses")
    return isinstance(uses, str) and action in uses


def _text(value: object) -> str | None:
    """Return a scalar as stripped text, or `None` when it is absent."""
    return None if value is None else str(value).strip()


def _declared_version(steps: list[dict[str, object]]) -> str | None:
    """Return the one `python-version` a job's `setup-python` steps declare.

    Returns
    -------
        The declared version, or `None` when the steps declare none or more
        than one, so an ambiguous job cannot pass.
    """
    versions = {
        _text(_mapping(step.get("with")).get("python-version"))
        for step in steps
        if _uses(step, _SETUP_PYTHON)
    }
    return versions.pop() if len(versions) == 1 else None


def lanes_in(path: str, document: dict[object, object]) -> list[CoverageLane]:
    """Return every coverage step in one workflow document.

    The selected interpreter is the effective `UV_PYTHON`: a step's `env`
    overrides its job's, which overrides the workflow's.

    Returns
    -------
        One lane per `generate-coverage` step, in document order.
    """
    workflow_env = _mapping(document.get("env"))
    lanes: list[CoverageLane] = []
    for job_name, job in _mapping(document.get("jobs")).items():
        job_mapping = _mapping(job)
        declared_steps = job_mapping.get("steps")
        steps = (
            [_mapping(step) for step in declared_steps]
            if isinstance(declared_steps, list)
            else []
        )
        declared = _declared_version(steps)
        job_env = workflow_env | _mapping(job_mapping.get("env"))
        lanes.extend(
            CoverageLane(
                f"{path} job {job_name!r}",
                declared,
                _text((job_env | _mapping(step.get("env"))).get(_INTERPRETER_VARIABLE)),
            )
            for step in steps
            if _uses(step, _COVERAGE_ACTION)
        )
    return lanes


def repository_lanes() -> list[CoverageLane]:
    """Return every coverage step in this repository's workflows."""
    yaml = YAML(typ="safe")
    return [
        lane
        for path in sorted(WORKFLOW_DIRECTORY.iterdir())
        if path.suffix.lower() in {".yml", ".yaml"}
        for lane in lanes_in(
            path.relative_to(REPOSITORY_ROOT).as_posix(),
            typ.cast("dict[object, object]", yaml.load(path.read_text("utf-8"))),
        )
    ]


def test_every_coverage_lane_selects_the_interpreter_it_declares() -> None:
    """Each lane's `UV_PYTHON` equals the version given to `setup-python`."""
    lanes = repository_lanes()
    assert len(lanes) >= 2, (
        "expected the pull-request lane and the publisher to generate "
        f"coverage, so this clause cannot pass vacuously; found {lanes}"
    )
    mismatched = [
        lane
        for lane in lanes
        if lane.declared is None or lane.selected != lane.declared
    ]
    assert not mismatched, (
        f"every coverage step must set {_INTERPRETER_VARIABLE} to its job's "
        f"setup-python version; these do not: {mismatched}"
    )


def test_every_coverage_lane_measures_on_one_interpreter() -> None:
    """The publisher and the lanes it baselines measure on one version."""
    selected = {lane.selected for lane in repository_lanes()}
    assert len(selected) == 1, (
        f"coverage lanes select different interpreters: {sorted(map(str, selected))}"
    )


def _job(
    *steps: dict[str, object], env: dict[str, object] | None = None
) -> dict[object, object]:
    """Return a synthetic workflow document with one job."""
    job: dict[str, object] = {"steps": list(steps)}
    if env is not None:
        job["env"] = env
    return {"jobs": {"lane": job}}


_SETUP: dict[str, object] = {
    "uses": "actions/setup-python@abc",
    "with": {"python-version": "3.13"},
}
_COVERAGE: dict[str, object] = {
    "uses": "leynos/shared-actions/.github/actions/generate-coverage@abc"
}


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (_job(_SETUP, _COVERAGE | {"env": {"UV_PYTHON": "3.13"}}), "3.13"),
        (_job(_SETUP, _COVERAGE, env={"UV_PYTHON": "3.13"}), "3.13"),
        (
            _job(
                _SETUP,
                _COVERAGE | {"env": {"UV_PYTHON": "3.14"}},
                env={"UV_PYTHON": "3.13"},
            ),
            "3.14",
        ),
        (_job(_SETUP, _COVERAGE), None),
    ],
)
def test_the_effective_scope_selects_the_interpreter(
    document: dict[object, object], expected: str | None
) -> None:
    """The step's `env` overrides the job's; an absent variable selects none."""
    (lane,) = lanes_in("w.yml", document)
    assert lane.selected == expected


def test_an_ambiguous_declaration_declares_nothing() -> None:
    """Two different `setup-python` versions in one job cannot pass."""
    other: dict[str, object] = {
        "uses": "actions/setup-python@abc",
        "with": {"python-version": "3.14"},
    }
    document = _job(_SETUP, other, _COVERAGE | {"env": {"UV_PYTHON": "3.13"}})
    (lane,) = lanes_in("w.yml", document)
    assert lane.declared is None
