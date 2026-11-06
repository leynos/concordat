"""Helpers for listing repositories in GitHub namespaces."""

from __future__ import annotations

import typing as typ

from github3 import GitHub
from github3.exceptions import (
    ConnectionError as GitHubConnectionError,
)
from github3.exceptions import (
    ForbiddenError,
    GitHubError,
    NotFoundError,
)

from .errors import ConcordatError

ERROR_NO_NAMESPACES = "Specify at least one namespace to list."


class _RepositoryClient(typ.Protocol):
    """Subset of the github3 client used to list repositories by owner."""

    def repositories_by(
        self,
        username: str,
        /,
        **kwargs: object,
    ) -> typ.Iterable[object]:
        """Return the repositories owned by ``username``."""


def _no_namespaces_error() -> ConcordatError:
    """Return the error raised when no namespace is supplied."""
    return ConcordatError(ERROR_NO_NAMESPACES)


def _namespace_not_found_error(namespace: str) -> ConcordatError:
    """Return a ConcordatError reporting that ``namespace`` does not exist."""
    message = f"Namespace {namespace!r} was not found on GitHub."
    return ConcordatError(message)


def _namespace_forbidden_error(namespace: str) -> ConcordatError:
    """Return a ConcordatError reporting that access to ``namespace`` is denied."""
    message = f"Access to namespace {namespace!r} is forbidden."
    return ConcordatError(message)


def _github_api_error(error: Exception) -> ConcordatError:
    """Wrap a generic GitHub API failure in a ConcordatError."""
    message = f"GitHub API error: {error}"
    return ConcordatError(message)


def _connection_error(error: Exception) -> ConcordatError:
    """Wrap a GitHub connection failure with its causes and TLS guidance.

    The message lists each distinct error in the cause and context chain, so
    the root cause of a TLS or proxy problem is visible to the user.

    Returns
    -------
    ConcordatError
        The error carrying the TLS guidance and the original error chain.
    """
    parts: list[str] = []
    current: Exception | None = error
    seen: set[int] = set()
    while current and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        if text and text not in parts:
            parts.append(text)
        next_error = current.__cause__ or current.__context__
        current = next_error if isinstance(next_error, Exception) else None

    detail = "; caused by: ".join(parts) if parts else repr(error)
    suggestion = (
        "Unable to contact GitHub over HTTPS. This usually means your TLS "
        "configuration or certificate store needs attention. If you are "
        "behind an intercepting proxy, export REQUESTS_CA_BUNDLE (or "
        "SSL_CERT_FILE) with the proxy's root certificate."
    )
    message = f"{suggestion}\nOriginal error: {detail}"
    return ConcordatError(message)


def list_namespace_repositories(
    namespaces: typ.Sequence[str],
    *,
    token: str | None = None,
    client_factory: typ.Callable[[], _RepositoryClient] | None = None,
) -> list[str]:
    """Return SSH URLs for repositories across the provided namespaces."""
    if not namespaces:
        raise _no_namespaces_error()

    factory = client_factory or (
        lambda: typ.cast("_RepositoryClient", GitHub(token=token))
    )
    client = factory()
    try:
        combined: list[str] = []
        for namespace in namespaces:
            combined.extend(_fetch_namespace(client, namespace))
        return combined
    finally:
        session = getattr(client, "session", None)
        if session is not None:
            session.close()


def _fetch_namespace(client: _RepositoryClient, namespace: str) -> list[str]:
    """Return the sorted SSH URLs of the repositories owned by ``namespace``.

    A repository without ``ssh_url`` is given one from its full name or name.
    GitHub errors are re-raised as ConcordatError.

    Returns
    -------
    list[str]
        The sorted SSH URLs of the namespace's repositories.

    Raises
    ------
    _namespace_not_found_error
        A ConcordatError when GitHub reports that the namespace does not exist.
    _namespace_forbidden_error
        A ConcordatError when access to the namespace is forbidden.
    _connection_error
        A ConcordatError when GitHub cannot be contacted.
    _github_api_error
        A ConcordatError for any other GitHub API failure.
    """
    try:
        generator = client.repositories_by(namespace, type="owner", number=-1)
        ssh_urls: list[str] = []
        for repo in generator:
            ssh_url = getattr(repo, "ssh_url", None)
            if not ssh_url:
                full_name = getattr(repo, "full_name", None)
                name = getattr(repo, "name", None)
                if full_name:
                    ssh_url = f"git@github.com:{full_name}.git"
                elif name:
                    ssh_url = f"git@github.com:{namespace}/{name}.git"
                else:
                    continue
            ssh_urls.append(ssh_url)
        ssh_urls.sort()
    except NotFoundError as error:
        raise _namespace_not_found_error(namespace) from error
    except ForbiddenError as error:
        raise _namespace_forbidden_error(namespace) from error
    except GitHubConnectionError as error:
        raise _connection_error(error) from error
    except GitHubError as error:
        raise _github_api_error(error) from error
    return ssh_urls
