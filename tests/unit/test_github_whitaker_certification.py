"""Specify the GitHub adapter behind QG-002's certification of install-whitaker."""

from __future__ import annotations

import typing as typ

import pytest
import requests

from concordat.auditor.github import (
    GithubError,
    GithubRateLimitError,
)
from concordat.rules.github_whitaker_certification import (
    GithubCertificationSource,
    GithubCertifier,
    github_certifier,
)
from concordat.rules.whitaker_certification import (
    STATUS_CERTIFIED,
    STATUS_UNCERTIFIED,
    STATUS_UNKNOWN,
    CertificationUnavailable,
    Floor,
    NoSuchCommit,
    runtime_digest_of,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from concordat.auditor.github import GithubClient

TAG: typ.Final = "certified/install-whitaker/0.2.12"
FLOOR: typ.Final = "f" * 40
PIN: typ.Final = "a" * 40
DIRECTORY: typ.Final = ".github/actions/install-whitaker"
ACTION_TREE: typ.Final = [
    {"path": "action.yml", "mode": "100644", "type": "blob", "sha": "1" * 40},
    {"path": "scripts", "mode": "040000", "type": "tree", "sha": "2" * 40},
    {"path": "scripts/run.sh", "mode": "100755", "type": "blob", "sha": "3" * 40},
    {"path": "tests/t.py", "mode": "100644", "type": "blob", "sha": "4" * 40},
    {"path": "README.md", "mode": "100644", "type": "blob", "sha": "5" * 40},
]
ACTION_DIGEST: typ.Final = runtime_digest_of([
    ("action.yml", "100644", "1" * 40),
    ("scripts/run.sh", "100755", "3" * 40),
])


class FakeClient:
    """A GitHub client answering from tables, counting the calls it gets."""

    def __init__(self) -> None:
        """Start with one annotated floor tag and an action on main."""
        self.refs: list[dict[str, typ.Any]] | Exception = [
            {
                "ref": f"refs/tags/{TAG}",
                "object": {"type": "tag", "sha": "7" * 40},
            }
        ]
        self.tags: dict[str, dict[str, typ.Any] | None] = {
            "7" * 40: {"object": {"type": "commit", "sha": FLOOR}}
        }
        self.comparisons: dict[tuple[str, str], str | Exception | None] = {
            (PIN, "main"): "ahead",
            (FLOOR, PIN): "ahead",
        }
        self.trees: dict[str, dict[str, typ.Any] | Exception | None] = {
            f"{PIN}:{DIRECTORY}": {"tree": ACTION_TREE, "truncated": False},
            f"{FLOOR}:{DIRECTORY}": {"tree": ACTION_TREE, "truncated": False},
        }
        self.calls: list[str] = []

    def matching_refs(
        self, _owner: str, _name: str, namespace: str
    ) -> list[dict[str, typ.Any]]:
        """Return the configured refs, or raise the configured failure."""
        self.calls.append(f"refs:{namespace}")
        if isinstance(self.refs, Exception):
            raise self.refs
        return self.refs

    def git_tag(self, _owner: str, _name: str, sha: str) -> dict[str, typ.Any] | None:
        """Return the annotated tag object for *sha*."""
        self.calls.append(f"tag:{sha}")
        return self.tags.get(sha)

    def compare_status(
        self, _owner: str, _name: str, base: str, head: str
    ) -> str | None:
        """Return the comparison, None when unknown, or raise the failure."""
        self.calls.append(f"compare:{base}...{head}")
        answer = self.comparisons.get((base, head))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def git_tree(
        self, _owner: str, _name: str, treeish: str, *, recursive: bool = False
    ) -> dict[str, typ.Any] | None:
        """Return the tree for *treeish*, None when absent, or raise."""
        assert recursive  # the adapter must ask for the whole subtree.
        self.calls.append(f"tree:{treeish}")
        answer = self.trees.get(treeish)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _source(client: FakeClient) -> GithubCertificationSource:
    """Return a source over *client* for the shared-actions action."""
    return GithubCertificationSource(
        lambda: typ.cast("GithubClient", client),
        "leynos/shared-actions",
        DIRECTORY,
        tag_prefix=TAG[:-6],
    )


def _verdict(client: FakeClient, sha: str = PIN) -> dict[str, typ.Any]:
    """Return the certifier's verdict for *sha* over *client*."""
    return dict(GithubCertifier(_source(client))(sha))


def test_a_descendant_of_an_annotated_floor_with_equal_content_is_certified() -> None:
    """The whole path: peel the tag, compare, read both trees, match digests."""
    verdict = _verdict(FakeClient())

    assert verdict == {"status": STATUS_CERTIFIED, "floor": TAG, "reason": None}


def test_the_tag_listing_is_scoped_to_the_prefix() -> None:
    """Only tags under the certification prefix are asked for."""
    client = FakeClient()

    _verdict(client)

    assert "refs:tags/certified/install-whitaker/" in client.calls


def test_a_lightweight_tag_points_straight_at_a_commit() -> None:
    """A tag made without a message needs no peeling."""
    client = FakeClient()
    client.refs = [
        {"ref": f"refs/tags/{TAG}", "object": {"type": "commit", "sha": FLOOR}}
    ]

    assert _verdict(client)["status"] == STATUS_CERTIFIED
    assert not [call for call in client.calls if call.startswith("tag:")]


def test_changed_runtime_content_is_not_certified() -> None:
    """The pin's tree differs from the floor's in a runtime file."""
    client = FakeClient()
    changed = [dict(entry) for entry in ACTION_TREE]
    changed[0]["sha"] = "9" * 40
    client.trees[f"{PIN}:{DIRECTORY}"] = {"tree": changed, "truncated": False}

    verdict = _verdict(client)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert "runtime content" in verdict["reason"]


def test_a_change_confined_to_tests_and_markdown_is_certified() -> None:
    """Excluded paths move nothing."""
    client = FakeClient()
    edited = [dict(entry) for entry in ACTION_TREE]
    edited[3]["sha"] = "8" * 40
    edited[4]["sha"] = "8" * 40
    client.trees[f"{PIN}:{DIRECTORY}"] = {"tree": edited, "truncated": False}

    assert _verdict(client)["status"] == STATUS_CERTIFIED


def test_a_commit_not_ancestral_to_main_is_not_certified() -> None:
    """A fork-only SHA: GitHub can compare it, but main is not ahead of it."""
    client = FakeClient()
    client.comparisons[PIN, "main"] = "diverged"

    verdict = _verdict(client)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert "default branch" in verdict["reason"]


def test_an_unknown_commit_is_not_certified() -> None:
    """GitHub answering 404 for the revision is a definite no."""
    client = FakeClient()
    client.comparisons[PIN, "main"] = None

    verdict = _verdict(client)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert "names no commit" in verdict["reason"]


def test_a_commit_older_than_the_floor_is_not_certified() -> None:
    """Main is ahead of it, but the floor is not behind it."""
    client = FakeClient()
    client.comparisons[FLOOR, PIN] = "behind"

    verdict = _verdict(client)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert "older than" in verdict["reason"]


def test_a_revision_without_the_directory_is_not_certified() -> None:
    """A tree lookup that finds nothing means the commit has no action."""
    client = FakeClient()
    client.trees[f"{PIN}:{DIRECTORY}"] = None

    verdict = _verdict(client)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert "no install-whitaker directory" in verdict["reason"]


def test_no_tags_certifies_nothing() -> None:
    """A repository with no certification tag certifies no revision."""
    client = FakeClient()
    client.refs = []

    verdict = _verdict(client)

    assert verdict["status"] == STATUS_UNCERTIFIED
    assert "no certification tags" in verdict["reason"]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        pytest.param(GithubRateLimitError("limit spent"), "rate limited", id="limit"),
        pytest.param(GithubError("boom"), "could not be read", id="http"),
        pytest.param(
            requests.ConnectionError("offline"), "could not be read", id="offline"
        ),
        pytest.param(ValueError("not json"), "could not be read", id="bad-body"),
    ],
)
@pytest.mark.parametrize("where", ["refs", "compare", "tree"])
def test_a_failing_api_is_unknown_not_a_refusal(
    where: str, failure: Exception, expected: str
) -> None:
    """Whichever lookup fails, the verdict is unknown with the reason."""
    client = FakeClient()
    if where == "refs":
        client.refs = failure
    elif where == "compare":
        client.comparisons[PIN, "main"] = failure
    else:
        client.trees[f"{PIN}:{DIRECTORY}"] = failure

    verdict = _verdict(client)

    assert verdict["status"] == STATUS_UNKNOWN
    assert expected in verdict["reason"]


def test_a_truncated_tree_is_unknown() -> None:
    """A partial listing would digest part of the directory, so it is refused."""
    client = FakeClient()
    client.trees[f"{PIN}:{DIRECTORY}"] = {"tree": ACTION_TREE, "truncated": True}

    verdict = _verdict(client)

    assert verdict["status"] == STATUS_UNKNOWN
    assert "truncated" in verdict["reason"]


def test_a_tag_that_does_not_lead_to_a_commit_is_unknown() -> None:
    """A tag on a tree or blob cannot be a floor, and the audit says so."""
    client = FakeClient()
    client.refs = [
        {"ref": f"refs/tags/{TAG}", "object": {"type": "tree", "sha": "6" * 40}}
    ]

    verdict = _verdict(client)

    assert verdict["status"] == STATUS_UNKNOWN
    assert "does not point at a commit" in verdict["reason"]


def test_what_was_learned_is_asked_once_per_run() -> None:
    """The floors, a comparison and a digest are each read once, for any pin."""
    client = FakeClient()
    certifier = GithubCertifier(_source(client))

    certifier(PIN)
    certifier(PIN)

    assert len(client.calls) == len(set(client.calls))


def test_the_client_is_built_only_when_a_question_is_asked() -> None:
    """A checkout that pins nothing reads no credentials."""
    built: list[str] = []

    def factory() -> FakeClient:
        """Record the build."""
        built.append("client")
        return FakeClient()

    certifier = github_certifier(typ.cast("cabc.Callable[..., typ.Any]", factory), {})
    assert built == []

    certifier(PIN)
    assert built == ["client"]


def test_the_parameters_say_where_the_action_lives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule's parameters, not constants, name the repository and the tags."""
    client = FakeClient()
    client.refs = []
    seen: list[tuple[str, str]] = []

    def matching_refs(owner: str, name: str, namespace: str) -> list[dict[str, str]]:
        """Record where the adapter looked for tags."""
        seen.append((f"{owner}/{name}", namespace))
        return []

    monkeypatch.setattr(client, "matching_refs", matching_refs)
    certifier = github_certifier(
        lambda: typ.cast("typ.Any", client),
        {
            "action_repository": "acme/actions",
            "action_directory": "x/y",
            "certification_tag_prefix": "ok/",
            "action_branch": "trunk",
        },
    )
    client.comparisons[PIN, "trunk"] = "identical"

    certifier(PIN)

    assert seen == [("acme/actions", "tags/ok/")]


def test_the_source_raises_the_documented_exceptions() -> None:
    """The protocol's exceptions are what the decision catches."""
    client = FakeClient()
    client.comparisons[PIN, "main"] = None
    source = _source(client)

    with pytest.raises(NoSuchCommit):
        source.is_on_default_branch(PIN)

    client.trees[f"{PIN}:{DIRECTORY}"] = GithubError("boom")
    with pytest.raises(CertificationUnavailable):
        source.runtime_digest(PIN)
    assert source.floors() == [Floor(TAG, FLOOR)]
