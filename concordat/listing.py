"""Helpers for listing repositories in GitHub namespaces."""

from __future__ import annotations

import asyncio
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

Runner = typ.Callable[[typ.Callable[[], list[str]]], typ.Awaitable[list[str]]]

ERROR_NO_NAMESPACES = "Specify at least one namespace to list."


def _no_namespaces_error() -> ConcordatError:
    return ConcordatError(ERROR_NO_NAMESPACES)


def _namespace_not_found_error(namespace: str) -> ConcordatError:
    message = f"Namespace {namespace!r} was not found on GitHub."
    return ConcordatError(message)


def _namespace_forbidden_error(namespace: str) -> ConcordatError:
    message = f"Access to namespace {namespace!r} is forbidden."
    return ConcordatError(message)


def _github_api_error(error: Exception) -> ConcordatError:
    message = f"GitHub API error: {error}"
    return ConcordatError(message)


def _connection_error(error: Exception) -> ConcordatError:
    parts: list[str] = []
    current: Exception | None = error  # type: ignore[assignment]
    seen: set[int] = set()
    while current and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        if text and text not in parts:
            parts.append(text)
        current = current.__cause__ or current.__context__  # type: ignore[assignment]

    detail = "; caused by: ".join(parts) if parts else repr(error)
    suggestion = (
        "Unable to contact GitHub over HTTPS. This usually means your TLS "
        "configuration or certificate store needs attention. If you are "
        "behind an intercepting proxy, export REQUESTS_CA_BUNDLE (or "
        "SSL_CERT_FILE) with the proxy's root certificate."
    )
    message = f"{suggestion}\nOriginal error: {detail}"
    return ConcordatError(message)


async def list_namespace_repositories(
    namespaces: typ.Sequence[str],
    *,
    token: str | None = None,
    runner: Runner | None = None,
    client_factory: typ.Callable[[], GitHub] | None = None,
) -> list[str]:
    """Return SSH URLs for repositories across the provided namespaces."""
    if not namespaces:
        raise _no_namespaces_error()

    runner_fn = runner or (lambda thunk: asyncio.to_thread(thunk))
    factory = client_factory or (lambda: GitHub(token=token))
    client = factory()

    tasks = [
        _list_single_namespace(client, namespace, runner_fn) for namespace in namespaces
    ]
    results = await asyncio.gather(*tasks)

    combined: list[str] = []
    for namespace_repositories in results:
        combined.extend(namespace_repositories)
    return combined


async def _list_single_namespace(
    client: GitHub,
    namespace: str,
    runner: Runner,
) -> list[str]:
    def fetch() -> list[str]:
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
        return ssh_urls

    try:
        return await runner(fetch)
    except NotFoundError as error:
        raise _namespace_not_found_error(namespace) from error
    except ForbiddenError as error:
        raise _namespace_forbidden_error(namespace) from error
    except GitHubConnectionError as error:
        raise _connection_error(error) from error
    except GitHubError as error:
        raise _github_api_error(error) from error
