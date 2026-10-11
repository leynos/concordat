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
import re
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
    "rustup_install_alias",
    "rustup_run",
    "rustup_override",
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
        case "rustup_install_alias":
            step = {"run": f"rustup install {tool}"}
        case "rustup_run":
            step = {"run": f"rustup run {tool} cargo check"}
        case "rustup_override":
            step = {"run": f"rustup override set {tool} --path ."}
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


def _workflow(selections: list[_Selection], per_job: int = 1) -> dict[str, object]:
    """Render *selections* as one workflow, `per_job` steps to each job."""
    env: dict[str, str] = {}
    jobs: dict[str, object] = {}
    for start in range(0, len(selections), per_job):
        chunk = selections[start : start + per_job]
        steps: list[dict[str, object]] = []
        for offset, selection in enumerate(chunk):
            step, step_env = _step(start + offset, selection)
            env.update(step_env)
            steps.append(step)
        job: dict[str, object] = {"runs-on": "ubuntu-latest", "steps": steps}
        legs = [s.toolchain for s in chunk if s.route == "matrix_leg"]
        if legs:
            job["strategy"] = {"matrix": {"rust": legs}}
        jobs[f"job{start}"] = job
    return {"env": env, "jobs": jobs} if env else {"jobs": jobs}


def _read(*documents: object) -> dict[str, typ.Any]:
    """Return the extractor's facts for a checkout holding every document."""
    with tempfile.TemporaryDirectory() as scratch:
        workflows = pathlib.Path(scratch) / ".github" / "workflows"
        workflows.mkdir(parents=True)
        for index, document in enumerate(documents):
            with (workflows / f"w{index}.yml").open("w", encoding="utf-8") as handle:
                YAML(typ="safe").dump(document, handle)
        return typ.cast("dict[str, typ.Any]", ci_facts(pathlib.Path(scratch)))


_NUMERIC_TOOLCHAIN: typ.Final = re.compile(r"\d+\.\d+\.\d+")


def _expected(selections: list[_Selection]) -> tuple[list[str], list[str]]:
    """Return the dated nightlies and numeric minors the decisions name.

    The minor is computed here by splitting the generated `M.m.p` text, not by
    the production helper, so a defect in that helper cannot hide itself.

    Returns
    -------
    tuple[list[str], list[str]]
        The sorted dated nightlies and the sorted numeric `major.minor` values.
    """
    names = {s.toolchain for s in selections}
    nightlies = sorted(n for n in names if n.startswith("nightly-"))
    minors = sorted({
        ".".join(n.split(".")[:2]) for n in names if _NUMERIC_TOOLCHAIN.fullmatch(n)
    })
    return nightlies, minors


@settings(max_examples=60, deadline=None)
@given(st.lists(_SELECTIONS, max_size=6), st.integers(1, 3))
def test_every_route_names_exactly_its_toolchains(
    selections: list[_Selection], per_job: int
) -> None:
    """Whatever route selects a toolchain, the facts name it, and nothing else."""
    facts = _read(_workflow(selections, per_job))
    nightlies, minors = _expected(selections)

    assert facts["errors"] == [], facts
    assert facts["dated_nightlies"] == nightlies, (selections, facts)
    assert facts["numeric_minors"] == minors, (selections, facts)


@settings(max_examples=40, deadline=None)
@given(
    st.lists(_SELECTIONS, min_size=1, max_size=6),
    st.integers(1, 3),
    st.integers(1, 3),
    st.randoms(use_true_random=False),
)
def test_order_grouping_and_duplication_do_not_change_the_facts(
    selections: list[_Selection],
    per_job: int,
    files: int,
    rng: random.Random,
) -> None:
    """The facts are a set: steps, jobs and files can be reordered or repeated."""
    shuffled = list(selections) + list(selections)
    rng.shuffle(shuffled)
    chunks = [shuffled[i::files] for i in range(files)]

    split = _read(*(_workflow(chunk, per_job) for chunk in chunks if chunk))

    assert split == _read(_workflow(selections)), (selections, split)


@settings(max_examples=80, deadline=None)
@given(st.integers(0, 999), st.integers(0, 999), st.integers(0, 999))
def test_numeric_versions_reduce_to_their_major_and_minor(
    major: int, minor: int, patch: int
) -> None:
    """`M.m` and `M.m.p` both reduce to `M.m`."""
    assert minor_of(f"{major}.{minor}") == f"{major}.{minor}"
    assert minor_of(f"{major}.{minor}.{patch}") == f"{major}.{minor}"


@settings(max_examples=120, deadline=None)
@given(
    st.text(max_size=12).filter(
        lambda text: re.fullmatch(r"\d+\.\d+(\.\d+)?", text) is None
    )
)
def test_anything_else_has_no_numeric_minor(text: str) -> None:
    """Channels, dates, partial versions and trailing noise are not numeric."""
    assert minor_of(text) is None, text


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
