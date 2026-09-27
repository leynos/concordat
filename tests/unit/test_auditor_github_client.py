"""Unit tests for the Auditor client's CV-006 reads, over a fake HTTP session.

Each case pins the endpoint and query a read sends, how it parses the
payload, and how it names an absence (404), a refusal (401 or 403) and any
other failure, so a change to the client cannot pass through `fetch`'s fake
unseen.
"""

from __future__ import annotations

import base64
import dataclasses
import typing as typ

import pytest

from concordat.auditor.github import (
    GithubClient,
    GithubError,
    GithubForbiddenError,
    GithubNotFoundError,
)

API = "https://api.example.test"


@dataclasses.dataclass
class _Response:
    """The `requests.Response` fields the client reads."""

    status_code: int
    payload: object = None
    links: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)
    text: str = ""

    def json(self) -> object:
        """Return the canned payload."""
        return self.payload


class _Session:
    """Answer requests by path and query, recording each URL requested."""

    def __init__(self, routes: dict[str, _Response]) -> None:
        """Serve *routes*, keyed by the path and query after the API root."""
        self.routes = routes
        self.requested: list[str] = []

    def request(self, method: str, url: str, **_kwargs: object) -> _Response:
        """Return the route for *url*, or a 404 for an unknown one."""
        assert method == "GET", method
        path = url.removeprefix(API)
        self.requested.append(path)
        return self.routes.get(path, _Response(404))


def _client(routes: dict[str, _Response]) -> tuple[GithubClient, _Session]:
    """Build a client whose session answers from *routes*."""
    client = GithubClient(token="t", api_url=API)  # noqa: S106 - a placeholder, never sent
    session = _Session(routes)
    client.session = typ.cast("typ.Any", session)
    return client, session


def test_the_environment_is_read_by_name_and_a_404_is_its_absence() -> None:
    """A 404 is an environment that does not exist, not a failure."""
    policy = {"protected_branches": False, "custom_branch_policies": True}
    client, session = _client({
        "/repos/o/r/environments/codescene": _Response(
            200, {"name": "codescene", "deployment_branch_policy": policy}
        ),
    })
    assert client.environment("o", "r", "codescene") == {
        "name": "codescene",
        "deployment_branch_policy": policy,
    }
    assert client.environment("o", "other", "codescene") is None
    assert session.requested == [
        "/repos/o/r/environments/codescene",
        "/repos/o/other/environments/codescene",
    ]


def test_branch_policies_are_read_across_every_page() -> None:
    """A policy on page two is read, so a second branch cannot hide there."""
    first = "/repos/o/r/environments/codescene/deployment-branch-policies?per_page=100"
    second = f"{first}&page=2"
    client, session = _client({
        first: _Response(
            200,
            {"branch_policies": [{"name": "main", "type": "branch"}]},
            links={"next": {"url": f"{API}{second}"}},
        ),
        second: _Response(200, {"branch_policies": [{"name": "release/*"}]}),
    })
    assert client.environment_branch_policies("o", "r", "codescene") == (
        ("main", "branch"),
        ("release/*", "branch"),
    )
    assert session.requested == [first, second]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("repository_secret_names", "/repos/o/r/actions/secrets?per_page=100"),
        (
            "environment_secret_names",
            "/repos/o/r/environments/codescene/secrets?per_page=100",
        ),
    ],
)
def test_secret_names_are_read_across_every_page(method: str, path: str) -> None:
    """The token on page two of the listing is still seen."""
    second = f"{path}&page=2"
    client, session = _client({
        path: _Response(
            200,
            {"secrets": [{"name": "OTHER", "created_at": "x"}]},
            links={"next": {"url": f"{API}{second}"}},
        ),
        second: _Response(200, {"secrets": [{"name": "CS_ACCESS_TOKEN"}]}),
    })
    args = ("o", "r", "codescene") if method.startswith("environment") else ("o", "r")
    assert getattr(client, method)(*args) == ("OTHER", "CS_ACCESS_TOKEN")
    assert session.requested == [path, second]


def test_workflow_texts_read_root_yaml_files_only() -> None:
    """Directories and non-YAML files in `.github/workflows` are skipped."""
    encoded = base64.b64encode(b"on: push\n").decode()
    client, session = _client({
        "/repos/o/r/contents/.github/workflows": _Response(
            200,
            [
                {"type": "file", "name": "ci.yml", "path": ".github/workflows/ci.yml"},
                {
                    "type": "file",
                    "name": "README.md",
                    "path": ".github/workflows/README.md",
                },
                {"type": "dir", "name": "shared", "path": ".github/workflows/shared"},
            ],
        ),
        "/repos/o/r/contents/.github/workflows/ci.yml": _Response(
            200, {"content": encoded}
        ),
    })
    assert client.workflow_texts("o", "r") == ("on: push\n",)
    assert session.requested == [
        "/repos/o/r/contents/.github/workflows",
        "/repos/o/r/contents/.github/workflows/ci.yml",
    ]


def test_a_repository_without_workflows_has_none() -> None:
    """A 404 on the workflow directory is a repository with no workflows."""
    client, _ = _client({})
    assert client.workflow_texts("o", "r") == ()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, GithubForbiddenError),
        (403, GithubForbiddenError),
        (500, GithubError),
        (429, GithubError),
    ],
)
def test_refusal_and_failure_are_named_apart_from_absence(
    status: int, error: type[Exception]
) -> None:
    """A refusal or a failure is raised; neither reads as an empty listing."""
    path = "/repos/o/r/actions/secrets?per_page=100"
    client, _ = _client({path: _Response(status)})
    with pytest.raises(error) as caught:
        client.repository_secret_names("o", "r")
    assert not isinstance(caught.value, GithubNotFoundError), caught.value
