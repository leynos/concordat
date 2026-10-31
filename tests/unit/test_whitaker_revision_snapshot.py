"""Bind the shipped QG-002 defaults to reviewed shared-actions history."""

from __future__ import annotations

import hashlib
import itertools
import json
import pathlib
import re
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
_FIXTURES: typ.Final = pathlib.Path(__file__).parents[1] / "fixtures/whitaker"
_SNAPSHOTS: typ.Final = (
    _FIXTURES / "approved-revisions-2aed718.json",
    _FIXTURES / "approved-revisions-d5fded1.json",
    _FIXTURES / "approved-revisions-5a99aed.json",
    _FIXTURES / "approved-revisions-6e99f22.json",
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
    runtime_digest: str
    runtime_records: list[str]
    commits: list[_Commit]


@pytest.fixture
def snapshots() -> list[_Snapshot]:
    """Read the frozen Git oracles, in root order, without the shipped manifest."""
    return [
        typ.cast("_Snapshot", json.loads(path.read_text(encoding="utf-8")))
        for path in _SNAPSHOTS
    ]


@pytest.fixture
def revision_envelopes(
    snapshots: list[_Snapshot], tmp_path: pathlib.Path
) -> list[ProvisioningEnvelope]:
    """Decode a real consumer workflow for each independently expected pin."""
    envelopes = []
    commits = [commit for snapshot in snapshots for commit in snapshot["commits"]]
    for commit in commits:
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
    snapshots: list[_Snapshot],
) -> None:
    """The captured root-to-tip chains are contiguous and content-identical."""
    assert [(s["root"], s["tip"], s["action_tree"]) for s in snapshots] == [
        (
            "6dea5677a84fec60ca51b07202570e3af12ffdb4",
            "2aed718022775671809f8c85a2c10547dfb1ae13",
            "e37c663890d62c79187175a37189d58c02266e12",
        ),
        (
            "d5fded1107ee6822fdbf9539d81ff18ce6ce073a",
            "8e6a325765ba382691d1b1c65fcccf1fc1d1b00c",
            "9230ba5c24773e5e8bbbf056060ca2da39ea9267",
        ),
        (
            "5a99aed78ce68dcebd3052b75c31e6f173162b1e",
            "5a99aed78ce68dcebd3052b75c31e6f173162b1e",
            "183e1bb43c8fc198afc79f41482689898eb42efc",
        ),
        (
            "6e99f225cb06684f9e6f89b63a7ca4a04095c067",
            "872f59f53717b5bebe8e24b2f4a7496b089a939a",
            "68fcb35324561347c569785e422aca109d572538",
        ),
    ]
    chain_a, chain_b, chain_c, chain_d = snapshots
    assert chain_a["commits"][0]["first_parent"] == (
        "774cc5a01e457714aede66e14a97b3e257812fc2"
    )
    assert chain_b["commits"][0]["first_parent"] == chain_a["tip"]
    assert chain_c["commits"][0]["first_parent"] == chain_b["tip"]
    assert chain_d["commits"][0]["first_parent"] == chain_c["tip"]
    for snapshot in snapshots:
        commits = snapshot["commits"]
        assert snapshot["repository"] == "leynos/shared-actions"
        assert snapshot["action_directory"] == ".github/actions/install-whitaker"
        assert commits[0]["revision"] == snapshot["root"]
        assert commits[-1]["revision"] == snapshot["tip"]
        assert len({commit["revision"] for commit in commits}) == len(commits)
        assert all(
            commit["action_tree"] == snapshot["action_tree"] for commit in commits
        )
        for parent, child in itertools.pairwise(commits):
            assert child["first_parent"] == parent["revision"]


def test_runtime_digests_record_which_chains_run_the_same_action(
    snapshots: list[_Snapshot],
) -> None:
    """Chains c and d differ in `tests/` only, so they share one runtime digest.

    The digests were captured with plain Git plumbing (`ls-tree -r`, drop
    `tests/` and Markdown, sort `<mode> <blob> <path>` records, SHA-256), not
    with the module under test. 5a99aed7, 6e99f225 and 872f59f5 run the same
    `action.yml` and helpers; #652 changed only the action's tests.
    """
    digests = [snapshot["runtime_digest"] for snapshot in snapshots]
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests)
    assert len(set(digests)) == 3
    assert digests[2] == digests[3]
    assert snapshots[2]["action_tree"] != snapshots[3]["action_tree"]


def test_runtime_digests_recompute_from_their_recorded_preimage(
    snapshots: list[_Snapshot],
) -> None:
    """Each digest is the SHA-256 of its sorted `<mode> <blob> <path>` records.

    The records were captured with `git ls-tree -r` at the chain's root, dropping
    `tests/` and Markdown paths, then sorted bytewise; the digest hashes them
    joined with a newline after each. Recomputing needs no clone, so a wrong
    digest or a wrong record fails here.
    """
    for snapshot in snapshots:
        records = snapshot["runtime_records"]
        assert records == sorted(records)
        assert all(
            re.fullmatch(r"\d{6} [0-9a-f]{40} \S+", record) for record in records
        )
        assert not any(
            record.split(" ", 2)[2].startswith("tests/") or record.endswith(".md")
            for record in records
        )
        preimage = "".join(f"{record}\n" for record in records).encode()
        assert hashlib.sha256(preimage).hexdigest() == snapshot["runtime_digest"]


def test_shipped_defaults_equal_the_independent_complete_history(
    snapshots: list[_Snapshot],
) -> None:
    """Omitting, mistyping or adding a pin changes the reviewed expected set."""
    parameters = packages.rule_parameters(packages.rule_package_dir(_RULE_ID))
    assert parameters[REFS_KEY] == [
        commit["revision"] for snapshot in snapshots for commit in snapshot["commits"]
    ]


def test_qg002_accepts_every_reviewed_revision(
    revision_envelopes: list[ProvisioningEnvelope],
) -> None:
    """Every expected pin reaches the real policy with shipped parameters."""
    results = invoke_conftest_batch(_RULE_ID, revision_envelopes)
    assert len(results) == len(revision_envelopes)
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
    assert len(results) == len(revision_envelopes)
    for result in results:
        findings = runner._findings_from_results(result)
        assert any(
            finding.rule_id == "QG-002" and finding.verdict == "noncompliant"
            for finding in findings
        ), findings
