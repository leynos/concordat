"""Unit tests for the GitHub adapter that resolves action pins.

`GithubPinResolver` drives the shared `GithubClient`, whose session is
replaced by a table of canned replies, so every status, transport failure and
body shape the adapter translates is exercised without the network.
"""

from __future__ import annotations

import itertools
import typing as typ

import pytest
import requests
from hypothesis import given
from hypothesis import strategies as st

from concordat.auditor.github import GithubClient
from concordat.rules.action_pins import commit_pin, tag_pin
from concordat.rules.github_pins import GithubPinResolver

ACTION: typ.Final = "DavidAnson/markdownlint-cli2-action"
COMMIT: typ.Final = "21c1be1b93ad9ed58fa840aacc3f279cde2a72ff"
TAG_OBJECT: typ.Final = "4580e1612f6407034edd6c0e4e316d725920867b"
API: typ.Final = "https://api.example.test"


class _Response:
    """The slice of `requests.Response` the client reads."""

    def __init__(
        self,
        status_code: int,
        body: object = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Record the status, the JSON body and the headers to return."""
        self.status_code = status_code
        self._body = body
        self.text = "" if body is None else str(body)
        self.headers = headers or {}

    def json(self) -> object:
        """Return the body, or fail as `requests` does on a non-JSON reply."""
        if self._body is None:
            message = "no JSON"
            raise ValueError(message)
        return self._body


class _Session:
    """Answer requests by path from a table, recording each path asked."""

    def __init__(self, routes: dict[str, _Response | Exception]) -> None:
        """Serve *routes*, keyed by the path after the API root."""
        self.routes = routes
        self.requested: list[str] = []

    def request(self, method: str, url: str, **_kwargs: object) -> _Response:
        """Return the route for *url*, raise its exception, or answer 404."""
        assert method == "GET", method
        path = url.removeprefix(API)
        self.requested.append(path)
        reply = self.routes.get(path, _Response(404, {"message": "Not Found"}))
        if isinstance(reply, Exception):
            raise reply
        return reply


def _commit(sha: str) -> str:
    return f"/repos/{ACTION}/git/commits/{sha}"


def _tag(sha: str) -> str:
    return f"/repos/{ACTION}/git/tags/{sha}"


def _resolver(
    routes: dict[str, _Response | Exception],
) -> tuple[GithubPinResolver, _Session]:
    """Build a resolver whose client answers from *routes*."""
    session = _Session(routes)

    def factory() -> GithubClient:
        client = GithubClient(token=None, api_url=API)
        client.session = typ.cast("typ.Any", session)
        return client

    return GithubPinResolver(factory), session


def test_a_commit_is_resolved_without_asking_for_a_tag() -> None:
    """A SHA the commit endpoint knows is a commit pin."""
    resolver, session = _resolver({_commit(COMMIT): _Response(200, {"sha": COMMIT})})
    assert resolver(ACTION, COMMIT) == commit_pin(COMMIT)
    assert session.requested == [_commit(COMMIT)]


def test_a_tag_object_is_peeled_to_its_commit() -> None:
    """A SHA only the tag endpoint knows is a tag naming its target."""
    body = {"object": {"type": "commit", "sha": COMMIT}}
    resolver, session = _resolver({_tag(TAG_OBJECT): _Response(200, body)})
    assert resolver(ACTION, TAG_OBJECT) == tag_pin(COMMIT)
    assert session.requested == [_commit(TAG_OBJECT), _tag(TAG_OBJECT)]


def test_a_tag_of_a_tag_claims_no_commit() -> None:
    """A tag whose target is another tag records no peeled commit."""
    body = {"object": {"type": "tag", "sha": COMMIT}}
    resolver, _ = _resolver({_tag(TAG_OBJECT): _Response(200, body)})
    assert resolver(ACTION, TAG_OBJECT) == tag_pin(None)


def test_an_unknown_object_is_unresolved() -> None:
    """A SHA neither endpoint knows is unresolved, not a commit."""
    resolver, _ = _resolver({})
    resolution = resolver(ACTION, TAG_OBJECT)
    assert resolution["object_type"] is None
    assert "neither a commit nor a tag" in str(resolution["error"])


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(_Response(403, {"message": "rate limited"}), id="refused"),
        pytest.param(_Response(422, {"message": "unprocessable"}), id="unprocessable"),
        pytest.param(_Response(500, {"message": "boom"}), id="server-error"),
        pytest.param(requests.ConnectionError("offline"), id="offline"),
        pytest.param(_Response(200, None), id="undecodable"),
    ],
)
def test_a_failed_commit_lookup_is_unresolved_and_stops(
    reply: _Response | Exception,
) -> None:
    """A commit lookup that fails is never read as "not a commit"."""
    resolver, session = _resolver({_commit(COMMIT): reply})
    resolution = resolver(ACTION, COMMIT)
    assert resolution["object_type"] is None, resolution
    assert f"git/commits/{COMMIT} could not be read" in str(resolution["error"])
    assert session.requested == [_commit(COMMIT)]


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(_Response(403, {"message": "rate limited"}), id="refused"),
        pytest.param(requests.ConnectionError("offline"), id="offline"),
        pytest.param(_Response(200, None), id="undecodable"),
    ],
)
def test_a_failed_tag_lookup_is_unresolved_not_unknown(
    reply: _Response | Exception,
) -> None:
    """After a 404 from the commit endpoint, a failing tag lookup is reported."""
    resolver, _ = _resolver({_tag(TAG_OBJECT): reply})
    resolution = resolver(ACTION, TAG_OBJECT)
    assert resolution["object_type"] is None, resolution
    assert f"git/tags/{TAG_OBJECT} could not be read" in str(resolution["error"])


def test_a_tag_body_that_is_not_an_object_is_unresolved() -> None:
    """A tag reply that is valid JSON but not an object claims nothing."""
    resolver, _ = _resolver({_tag(TAG_OBJECT): _Response(200, ["not", "an", "object"])})
    resolution = resolver(ACTION, TAG_OBJECT)
    assert resolution["object_type"] is None
    assert "not an object" in str(resolution["error"])


def test_the_client_is_built_once_on_first_use() -> None:
    """No lookup, no client; many lookups, one client."""
    built: list[GithubClient] = []
    session = _Session({_commit(COMMIT): _Response(200, {"sha": COMMIT})})

    def factory() -> GithubClient:
        client = GithubClient(token=None, api_url=API)
        client.session = typ.cast("typ.Any", session)
        built.append(client)
        return client

    resolver = GithubPinResolver(factory)
    assert built == []
    resolver(ACTION, COMMIT)
    resolver(ACTION, COMMIT)
    assert len(built) == 1


@pytest.mark.parametrize(
    ("token", "authorization"),
    [
        pytest.param(None, None, id="anonymous"),
        pytest.param("t", "Bearer t", id="token"),
    ],
)
def test_the_client_authenticates_only_with_a_token(
    token: str | None, authorization: str | None
) -> None:
    """Without a token the client sends no Authorization header at all."""
    client = GithubClient(token=token, api_url=API)
    assert client.session.headers.get("Authorization") == authorization


def test_each_lookup_is_logged_with_its_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A debug record names the pin and a bounded outcome, never the body."""
    resolver, _ = _resolver({
        _commit(COMMIT): _Response(500, {"message": "secret-body-text"}),
        _commit(TAG_OBJECT): _Response(429, {"message": "slow"}),
        _commit("0" * 40): _Response(200, {"sha": "0" * 40}),
    })
    with caplog.at_level("DEBUG", logger="concordat.rules.github_pins"):
        resolver(ACTION, COMMIT)
        resolver(ACTION, TAG_OBJECT)
        resolver(ACTION, "0" * 40)
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 3, messages
    assert f"{ACTION}@{COMMIT}: unresolved in " in messages[0]
    assert f"{ACTION}@{TAG_OBJECT}: rate_limited in " in messages[1]
    assert f"{ACTION}@{'0' * 40}: commit in " in messages[2]
    assert not any("secret-body-text" in message for message in messages)


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(_Response(429, {"message": "slow down"}), id="429"),
        pytest.param(
            _Response(403, {"message": "limit"}, {"X-RateLimit-Remaining": "0"}),
            id="403-spent",
        ),
    ],
)
def test_a_spent_rate_limit_is_flagged(reply: _Response) -> None:
    """A rate-limit refusal is unresolved and marked, so its remedy can be named."""
    resolver, _ = _resolver({_commit(COMMIT): reply})
    resolution = resolver(ACTION, COMMIT)
    assert resolution["object_type"] is None, resolution
    assert resolution["rate_limited"] is True, resolution
    assert f"git/commits/{COMMIT} was rate limited" in str(resolution["error"])


def test_a_plain_refusal_is_not_a_rate_limit() -> None:
    """A 403 with rate limit left is a refusal, not a spent limit."""
    reply = _Response(403, {"message": "no"}, {"X-RateLimit-Remaining": "12"})
    resolver, _ = _resolver({_commit(COMMIT): reply})
    assert resolver(ACTION, COMMIT)["rate_limited"] is False


def test_a_rate_limited_tag_lookup_is_flagged() -> None:
    """After a 404 on the commit endpoint, a spent limit on the tag one is marked."""
    resolver, _ = _resolver({
        _tag(TAG_OBJECT): _Response(429, {"message": "slow down"})
    })
    resolution = resolver(ACTION, TAG_OBJECT)
    assert resolution["rate_limited"] is True, resolution
    assert f"git/tags/{TAG_OBJECT} was rate limited" in str(resolution["error"])


@pytest.mark.parametrize(
    ("routes", "sha"),
    [
        pytest.param(
            {_commit(COMMIT): _Response(200, {"sha": COMMIT})}, COMMIT, id="commit"
        ),
        pytest.param(
            {_commit(COMMIT): _Response(429, {"message": "x"})},
            COMMIT,
            id="rate-limited",
        ),
        pytest.param({}, TAG_OBJECT, id="unknown"),
    ],
)
def test_each_pin_is_looked_up_once_per_resolver(
    routes: dict[str, _Response | Exception], sha: str
) -> None:
    """Asking again about the same pin reuses the answer, whatever it was."""
    resolver, session = _resolver(routes)
    first = resolver(ACTION, sha)
    asked = list(session.requested)
    assert resolver(ACTION, sha) == first
    assert session.requested == asked


_REPOSITORIES: typ.Final = ("o/one", "o/two", "p/one")
_SHAS: typ.Final = (COMMIT, TAG_OBJECT, "0" * 40)


@given(
    st.lists(
        st.tuples(st.sampled_from(_REPOSITORIES), st.sampled_from(_SHAS)), max_size=20
    )
)
def test_each_repository_and_pin_is_looked_up_once_in_any_order(
    calls: list[tuple[str, str]],
) -> None:
    """Memoization is per `(repository, sha)`, whatever the call order.

    Repeated and interleaved pairs cost one lookup each; the same SHA in
    another repository is a different pair and is looked up on its own.
    """
    routes: dict[str, _Response | Exception] = {
        f"/repos/{repository}/git/commits/{sha}": _Response(200, {"sha": sha})
        for repository in _REPOSITORIES
        for sha in _SHAS
    }
    resolver, session = _resolver(routes)
    answers = list(itertools.starmap(resolver, calls))
    assert answers == [commit_pin(sha) for _, sha in calls]
    expected = {f"/repos/{repository}/git/commits/{sha}" for repository, sha in calls}
    assert sorted(session.requested) == sorted(expected)
