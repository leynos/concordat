"""Exception taxonomy for estate management.

These errors are re-exported from :mod:`concordat.estate` so existing callers
that import them from that module continue to work unchanged.
"""

from __future__ import annotations

import typing as typ

from .errors import ConcordatError

if typ.TYPE_CHECKING:
    from pathlib import Path

ERROR_OWNER_REQUIRED = (
    "Unable to determine github_owner for the estate. Provide --github-owner "
    "when the remote URL is not a GitHub repository."
)


class EstateError(ConcordatError):
    """Base class for estate-related Concordat errors."""


class EstateNotConfiguredError(EstateError):
    """Raised when referring to an unknown estate alias."""

    def __init__(self, alias: str) -> None:
        """Initialise the error with the missing alias."""
        message = f"Estate {alias!r} is not configured."
        super().__init__(message)
        self.alias = alias


class DuplicateEstateAliasError(EstateError):
    """Raised when attempting to register an alias twice."""

    def __init__(self, alias: str) -> None:
        """Initialise the error with the duplicate alias."""
        message = f"Estate alias {alias!r} already exists."
        super().__init__(message)
        self.alias = alias


class NoActiveEstateError(EstateError):
    """Raised when an operation needs an active estate but none is set."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__(
            "No active estate configured; run `concordat estate use` first."
        )


class MissingEstateAliasError(EstateError):
    """Raised when init-estate is invoked without an alias."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__("Estate alias is required.")


class UnsupportedRepositoryCreationError(EstateError):
    """Raised when trying to bootstrap a non-GitHub repository."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__("Only GitHub repositories can be created automatically.")


class NonEmptyRepositoryError(EstateError):
    """Raised when the target repository already has commits."""

    def __init__(self, repo_url: str) -> None:
        """Initialise the error with the offending repository."""
        message = (
            f"Repository {repo_url!r} already contains commits; "
            "estate init requires an empty repository."
        )
        super().__init__(message)
        self.repo_url = repo_url


class RepositoryUnreachableError(EstateError):
    """Raised when the estate repo cannot be reached via SSH."""

    def __init__(self, repo_url: str) -> None:
        """Initialise the error with the unreachable repository."""
        message = f"Cannot reach {repo_url!r}; provide a GitHub SSH URL."
        super().__init__(message)
        self.repo_url = repo_url


class RepositoryInaccessibleError(EstateError):
    """Raised when GitHub reports a repo exists but SSH access fails."""

    def __init__(self, repo_url: str) -> None:
        """Initialise the error with the inaccessible repository."""
        message = (
            f"Repository {repo_url!r} exists but could not be reached via SSH; "
            "ensure your agent exposes the required key."
        )
        super().__init__(message)
        self.repo_url = repo_url


class RepositorySlugUnknownError(EstateError):
    """Raised when the GitHub slug cannot be determined."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__("Unable to determine repository slug for automatic creation.")


class EstateCreationAbortedError(EstateError):
    """Raised when the operator declines repository creation."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__("Estate creation aborted by user.")


class GitHubOwnerConfirmationAbortedError(EstateError):
    """Raised when the operator declines the inferred github_owner."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__(
            "GitHub owner confirmation declined; re-run with --github-owner "
            "to override."
        )


class RepositoryIdentityError(EstateError):
    """Raised when the owner/name pair cannot be derived for creation."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__("Unable to determine repository owner and name for creation.")


class EstateInventoryMissingError(EstateError):
    """Raised when the inventory file is absent from the estate."""

    def __init__(self, alias: str, path: str) -> None:
        """Initialise the error with the alias and path."""
        message = f"Inventory {path!r} missing from estate {alias!r}."
        super().__init__(message)
        self.alias = alias
        self.path = path


class RepositoryCreationPermissionError(EstateError):
    """Raised when neither the org nor the authenticated user can create the repo."""

    def __init__(self, owner: str) -> None:
        """Initialise the error with the owner namespace."""
        message = f"Authenticated user cannot create repositories under {owner!r}."
        super().__init__(message)
        self.owner = owner


class TemplateMissingError(EstateError):
    """Raised when the bundled template cannot be located."""

    def __init__(self, template_root: Path) -> None:
        """Initialise the error with the template path."""
        message = f"Template directory {template_root} is missing."
        super().__init__(message)
        self.template_root = template_root


class TemplatePushError(EstateError):
    """Raised when pushing the bootstrapped template fails."""

    def __init__(self, detail: str) -> None:
        """Initialise the error with the push failure detail."""
        message = f"Failed to push estate template: {detail}"
        super().__init__(message)
        self.detail = detail


class GitHubClientInitializationError(EstateError):
    """Raised when a GitHub client cannot be produced."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__("Unable to initialise GitHub client.")


class MissingGitHubTokenError(EstateError):
    """Raised when a GitHub token is required but missing."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__(
            "GITHUB_TOKEN is required to create repositories automatically."
        )


class GitHubAuthenticationError(EstateError):
    """Raised when GitHub rejects authentication."""

    def __init__(
        self,
        message: str = "Failed to authenticate with the provided token.",
    ) -> None:
        """Initialise the error with the provided detail."""
        super().__init__(message)
        self.message = message


class MissingGitHubOwnerError(EstateError):
    """Raised when github_owner cannot be resolved for an estate."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__(ERROR_OWNER_REQUIRED)


class ActiveOwnerMismatchError(EstateError):
    """Raised when an estate's owner differs from the active GitHub owner."""

    def __init__(self, active_owner: str, estate_owner: str) -> None:
        """Initialise the error with both owners."""
        message = (
            f"estate owner {estate_owner!r} does not match the active GitHub "
            f"owner {active_owner!r}; run `concordat owner use {estate_owner}` "
            "first if that is the intended namespace"
        )
        super().__init__(message)
        self.active_owner = active_owner
        self.estate_owner = estate_owner


class GitHubOrganizationAuthenticationError(GitHubAuthenticationError):
    """Raised when organization-level auth fails."""

    def __init__(self, owner: str) -> None:
        """Initialise the error with the organization owner."""
        message = (
            f"GitHub authentication failed accessing organization {owner!r}. "
            "Ensure GITHUB_TOKEN includes the 'repo' scope and is valid."
        )
        super().__init__(message)
        self.owner = owner


class GitHubRepositoryCreationAuthenticationError(GitHubAuthenticationError):
    """Raised when repo creation fails due to authentication."""

    def __init__(self, owner: str, name: str) -> None:
        """Initialise the error with the repository slug."""
        message = (
            f"GitHub authentication failed creating {owner}/{name}. "
            "Ensure GITHUB_TOKEN includes the 'repo' scope and is valid."
        )
        super().__init__(message)
        self.owner = owner
        self.name = name


class GitHubRepositoryAuthenticationError(GitHubAuthenticationError):
    """Raised when generic repository operations fail during authentication."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__(
            "GitHub authentication failed when creating the repository. "
            "Ensure GITHUB_TOKEN includes the 'repo' scope and is valid."
        )
