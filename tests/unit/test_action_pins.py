"""Unit and property tests for collecting and resolving action pins.

`concordat.rules.action_pins` is the transport-free half of PD-006's pin
resolution: it finds the full-SHA pins of an action in decoded workflows and
asks a resolver about each distinct one. `build_markdown_envelope` stays a
query and `with_action_pins` is the step that calls the resolver; both are
exercised here without any network.
"""

from __future__ import annotations

import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from concordat.rules.action_pins import (
    PinResolution,
    commit_pin,
    pinned_shas,
    resolve_pins,
    tag_pin,
)
from concordat.rules.markdown_envelope import build_markdown_envelope, with_action_pins

if typ.TYPE_CHECKING:
    import pathlib

ACTION: typ.Final = "DavidAnson/markdownlint-cli2-action"
OTHER_ACTION: typ.Final = "actions/checkout"
COMMIT: typ.Final = "21c1be1b93ad9ed58fa840aacc3f279cde2a72ff"
TAG_OBJECT: typ.Final = "4580e1612f6407034edd6c0e4e316d725920867b"

_HEX: typ.Final = "0123456789abcdef"
_FULL_SHAS: typ.Final = st.text(alphabet=_HEX, min_size=40, max_size=40)
# Refs that must never be collected: floating tags, short or long SHAs, and
# upper-case hexadecimal, which the policy does not treat as a full SHA.
_OTHER_REFS: typ.Final = st.one_of(
    st.sampled_from(["v24", "main", "v24.2.0"]),
    st.text(alphabet=_HEX, min_size=1, max_size=39),
    st.text(alphabet=_HEX, min_size=41, max_size=44),
    _FULL_SHAS.map(str.upper).filter(lambda ref: ref != ref.lower()),
)
_MALFORMED: typ.Final[tuple[dict[str, object], ...]] = (
    {"path": "bad.yml", "parsed": None, "error": "undecodable"},
    {"path": "list.yml", "parsed": {"jobs": []}, "error": None},
    {"path": "steps.yml", "parsed": {"jobs": {"a": {"steps": "no"}}}, "error": None},
)


def _workflow(*uses: object) -> dict[str, object]:
    """Return a decoded workflow whose one job runs a step per `uses` value."""
    steps = [{"uses": ref} for ref in uses]
    return {
        "path": "ci.yml",
        "parsed": {"jobs": {"lint": {"steps": steps}}},
        "error": None,
    }


def _draw_uses(draw: st.DrawFn, kind: str, expected: set[str]) -> object:
    """Draw one `uses:` value of *kind*, recording a target pin in *expected*."""
    if kind == "pin":
        sha = draw(_FULL_SHAS)
        expected.add(sha)
        return f"{ACTION}@{sha}"
    if kind == "other":
        return f"{ACTION}@{draw(_OTHER_REFS)}"
    if kind == "action":
        return f"{OTHER_ACTION}@{draw(_FULL_SHAS)}"
    return draw(st.one_of(st.none(), st.integers(), st.just(["x"])))


@st.composite
def _workflows(draw: st.DrawFn) -> tuple[list[dict[str, object]], set[str]]:
    """Draw workflows mixing target pins with every shape that must be ignored.

    Returns
    -------
    tuple[list[dict[str, object]], set[str]]
        The workflows, and the set of full-SHA pins of `ACTION` they hold.
    """
    expected: set[str] = set()
    kinds = st.lists(st.sampled_from(["pin", "other", "action", "junk"]), max_size=6)
    workflows = [
        _workflow(*(_draw_uses(draw, kind, expected) for kind in draw(kinds)))
        for _ in range(draw(st.integers(min_value=0, max_value=4)))
    ]
    workflows.extend(draw(st.lists(st.sampled_from(_MALFORMED), max_size=3)))
    return draw(st.permutations(workflows)), expected


@given(_workflows())
def test_pinned_shas_are_exactly_the_sorted_distinct_target_pins(
    case: tuple[list[dict[str, object]], set[str]],
) -> None:
    """Every full-SHA pin of the action is collected once, in sorted order.

    Other actions, floating or malformed refs, non-string `uses:` values and
    undecodable workflows contribute nothing, whatever their order.
    """
    workflows, expected = case
    assert pinned_shas(workflows, ACTION) == sorted(expected)


@given(st.data())
def test_pinned_shas_do_not_depend_on_workflow_order(data: st.DataObject) -> None:
    """Reordering the workflows leaves the collected pins unchanged."""
    workflows, _ = data.draw(_workflows())
    shuffled = data.draw(st.permutations(workflows))
    assert pinned_shas(shuffled, ACTION) == pinned_shas(workflows, ACTION)


@given(_workflows())
def test_resolve_pins_asks_once_per_distinct_pin(
    case: tuple[list[dict[str, object]], set[str]],
) -> None:
    """The resolver sees each distinct pin once, and its answers key the result."""
    workflows, expected = case
    asked: list[tuple[str, str]] = []

    def resolver(repository: str, sha: str) -> PinResolution:
        asked.append((repository, sha))
        return commit_pin(sha)

    resolved = resolve_pins(workflows, ACTION, resolver)
    assert sorted(asked) == [(ACTION, sha) for sha in sorted(expected)]
    assert resolved == {sha: commit_pin(sha) for sha in expected}


@pytest.mark.parametrize(
    "workflow",
    [
        pytest.param(_MALFORMED[0], id="undecoded"),
        pytest.param(_MALFORMED[1], id="jobs-list"),
        pytest.param(_workflow(f"{ACTION}@{COMMIT.upper()}"), id="uppercase-hex"),
        pytest.param(_workflow(f"{OTHER_ACTION}@{COMMIT}"), id="other-action"),
    ],
)
def test_malformed_or_non_sha_input_yields_nothing(workflow: dict[str, object]) -> None:
    """Shapes the policy reports in its own right contribute no pin."""
    assert pinned_shas([workflow], ACTION) == []


def _checkout_pinning(tmp_path: pathlib.Path, *shas: str) -> pathlib.Path:
    """Return a checkout whose one workflow pins `ACTION` to each of *shas*."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    steps = "".join(f"      - uses: {ACTION}@{sha}\n" for sha in shas)
    (workflows / "ci.yml").write_text(
        f"jobs:\n  lint:\n    steps:\n{steps}", encoding="utf-8"
    )
    return tmp_path


def test_building_the_envelope_resolves_nothing(tmp_path: pathlib.Path) -> None:
    """The builder is a query: pins are left for the command step to resolve."""
    envelope = build_markdown_envelope(_checkout_pinning(tmp_path, COMMIT))
    assert envelope["action_pins"] == {}


def test_with_action_pins_records_the_resolver_answers(tmp_path: pathlib.Path) -> None:
    """The command step asks about each pin and records the answers, copying."""
    envelope = build_markdown_envelope(_checkout_pinning(tmp_path, COMMIT, TAG_OBJECT))
    answers = {COMMIT: commit_pin(COMMIT), TAG_OBJECT: tag_pin(COMMIT)}
    resolved = with_action_pins(envelope, lambda _repository, sha: answers[sha])
    assert resolved["action_pins"] == answers
    assert envelope["action_pins"] == {}


def test_with_action_pins_reads_the_named_action(tmp_path: pathlib.Path) -> None:
    """A non-default action is the one whose pins are collected and asked about."""
    envelope = build_markdown_envelope(_checkout_pinning(tmp_path, COMMIT))
    asked: list[str] = []

    def resolver(repository: str, sha: str) -> PinResolution:
        asked.append(repository)
        return commit_pin(sha)

    assert with_action_pins(envelope, resolver, OTHER_ACTION)["action_pins"] == {}
    assert with_action_pins(envelope, resolver, ACTION)["action_pins"] == {
        COMMIT: commit_pin(COMMIT)
    }
    assert asked == [ACTION]
