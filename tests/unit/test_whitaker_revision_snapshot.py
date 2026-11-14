"""Bind the shipped QG-002 defaults to reviewed shared-actions history."""

from __future__ import annotations

import functools
import hashlib
import itertools
import json
import pathlib
import re
import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.whitaker_certification import (
    STATUS_CERTIFIED,
    STATUS_UNCERTIFIED,
    Floor,
    NoSuchCommit,
    certify_revision,
)
from concordat.rules.whitaker_provisioning_envelope import (
    ProvisioningEnvelope,
    build_whitaker_provisioning_envelope,
    with_install_certifications,
)
from tests.unit.conftest_batch_support import invoke_conftest_batch

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_RULE_ID: typ.Final = "whitaker-provisioning"
_ACTION: typ.Final = "leynos/shared-actions/.github/actions/install-whitaker"
_TAG_PREFIX: typ.Final = "certified/install-whitaker/"
# 38905b4b sits between chains e and f and is not approved: it ships the 0.2.12
# default without the version-keyed digests, so its runtime content is its own.
_GAP: typ.Final = "38905b4b2caec5b049438e6dc3f3f6b36c04d2df"
# A later commit on main that leaves chain f's runtime content alone.
_BUMP: typ.Final = "b" * 40
_FIXTURES: typ.Final = pathlib.Path(__file__).parents[1] / "fixtures/whitaker"
_SNAPSHOTS: typ.Final = (
    _FIXTURES / "approved-revisions-2aed718.json",
    _FIXTURES / "approved-revisions-d5fded1.json",
    _FIXTURES / "approved-revisions-5a99aed.json",
    _FIXTURES / "approved-revisions-6e99f22.json",
    _FIXTURES / "approved-revisions-2ef6299.json",
    _FIXTURES / "approved-revisions-0eabb6b.json",
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


def _tag(chain: int) -> str:
    """Return the certification tag a maintainer would create at *chain*'s root."""
    return f"{_TAG_PREFIX}chain-{'abcdef'[chain]}"


class _SnapshotHistory:
    """The reviewed first-parent history as a certification source.

    The commits are the chains in order, with the unapproved gap between chains
    e and f and, optionally, later commits. A commit descends from an earlier
    one in that order; its runtime digest is its chain's. Only the chains named
    in *floor_chains* carry a certification tag, at their root.
    """

    def __init__(
        self,
        snapshots: list[_Snapshot],
        floor_chains: cabc.Iterable[int],
        extra: cabc.Sequence[str],
    ) -> None:
        """Index the commits of *snapshots* and record which roots are tagged."""
        self._order: list[str] = []
        self._digests: dict[str, str] = {_GAP: "unapproved-content"}
        for number, snapshot in enumerate(snapshots):
            if number == len(snapshots) - 1:
                self._order.append(_GAP)
            for commit in snapshot["commits"]:
                self._order.append(commit["revision"])
                self._digests[commit["revision"]] = snapshot["runtime_digest"]
        for sha in extra:
            self._order.append(sha)
            self._digests[sha] = snapshots[-1]["runtime_digest"]
        self._floors = [
            Floor(_tag(number), snapshots[number]["root"]) for number in floor_chains
        ]

    def floors(self) -> list[Floor]:
        """Return the tagged roots."""
        return sorted(self._floors)

    def is_on_default_branch(self, sha: str) -> bool:
        """Report whether *sha* is in the indexed history.

        Returns
        -------
        bool
            Always True for an indexed commit.

        Raises
        ------
        NoSuchCommit
            For a commit outside it, as the API's 404 does.
        """
        if sha not in self._order:
            raise NoSuchCommit(sha)
        return True

    def descends_from(self, floor: str, sha: str) -> bool:
        """Report whether *sha* is at or after *floor* in the history."""
        return self._order.index(sha) >= self._order.index(floor)

    def runtime_digest(self, sha: str) -> str | None:
        """Return the commit's runtime digest."""
        return self._digests.get(sha)


def _history(
    snapshots: list[_Snapshot],
    floor_chains: cabc.Iterable[int],
    extra: cabc.Sequence[str] = (),
) -> _SnapshotHistory:
    """Return the reviewed history with a tag at each chain in *floor_chains*."""
    return _SnapshotHistory(snapshots, floor_chains, extra)


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
            "c7891db19a5b424111dcc693e7400fd949a8ef96",
            "68fcb35324561347c569785e422aca109d572538",
        ),
        (
            "2ef6299e7971fde1aafbcb021e21d98f883db525",
            "6f53870bc36cfac0c0c9893daaee77a5eb4327f4",
            "64bf11a749e66c62cb921e4ef4479f5b26bf1114",
        ),
        (
            "0eabb6bc07148981de5da395e30cd78d83f5c7c2",
            "b2ec32c03a926824d8c716451b4b290a9e102a65",
            "2255888485647779a82692cc5826b89a43d7bc05",
        ),
    ]
    chain_a, chain_b, chain_c, chain_d, chain_e, chain_f = snapshots
    assert chain_a["commits"][0]["first_parent"] == (
        "774cc5a01e457714aede66e14a97b3e257812fc2"
    )
    assert chain_b["commits"][0]["first_parent"] == chain_a["tip"]
    assert chain_c["commits"][0]["first_parent"] == chain_b["tip"]
    assert chain_d["commits"][0]["first_parent"] == chain_c["tip"]
    assert chain_e["commits"][0]["first_parent"] == chain_d["tip"]
    # 38905b4b (installer 0.2.12 default) sits between chains e and f and is not
    # approved: it ships the 0.2.12 default without the version-keyed digests.
    assert chain_f["commits"][0]["first_parent"] == (
        "38905b4b2caec5b049438e6dc3f3f6b36c04d2df"
    )
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
    """Chains c, d and e differ in `tests/` only, so they share one runtime digest.

    The digests were captured with plain Git plumbing (`ls-tree -r`, drop
    `tests/` and Markdown, sort `<mode> <blob> <path>` records, SHA-256), not
    with the module under test. 5a99aed7, 6e99f225 and 872f59f5 run the same
    `action.yml` and helpers; #652 changed only the action's tests.
    """
    digests = [snapshot["runtime_digest"] for snapshot in snapshots]
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests)
    assert len(set(digests)) == 4
    assert digests[2] == digests[3] == digests[4]
    assert snapshots[2]["action_tree"] != snapshots[3]["action_tree"]
    assert snapshots[3]["action_tree"] != snapshots[4]["action_tree"]
    assert digests[5] not in digests[:5]


def test_the_0eabb6bc_root_carries_installer_0_2_12_runtime_content(
    snapshots: list[_Snapshot],
) -> None:
    """The root's runtime content pins installer 0.2.12 and keys snapshots by it.

    The lines were captured from `git show 0eabb6bc:<action>/...` and kept in the
    oracle: one installer digest per platform asset of the default version, and
    snapshot digests keyed by installer version, which differ for the same
    commit's bundle between releases.
    """
    chain = typ.cast("dict[str, typ.Any]", snapshots[5])
    assert chain["default_installer_version"] == "0.2.12"
    installer = [line.split("  ") for line in chain["installer_digests"]]
    assert len(installer) == 5
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest, _ in installer)
    assert all(
        asset.endswith(("-v0.2.12.tgz", "-v0.2.12.zip")) for _, asset in installer
    )
    keyed: dict[tuple[str, str], str] = {}
    for line in chain["snapshot_digests"]:
        digest, asset, version = line.split("  ")
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        keyed[asset, version] = digest
    assert {version for _, version in keyed} == {"0.2.11", "0.2.12"}
    assert len(set(keyed.values())) == len(keyed)


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


def test_floor_tags_at_the_approved_roots_certify_every_reviewed_revision(
    snapshots: list[_Snapshot],
) -> None:
    """Parity with the retired list: every revision it named is certified.

    The reviewed chains are the complete list QG-002 used to ship, captured
    independently with Git plumbing. A certification tag at each approved root
    certifies every one of those revisions, and nothing else on the way.
    """
    source = _history(snapshots, floor_chains=range(len(snapshots)))
    for snapshot in snapshots:
        for commit in snapshot["commits"]:
            verdict = certify_revision(source, commit["revision"])
            assert verdict["status"] == STATUS_CERTIFIED, (commit, verdict)
    assert certify_revision(source, _GAP)["status"] == STATUS_UNCERTIFIED


def test_one_floor_per_installer_release_is_needed(
    snapshots: list[_Snapshot],
) -> None:
    """Only the newest floor leaves the older approved releases uncertified.

    Chains a, b and c-e run different action content from chain f (installer
    0.2.12), so "newer than the newest floor" alone would turn the estate's
    older, approved pins noncompliant: certification is per tagged release.
    """
    source = _history(snapshots, floor_chains=[5])
    older = [c["revision"] for s in snapshots[:5] for c in s["commits"]]
    assert all(
        certify_revision(source, revision)["status"] == STATUS_UNCERTIFIED
        for revision in older
    )
    newest = [c["revision"] for c in snapshots[5]["commits"]]
    assert all(
        certify_revision(source, revision)["status"] == STATUS_CERTIFIED
        for revision in newest
    )


def test_a_later_bump_after_the_newest_floor_is_certified_unchanged(
    snapshots: list[_Snapshot],
) -> None:
    """A commit after the floor that leaves the digest alone needs no new tag.

    This is the shared-actions Dependabot bump that left QG-002 red on its
    own list: its install-whitaker content equals the floor's.
    """
    source = _history(snapshots, floor_chains=range(len(snapshots)), extra=[_BUMP])
    verdict = certify_revision(source, _BUMP)
    assert verdict["status"] == STATUS_CERTIFIED
    assert verdict["floor"] == _tag(5)


def test_qg002_accepts_every_reviewed_revision(
    snapshots: list[_Snapshot],
    revision_envelopes: list[ProvisioningEnvelope],
) -> None:
    """Every expected pin reaches the real policy, certified, with no finding."""
    source = _history(snapshots, floor_chains=range(len(snapshots)))
    certified_envelopes = [
        with_install_certifications(
            envelope,
            functools.partial(certify_revision, source),
            _ACTION,
        )
        for envelope in revision_envelopes
    ]
    results = invoke_conftest_batch(_RULE_ID, certified_envelopes)
    assert len(results) == len(certified_envelopes)
    for envelope, result in zip(certified_envelopes, results, strict=True):
        findings = runner._findings_from_results(result)
        assert not findings, (envelope["repository"], findings)


def test_qg002_cannot_accept_a_reviewed_pin_without_its_certification(
    revision_envelopes: list[ProvisioningEnvelope],
) -> None:
    """The policy accepts a known pin only on a recorded certification.

    An envelope built without the command step records none, and the policy
    reads that as an unchecked pin, never as a pass.
    """
    results = invoke_conftest_batch(_RULE_ID, revision_envelopes)
    assert len(results) == len(revision_envelopes)
    for result in results:
        findings = runner._findings_from_results(result)
        assert any(
            finding.rule_id == "QG-002" and finding.verdict == "indeterminate"
            for finding in findings
        ), findings


def test_qg002_refuses_an_uncertified_reviewed_pin(
    snapshots: list[_Snapshot],
    revision_envelopes: list[ProvisioningEnvelope],
) -> None:
    """With only the newest floor, an older reviewed pin is noncompliant."""
    source = _history(snapshots, floor_chains=[5])
    older = revision_envelopes[0]
    recorded = with_install_certifications(
        older, functools.partial(certify_revision, source), _ACTION
    )
    (result,) = invoke_conftest_batch(_RULE_ID, [recorded])
    findings = runner._findings_from_results(result)
    assert [(f.rule_id, f.verdict) for f in findings] == [("QG-002", "noncompliant")]
