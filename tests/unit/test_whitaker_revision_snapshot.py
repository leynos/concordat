"""Bind the shipped QG-002 defaults to reviewed shared-actions history."""

from __future__ import annotations

import itertools
import json
import pathlib
import typing as typ

import pytest

from concordat.rules import packages, runner
from concordat.rules.whitaker_provisioning_envelope import (
    ProvisioningEnvelope,
    build_whitaker_provisioning_envelope,
)
from concordat.rules.whitaker_revisions import REFS_KEY
from tests.unit.conftest_batch_support import invoke_conftest_batch

_RULE_ID: typ.Final = "whitaker-provisioning"
_SNAPSHOT: typ.Final = (
    pathlib.Path(__file__).parents[1]
    / "fixtures/whitaker/approved-revisions-e693d81.json"
)


class _Commit(typ.TypedDict):
    """Record one immutable first-parent commit and its installer tree."""

    revision: str
    first_parent: str
    action_tree: str


class _Snapshot(typ.TypedDict):
    """Describe the independently captured approved installer history."""

    repository: str
    tip: str
    root: str
    action_directory: str
    action_tree: str
    commits: list[_Commit]


@pytest.fixture
def snapshot() -> _Snapshot:
    """Read the frozen Git oracle without consulting the shipped manifest."""
    return typ.cast("_Snapshot", json.loads(_SNAPSHOT.read_text(encoding="utf-8")))


@pytest.fixture
def revision_envelopes(
    snapshot: _Snapshot, tmp_path: pathlib.Path
) -> list[ProvisioningEnvelope]:
    """Decode a real consumer workflow for each independently expected pin."""
    envelopes = []
    for commit in snapshot["commits"]:
        root = tmp_path / commit["revision"]
        workflow = root / ".github/workflows/ci.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "on: pull_request\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: leynos/shared-actions/.github/actions/"
            f"install-whitaker@{commit['revision']}\n",
            encoding="utf-8",
        )
        envelopes.append(build_whitaker_provisioning_envelope(root))
    return envelopes


def test_snapshot_records_the_complete_reviewed_first_parent_segment(
    snapshot: _Snapshot,
) -> None:
    """The captured root-to-tip chain is contiguous and content-identical."""
    commits = snapshot["commits"]
    assert snapshot["repository"] == "leynos/shared-actions"
    assert snapshot["root"] == "6dea5677a84fec60ca51b07202570e3af12ffdb4"
    assert snapshot["tip"] == "e693d81d176d2305e2b736a0be0f9038085dec7f"
    assert snapshot["action_directory"] == ".github/actions/install-whitaker"
    assert snapshot["action_tree"] == "e37c663890d62c79187175a37189d58c02266e12"
    assert len(commits) == 68
    assert commits[0]["revision"] == snapshot["root"]
    assert commits[0]["first_parent"] == "774cc5a01e457714aede66e14a97b3e257812fc2"
    assert commits[-1]["revision"] == snapshot["tip"]
    assert len({commit["revision"] for commit in commits}) == len(commits)
    assert all(commit["action_tree"] == snapshot["action_tree"] for commit in commits)
    for parent, child in itertools.pairwise(commits):
        assert child["first_parent"] == parent["revision"]


def test_shipped_defaults_equal_the_independent_complete_history(
    snapshot: _Snapshot,
) -> None:
    """Omitting, mistyping or adding a pin changes the reviewed expected set."""
    parameters = packages.rule_parameters(packages.rule_package_dir(_RULE_ID))
    assert parameters[REFS_KEY] == [
        commit["revision"] for commit in snapshot["commits"]
    ]


def test_qg002_accepts_every_reviewed_revision(
    revision_envelopes: list[ProvisioningEnvelope],
) -> None:
    """Every expected pin reaches the real policy with shipped parameters."""
    results = invoke_conftest_batch(_RULE_ID, revision_envelopes)
    assert len(results) == 68
    for envelope, result in zip(revision_envelopes, results, strict=True):
        findings = runner._findings_from_results(result)
        assert not findings, (envelope["repository"], findings)


@pytest.mark.parametrize("mutation", ["omitted", "altered"])
def test_qg002_refuses_expected_pins_without_their_manifest_entries(
    revision_envelopes: list[ProvisioningEnvelope],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    """The policy cannot accept a known pin independently of its manifest entry."""
    parameters = packages.rule_parameters(packages.rule_package_dir(_RULE_ID))
    parameters[REFS_KEY] = [] if mutation == "omitted" else ["f" * 40]
    monkeypatch.setattr(runner, "_rule_parameters", lambda _directory: parameters)
    results = invoke_conftest_batch(_RULE_ID, revision_envelopes)
    assert len(results) == 68
    for result in results:
        findings = runner._findings_from_results(result)
        assert any(
            finding.rule_id == "QG-002" and finding.verdict == "noncompliant"
            for finding in findings
        ), findings
