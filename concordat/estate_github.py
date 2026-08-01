"""GitHub API client construction and repository provisioning.

This module owns the calls concordat makes against the GitHub API when an
estate repository must be created, and the translation of github3's
authentication failures into the estate error taxonomy. It knows nothing
about git, the estate-init decision flow, or :mod:`concordat.estate`.
"""

from __future__ import annotations

import types
import typing as typ

import github3

# Imported explicitly so the `github3.orgs.Organization` hints below resolve.
import github3.orgs
from github3 import exceptions as github3_exceptions

from .estate_errors import (
    GitHubClientInitializationError,
    GitHubOrganizationAuthenticationError,
    GitHubRepositoryAuthenticationError,
    GitHubRepositoryCreationAuthenticationError,
    MissingGitHubTokenError,
    RepositoryCreationPermissionError,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# Both creation paths must offer the same repository, so the options they share
# are declared once here and unpacked at each call site, leaving only the name
# path-specific. The proxy keeps the shared mapping immutable.
_REPOSITORY_OPTIONS: cabc.Mapping[str, object] = types.MappingProxyType(
    {
        "private": True,
        "auto_init": False,
        "description": "Platform standards repository managed by concordat",
    }
)

# github3 raises a 401 as `AuthenticationFailed` and a 403 as `ForbiddenError`;
# they are siblings rather than one deriving from the other, so both must be
# named to translate a rejected call into the estate taxonomy.
_REJECTED = (
    github3_exceptions.AuthenticationFailed,
    github3_exceptions.ForbiddenError,
)


def _build_client(
    token: str | None,
    client_factory: typ.Callable[[str | None], github3.GitHub] | None = None,
) -> github3.GitHub:
    """Return an authenticated GitHub client from *client_factory* or *token*."""
    if client_factory:
        client = client_factory(token)
        if client is None:
            raise GitHubClientInitializationError
        return client

    if not token:
        raise MissingGitHubTokenError

    return github3.GitHub(token=token)


def _create_repository(
    client: github3.GitHub,
    owner: str,
    name: str,
) -> None:
    """Create a repository in an organisation or for the authenticated user."""
    org = _find_organization(client, owner)
    if org:
        _create_organization_repository(org, owner, name)
        return
    _create_personal_repository(client, owner, name)


def _find_organization(
    client: github3.GitHub,
    owner: str,
) -> github3.orgs.Organization | None:
    """Return the organisation named *owner*, or ``None`` when there is none.

    An authentication failure here precedes any creation attempt: a rejected
    lookup says nothing about whether *owner* is an organisation, so falling
    through to the personal path would misreport the cause.
    """
    try:
        return client.organization(owner)
    except github3_exceptions.NotFoundError:
        return None
    except _REJECTED as error:
        raise GitHubOrganizationAuthenticationError(owner) from error


def _create_organization_repository(
    org: github3.orgs.Organization,
    owner: str,
    name: str,
) -> None:
    """Create *name* inside the *owner* organisation."""
    try:
        org.create_repository(name, **_REPOSITORY_OPTIONS)
    except _REJECTED as error:
        raise GitHubRepositoryCreationAuthenticationError(owner, name) from error


def _create_personal_repository(
    client: github3.GitHub,
    owner: str,
    name: str,
) -> None:
    """Create *name* for the authenticated user, who must be *owner*."""
    user = client.me()
    if not user or user.login != owner:
        raise RepositoryCreationPermissionError(owner)
    try:
        client.create_repository(name, **_REPOSITORY_OPTIONS)
    except _REJECTED as error:
        raise GitHubRepositoryAuthenticationError from error
