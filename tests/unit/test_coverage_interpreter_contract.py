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
from hypothesis import given
from hypothesis import strategies as st
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

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


class WorkflowLoadError(Exception):
    """A workflow file could not be read or parsed as a mapping."""


def load_workflows(directory: Path) -> dict[str, dict[object, object]]:
    """Read and parse every workflow file in ``directory``.

    This is the only fallible step; `lanes_in` is a pure transformation of
    what it returns.

    Returns
    -------
        Each workflow document keyed by its name, sorted by name.

    Raises
    ------
    WorkflowLoadError
        When a file cannot be read, is not valid YAML, or is not a mapping,
        naming the file.
    """
    yaml = YAML(typ="safe")
    documents: dict[str, dict[object, object]] = {}
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        try:
            document = yaml.load(path.read_text("utf-8"))
        except (OSError, YAMLError) as error:
            msg = f"cannot load workflow {path.name}: {error}"
            raise WorkflowLoadError(msg) from error
        if not isinstance(document, dict):
            msg = f"workflow {path.name} is not a mapping"
            raise WorkflowLoadError(msg)
        documents[path.name] = typ.cast("dict[object, object]", document)
    return documents


def lanes_of(documents: dict[str, dict[object, object]]) -> list[CoverageLane]:
    """Return every coverage step across already-loaded workflow documents."""
    return [
        lane
        for name, document in documents.items()
        for lane in lanes_in(name, document)
    ]


def repository_lanes() -> list[CoverageLane]:
    """Return every coverage step in this repository's workflows."""
    return lanes_of(load_workflows(WORKFLOW_DIRECTORY))


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
    *steps: dict[str, object],
    env: dict[str, object] | None = None,
    workflow_env: dict[str, object] | None = None,
) -> dict[object, object]:
    """Return a synthetic workflow document with one job."""
    job: dict[str, object] = {"steps": list(steps)}
    if env is not None:
        job["env"] = env
    document: dict[object, object] = {"jobs": {"lane": job}}
    if workflow_env is not None:
        document["env"] = workflow_env
    return document


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
        (_job(_SETUP, _COVERAGE, workflow_env={"UV_PYTHON": "3.13"}), "3.13"),
        (
            _job(
                _SETUP,
                _COVERAGE,
                env={"UV_PYTHON": "3.14"},
                workflow_env={"UV_PYTHON": "3.13"},
            ),
            "3.14",
        ),
    ],
)
def test_the_effective_scope_selects_the_interpreter(
    document: dict[object, object], expected: str | None
) -> None:
    """The step's `env` overrides the job's; an absent variable selects none."""
    (lane,) = lanes_in("w.yml", document)
    assert lane.selected == expected, (
        f"expected {_INTERPRETER_VARIABLE}={expected!r}, selected {lane.selected!r}"
    )


def test_an_ambiguous_declaration_declares_nothing() -> None:
    """Two different `setup-python` versions in one job cannot pass."""
    other: dict[str, object] = {
        "uses": "actions/setup-python@abc",
        "with": {"python-version": "3.14"},
    }
    document = _job(_SETUP, other, _COVERAGE | {"env": {"UV_PYTHON": "3.13"}})
    (lane,) = lanes_in("w.yml", document)
    assert lane.declared is None, (
        f"conflicting setup-python versions must declare nothing: {lane}"
    )


@pytest.mark.parametrize(
    ("name", "text"),
    [("broken.yml", "jobs: [unclosed\n"), ("scalar.yaml", "just text\n")],
)
def test_an_unloadable_workflow_is_named(tmp_path: Path, name: str, text: str) -> None:
    """A file the loader cannot interpret fails naming the file.

    Skipping it would drop a lane from every clause in silence.
    """
    (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a workflow", encoding="utf-8")
    with pytest.raises(WorkflowLoadError, match=name):
        load_workflows(tmp_path)


_versions = st.sampled_from([None, "3.12", "3.13", "3.14"])


@given(
    workflow_version=_versions,
    job_version=_versions,
    step_version=_versions,
    others_before=st.integers(0, 2),
    others_after=st.integers(0, 2),
)
def test_the_nearest_scope_selects_the_interpreter(
    workflow_version: str | None,
    job_version: str | None,
    step_version: str | None,
    others_before: int,
    others_after: int,
) -> None:
    """The nearest scope that sets `UV_PYTHON` wins, wherever the step sits.

    Unrelated steps around the coverage step, and unrelated variables beside
    `UV_PYTHON`, must not change the selection.
    """

    def scope(version: str | None) -> dict[str, object]:
        """Return an `env` mapping, with `UV_PYTHON` only when set."""
        return {"OTHER": "x"} | ({} if version is None else {"UV_PYTHON": version})

    other: dict[str, object] = {"run": "true", "env": {"UV_PYTHON": "2.7"}}
    document = _job(
        _SETUP,
        *[other] * others_before,
        _COVERAGE | {"env": scope(step_version)},
        *[other] * others_after,
        env=scope(job_version),
        workflow_env=scope(workflow_version),
    )
    nearest = next(
        (v for v in (step_version, job_version, workflow_version) if v is not None),
        None,
    )
    (lane,) = lanes_in("w.yml", document)
    assert lane.selected == nearest, f"expected {nearest!r}, selected {lane}"
    assert lane.declared == "3.13", f"setup-python declares 3.13: {lane}"
