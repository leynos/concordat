"""GitHub API client construction and repository provisioning.

This module owns the calls concordat makes against the GitHub API when an
estate repository must be created, and the translation of github3's
authentication failures into the estate error taxonomy. It knows nothing
about git, the estate-init decision flow, or :mod:`concordat.estate`.
"""

from __future__ import annotations

import typing as typ

import github3
from github3 import exceptions as github3_exceptions

from .estate_errors import (
    GitHubAuthenticationError,
    GitHubClientInitializationError,
    GitHubOrganizationAuthenticationError,
    GitHubRepositoryAuthenticationError,
    GitHubRepositoryCreationAuthenticationError,
    MissingGitHubTokenError,
    RepositoryCreationPermissionError,
)


def _build_client(
    token: str | None,
    client_factory: typ.Callable[[str | None], github3.GitHub] | None = None,
) -> github3.GitHub:
    if client_factory:
        client = client_factory(token)
        if client is None:
            raise GitHubClientInitializationError
        return client

    if not token:
        raise MissingGitHubTokenError

    client = github3.GitHub(token=token)
    if client is None:
        raise GitHubAuthenticationError
    return client


def _create_repository(
    client: github3.GitHub,
    owner: str,
    name: str,
) -> None:
    try:
        org = client.organization(owner)
    except github3_exceptions.AuthenticationFailed as error:
        raise GitHubOrganizationAuthenticationError(owner) from error
    except github3_exceptions.NotFoundError:
        org = None

    if org:
        try:
            org.create_repository(
                name,
                private=True,
                auto_init=False,
                description="Platform standards repository managed by concordat",
            )
        except github3_exceptions.AuthenticationFailed as error:
            raise GitHubRepositoryCreationAuthenticationError(owner, name) from error
        return

    user = client.me()
    if not user or user.login != owner:
        raise RepositoryCreationPermissionError(owner)
    try:
        client.create_repository(
            name,
            private=True,
            auto_init=False,
            description="Platform standards repository managed by concordat",
        )
    except github3_exceptions.AuthenticationFailed as error:
        raise GitHubRepositoryAuthenticationError from error
