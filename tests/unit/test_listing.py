"""Unit tests for GitHub namespace listing helpers."""

from __future__ import annotations

import types

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


def test_list_namespace_repositories_combines_namespaces() -> None:
    """Aggregate repositories from multiple namespaces in order."""
    namespace_calls: list[str] = []

    class DummyClient:
        session = types.SimpleNamespace(close=lambda: None)

        def repositories_by(
            self,
            namespace: str,
            **kwargs: object,
        ) -> list[types.SimpleNamespace]:
            namespace_calls.append(namespace)
            data = {
                "first": [
                    types.SimpleNamespace(
                        ssh_url="git@github.com:first/repo-one.git",
                        full_name="first/repo-one",
                        name="repo-one",
                    )
                ],
                "second": [
                    types.SimpleNamespace(
                        ssh_url="git@github.com:second/repo-two.git",
                        full_name="second/repo-two",
                        name="repo-two",
                    )
                ],
            }
            return data[namespace]

    results = listing.list_namespace_repositories(
        ("first", "second"),
        client_factory=lambda: DummyClient(),
    )

    assert results == [
        "git@github.com:first/repo-one.git",
        "git@github.com:second/repo-two.git",
    ]
    assert namespace_calls == ["first", "second"]


def test_list_namespace_repositories_falls_back_to_full_name() -> None:
    """Construct SSH URLs when the API omits them."""

    class DummyClient:
        session = types.SimpleNamespace(close=lambda: None)

        def repositories_by(
            self,
            namespace: str,
            **kwargs: object,
        ) -> list[types.SimpleNamespace]:
            return [
                types.SimpleNamespace(
                    ssh_url=None,
                    full_name=f"{namespace}/service",
                    name="service",
                )
            ]

    results = listing.list_namespace_repositories(
        ("team",),
        client_factory=lambda: DummyClient(),
    )

    assert results == ["git@github.com:team/service.git"]


def test_list_namespace_repositories_raises_when_namespace_missing() -> None:
    """Translate GitHub not-found errors into Concordat errors."""

    class DummyClient:
        session = types.SimpleNamespace(close=lambda: None)

        def repositories_by(
            self,
            namespace: str,
            **kwargs: object,
        ) -> list[types.SimpleNamespace]:
            raise NotFoundError(_fake_response())

    with pytest.raises(ConcordatError) as caught:
        listing.list_namespace_repositories(
            ("unknown",),
            client_factory=lambda: DummyClient(),
        )

    assert "unknown" in str(caught.value)


def test_list_namespace_repositories_requires_namespace() -> None:
    """Reject empty namespace lists."""
    with pytest.raises(ConcordatError):
        listing.list_namespace_repositories(())


def test_list_namespace_repositories_formats_connection_error() -> None:
    """Return a helpful message when TLS negotiation fails."""

    class DummyClient:
        session = types.SimpleNamespace(close=lambda: None)

        def repositories_by(
            self,
            namespace: str,
            **kwargs: object,
        ) -> list[types.SimpleNamespace]:
            underlying = requests_exceptions.SSLError("unknown error (_ssl.c:3113)")
            raise GitHubConnectionError(underlying)

    with pytest.raises(ConcordatError) as caught:
        listing.list_namespace_repositories(
            ("alpha",),
            client_factory=lambda: DummyClient(),
        )

    message = str(caught.value)
    assert "Unable to contact GitHub" in message
    assert "unknown error" in message
