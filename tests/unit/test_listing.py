"""Unit tests for GitHub namespace listing helpers."""

from __future__ import annotations

import types
import typing as typ
import unittest.mock as mock

import pytest
from github3.exceptions import ConnectionError as GitHubConnectionError
from github3.exceptions import NotFoundError
from requests import exceptions as requests_exceptions

from concordat import listing
from concordat.errors import ConcordatError


def _fake_response(status_code: int = 404, reason: str = "Not Found") -> object:
    payload = {"message": reason, "errors": []}
    return types.SimpleNamespace(
        status_code=status_code,
        reason=reason,
        headers={},
        history=(),
        url="https://api.github.com/mock",
        json=lambda: payload,
        content="",
    )


@pytest.mark.asyncio
async def test_list_namespace_repositories_combines_namespaces() -> None:
    """Aggregate repositories from multiple namespaces in order."""
    client = mock.Mock()
    client.repositories_by.side_effect = [
        [
            types.SimpleNamespace(
                ssh_url="git@github.com:first/repo-one.git",
                full_name="first/repo-one",
                name="repo-one",
            )
        ],
        [
            types.SimpleNamespace(
                ssh_url="git@github.com:second/repo-two.git",
                full_name="second/repo-two",
                name="repo-two",
            )
        ],
    ]

    async def runner(func: typ.Callable[[], list[str]]) -> list[str]:
        return func()

    results = await listing.list_namespace_repositories(
        ("first", "second"),
        runner=runner,
        client_factory=lambda: client,
    )

    assert results == [
        "git@github.com:first/repo-one.git",
        "git@github.com:second/repo-two.git",
    ]
    assert client.repositories_by.call_count == 2
    client.repositories_by.assert_any_call("first", type="owner", number=-1)
    client.repositories_by.assert_any_call("second", type="owner", number=-1)


@pytest.mark.asyncio
async def test_list_namespace_repositories_falls_back_to_full_name() -> None:
    """Construct SSH URLs when the API omits them."""
    client = mock.Mock()
    client.repositories_by.return_value = [
        types.SimpleNamespace(
            ssh_url=None,
            full_name="team/service",
            name="service",
        )
    ]

    async def runner(func: typ.Callable[[], list[str]]) -> list[str]:
        return func()

    results = await listing.list_namespace_repositories(
        ("team",),
        runner=runner,
        client_factory=lambda: client,
    )

    assert results == ["git@github.com:team/service.git"]


@pytest.mark.asyncio
async def test_list_namespace_repositories_raises_when_namespace_missing() -> None:
    """Translate GitHub not-found errors into Concordat errors."""
    client = mock.Mock()
    client.repositories_by.side_effect = NotFoundError(_fake_response())

    async def runner(func: typ.Callable[[], list[str]]) -> list[str]:
        return func()

    with pytest.raises(ConcordatError) as caught:
        await listing.list_namespace_repositories(
            ("unknown",),
            runner=runner,
            client_factory=lambda: client,
        )

    assert "unknown" in str(caught.value)


@pytest.mark.asyncio
async def test_list_namespace_repositories_requires_namespace() -> None:
    """Reject empty namespace lists."""
    with pytest.raises(ConcordatError):
        await listing.list_namespace_repositories(())


@pytest.mark.asyncio
async def test_list_namespace_repositories_formats_connection_error() -> None:
    """Return a helpful message when TLS negotiation fails."""
    underlying = requests_exceptions.SSLError("unknown error (_ssl.c:3113)")
    client = mock.Mock()
    client.repositories_by.side_effect = GitHubConnectionError(underlying)

    async def runner(func: typ.Callable[[], list[str]]) -> list[str]:
        return func()

    with pytest.raises(ConcordatError) as caught:
        await listing.list_namespace_repositories(
            ("alpha",),
            runner=runner,
            client_factory=lambda: client,
        )

    message = str(caught.value)
    assert "Unable to contact GitHub" in message
    assert "unknown error" in message
