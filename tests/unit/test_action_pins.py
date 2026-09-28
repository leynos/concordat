"""Unit tests for resolving what a GitHub Action's full-SHA pin names.

`concordat.rules.action_pins` turns the `uses:` refs of a workflow into facts
the Markdown policy judges: which 40-hex pins the action carries, and whether
each names a commit, an annotated tag object, or could not be resolved. The
GitHub resolver is driven through a fake session so no test reaches the API.
"""

from __future__ import annotations

import typing as typ

import pytest
import requests

from concordat.rules.action_pins import (
    PinResolution,
    commit_pin,
    github_resolver,
    pinned_shas,
    resolve_pins,
    tag_pin,
    unresolved_pin,
)
from concordat.rules.markdown_envelope import build_markdown_envelope

if typ.TYPE_CHECKING:
    import pathlib

ACTION: typ.Final = "DavidAnson/markdownlint-cli2-action"
COMMIT: typ.Final = "21c1be1b93ad9ed58fa840aacc3f279cde2a72ff"
TAG_OBJECT: typ.Final = "4580e1612f6407034edd6c0e4e316d725920867b"
API: typ.Final = "https://api.example.test"
BEARER: typ.Final = "fixture-bearer"


def _workflow(*uses: str) -> dict[str, object]:
    """Return a decoded workflow whose one job runs a step per `uses` ref."""
    steps = [{"uses": ref} for ref in uses]
    return {
        "path": "ci.yml",
        "parsed": {"jobs": {"lint": {"steps": steps}}},
        "error": None,
    }


class FakeResponse:
    """The slice of `requests.Response` the resolver reads."""

    def __init__(self, status_code: int, body: object = None) -> None:
        """Record the status and the JSON body to return."""
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        """Return the body, or fail as `requests` does on a non-JSON reply."""
        if self._body is None:
            message = "no JSON"
            raise ValueError(message)
        return self._body


class FakeSession:
    """Answer GETs from a table keyed by URL and record every request."""

    def __init__(self, replies: dict[str, FakeResponse | Exception]) -> None:
        """Store the canned replies; an unlisted URL answers 404."""
        self.headers: dict[str, str] = {}
        self.replies = replies
        self.requested: list[str] = []

    def get(self, url: str, *, timeout: float) -> FakeResponse:
        """Return the canned reply for *url*, raising a canned exception."""
        assert timeout > 0
        self.requested.append(url)
        reply = self.replies.get(url, FakeResponse(404, {"message": "Not Found"}))
        if isinstance(reply, Exception):
            raise reply
        return reply


def _commit_url(sha: str) -> str:
    return f"{API}/repos/{ACTION}/git/commits/{sha}"


def _tag_url(sha: str) -> str:
    return f"{API}/repos/{ACTION}/git/tags/{sha}"


def _resolve(session: FakeSession, sha: str) -> dict[str, object]:
    resolver = github_resolver(
        token=BEARER, api_url=API, session=typ.cast("requests.Session", session)
    )
    return dict(resolver(ACTION, sha))


class TestPinnedShas:
    """Only full-SHA refs of the named action are collected."""

    def test_collects_distinct_full_sha_pins_sorted(self) -> None:
        """Repeated pins appear once; floating refs and other actions do not."""
        workflows = [
            _workflow(
                f"{ACTION}@{TAG_OBJECT}", f"{ACTION}@v24", "actions/checkout@" + COMMIT
            ),
            _workflow(f"{ACTION}@{COMMIT}", f"{ACTION}@{TAG_OBJECT}"),
        ]
        assert pinned_shas(workflows, ACTION) == [COMMIT, TAG_OBJECT]

    @pytest.mark.parametrize(
        "workflow",
        [
            pytest.param({"path": "x", "parsed": None, "error": "bad"}, id="undecoded"),
            pytest.param(
                {"path": "x", "parsed": {"jobs": []}, "error": None}, id="jobs-list"
            ),
            pytest.param(
                {
                    "path": "x",
                    "parsed": {"jobs": {"a": {"steps": "no"}}},
                    "error": None,
                },
                id="steps-string",
            ),
            pytest.param(_workflow(f"{ACTION}@{COMMIT.upper()}"), id="uppercase-hex"),
        ],
    )
    def test_malformed_or_non_sha_input_yields_nothing(
        self, workflow: dict[str, object]
    ) -> None:
        """Shapes the policy reports in its own right contribute no pin."""
        assert pinned_shas([workflow], ACTION) == []


def test_resolve_pins_keys_each_pin_by_sha() -> None:
    """Every collected pin is resolved once and keyed by its SHA."""
    answers = {COMMIT: commit_pin(COMMIT), TAG_OBJECT: tag_pin(COMMIT)}
    asked: list[str] = []

    def resolver(repository: str, sha: str) -> PinResolution:
        assert repository == ACTION
        asked.append(sha)
        return answers[sha]

    workflows = [
        _workflow(f"{ACTION}@{COMMIT}", f"{ACTION}@{TAG_OBJECT}"),
        _workflow(f"{ACTION}@{COMMIT}"),
    ]
    assert resolve_pins(workflows, ACTION, resolver) == answers
    assert asked == [COMMIT, TAG_OBJECT]


class TestGithubResolver:
    """The REST resolver tells commits from tag objects and never raises."""

    def test_commit_is_resolved_without_asking_for_a_tag(self) -> None:
        """A SHA the commit endpoint knows is a commit pin."""
        session = FakeSession({_commit_url(COMMIT): FakeResponse(200, {"sha": COMMIT})})
        assert _resolve(session, COMMIT) == commit_pin(COMMIT)
        assert session.requested == [_commit_url(COMMIT)]
        assert session.headers["Authorization"] == f"Bearer {BEARER}"

    def test_tag_object_is_peeled_to_its_commit(self) -> None:
        """A SHA only the tag endpoint knows is a tag naming its target."""
        body = {"object": {"type": "commit", "sha": COMMIT}}
        session = FakeSession({_tag_url(TAG_OBJECT): FakeResponse(200, body)})
        assert _resolve(session, TAG_OBJECT) == tag_pin(COMMIT)

    def test_tag_of_a_tag_does_not_claim_a_commit(self) -> None:
        """A tag whose target is another tag records no peeled commit."""
        body = {"object": {"type": "tag", "sha": COMMIT}}
        session = FakeSession({_tag_url(TAG_OBJECT): FakeResponse(200, body)})
        assert _resolve(session, TAG_OBJECT) == tag_pin(None)

    def test_unknown_object_is_unresolved(self) -> None:
        """A SHA neither endpoint knows is unresolved, not a commit."""
        resolution = _resolve(FakeSession({}), TAG_OBJECT)
        assert resolution["object_type"] is None
        assert "neither a commit nor a tag" in str(resolution["error"])

    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param(FakeResponse(403, {"message": "rate limited"}), id="refused"),
            pytest.param(requests.ConnectionError("offline"), id="offline"),
            pytest.param(FakeResponse(200, None), id="undecodable"),
            pytest.param(
                FakeResponse(200, ["not", "an", "object"]), id="not-an-object"
            ),
        ],
    )
    def test_failure_leaves_the_pin_unresolved(
        self, reply: FakeResponse | Exception
    ) -> None:
        """A refusal or transport failure is reported, never taken as a commit."""
        session = FakeSession({_commit_url(COMMIT): reply})
        resolution = _resolve(session, COMMIT)
        assert resolution["object_type"] is None, resolution
        assert resolution["error"], resolution
        assert session.requested == [_commit_url(COMMIT)]


def test_envelope_without_a_resolver_records_pins_unresolved(
    tmp_path: pathlib.Path,
) -> None:
    """The builder's default resolver fails closed rather than trusting a pin."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        f"jobs:\n  lint:\n    steps:\n      - uses: {ACTION}@{COMMIT}\n",
        encoding="utf-8",
    )
    envelope = build_markdown_envelope(tmp_path)
    assert envelope["action_pins"] == {
        COMMIT: unresolved_pin(f"{ACTION}@{COMMIT} was not resolved: no resolver")
    }
