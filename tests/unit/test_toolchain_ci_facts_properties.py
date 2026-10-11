"""Independent property tests for the workflow toolchain facts (RT-026, RT-027).

Each generated case is a list of semantic decisions: which toolchain a step
selects and by which route (an action input, an action ref, a `rustup`
command, a `cargo +toolchain` override, an `env` reference or a matrix leg).
The case is rendered to a workflow in a scratch checkout, read by the real
extractor, and compared with the toolchains the decisions name. The
expectation is a predicate over the decisions, not a second reading of the
rendered YAML. Order and duplication must not change the answer, and a
document of arbitrary shape must be read without error.
"""

from __future__ import annotations

import dataclasses
import pathlib
import tempfile
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st
from ruamel.yaml import YAML

from concordat.rules.toolchain_ci_facts import ci_facts, minor_of

if typ.TYPE_CHECKING:
    import random

_ROUTES: typ.Final = (
    "action_input",
    "action_ref",
    "rustup_install",
    "rustup_default",
    "cargo_override",
    "env_reference",
    "matrix_leg",
)
_TOOLCHAINS: typ.Final = st.one_of(
    st.builds(
        "{}.{}.{}".format,
        st.integers(80, 99),
        st.integers(0, 9),
        st.integers(0, 3),
    ),
    st.builds(
        "nightly-2026-{:02d}-{:02d}".format, st.integers(1, 12), st.integers(1, 28)
    ),
    st.sampled_from(["stable", "nightly", "beta"]),
)


@dataclasses.dataclass(frozen=True)
class _Selection:
    """One step selecting one toolchain by one route."""

    route: str
    toolchain: str


_SELECTIONS: typ.Final = st.builds(_Selection, st.sampled_from(_ROUTES), _TOOLCHAINS)


def _step(
    index: int, selection: _Selection
) -> tuple[dict[str, object], dict[str, str]]:
    """Render one selection as a step and the env entries it needs."""
    tool = selection.toolchain
    env: dict[str, str] = {}
    step: dict[str, object]
    match selection.route:
        case "action_input":
            step = {
                "uses": "dtolnay/rust-toolchain@master",
                "with": {"toolchain": tool},
            }
        case "action_ref":
            step = {"uses": f"dtolnay/rust-toolchain@{tool}"}
        case "rustup_install":
            step = {"run": f"rustup toolchain install --profile minimal {tool}"}
        case "rustup_default":
            step = {"run": f"rustup default {tool} && rustup show"}
        case "cargo_override":
            step = {"run": f"cargo +{tool} check"}
        case "env_reference":
            env = {f"TC_{index}": tool}
            step = {
                "uses": "dtolnay/rust-toolchain@master",
                "with": {"toolchain": f"${{{{ env.TC_{index} }}}}"},
            }
        case _:
            step = {
                "uses": "dtolnay/rust-toolchain@master",
                "with": {"toolchain": "${{ matrix.rust }}"},
            }
    return step, env


def _workflow(selections: list[_Selection]) -> dict[str, object]:
    """Render *selections* as one workflow, one job per selection."""
    env: dict[str, str] = {}
    jobs: dict[str, object] = {}
    for index, selection in enumerate(selections):
        step, step_env = _step(index, selection)
        env.update(step_env)
        job: dict[str, object] = {"runs-on": "ubuntu-latest", "steps": [step]}
        if selection.route == "matrix_leg":
            job["strategy"] = {"matrix": {"rust": [selection.toolchain]}}
        jobs[f"job{index}"] = job
    return {"env": env, "jobs": jobs} if env else {"jobs": jobs}


def _read(document: object) -> dict[str, typ.Any]:
    """Return the extractor's facts for a checkout holding *document*."""
    with tempfile.TemporaryDirectory() as scratch:
        workflows = pathlib.Path(scratch) / ".github" / "workflows"
        workflows.mkdir(parents=True)
        with (workflows / "ci.yml").open("w", encoding="utf-8") as handle:
            YAML(typ="safe").dump(document, handle)
        return typ.cast("dict[str, typ.Any]", ci_facts(pathlib.Path(scratch)))


def _expected(selections: list[_Selection]) -> tuple[list[str], list[str]]:
    """Return the dated nightlies and numeric minors the decisions name."""
    names = {s.toolchain for s in selections}
    nightlies = sorted(n for n in names if n.startswith("nightly-"))
    minors = sorted({m for n in names if (m := minor_of(n)) is not None})
    return nightlies, minors


@settings(max_examples=60, deadline=None)
@given(st.lists(_SELECTIONS, max_size=6))
def test_every_route_names_exactly_its_toolchains(
    selections: list[_Selection],
) -> None:
    """Whatever route selects a toolchain, the facts name it, and nothing else."""
    facts = _read(_workflow(selections))
    nightlies, minors = _expected(selections)

    assert facts["errors"] == [], facts
    assert facts["dated_nightlies"] == nightlies, (selections, facts)
    assert facts["numeric_minors"] == minors, (selections, facts)


@settings(max_examples=40, deadline=None)
@given(st.lists(_SELECTIONS, min_size=1, max_size=6), st.randoms(use_true_random=False))
def test_order_and_duplication_do_not_change_the_facts(
    selections: list[_Selection], rng: random.Random
) -> None:
    """The facts are a set: reordering or repeating the steps changes nothing."""
    shuffled = list(selections) + list(selections)
    rng.shuffle(shuffled)

    assert _read(_workflow(shuffled)) == _read(_workflow(selections))


_SCALARS: typ.Final = st.one_of(
    st.none(), st.booleans(), st.integers(), st.text(max_size=8)
)
_NODES: typ.Final = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(
            st.sampled_from([
                "jobs",
                "steps",
                "env",
                "with",
                "uses",
                "run",
                "strategy",
                "matrix",
                "x",
            ]),
            children,
            max_size=4,
        ),
    ),
    max_leaves=12,
)


@settings(max_examples=80, deadline=None)
@given(_NODES)
def test_a_document_of_any_shape_is_read_without_error(document: object) -> None:
    """Malformed or non-mapping nodes are skipped, never raised on."""
    facts = _read({"jobs": {"j": document}} if document is not None else {})

    assert isinstance(facts["dated_nightlies"], list), facts
