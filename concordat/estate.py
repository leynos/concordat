"""Estate management helpers for the concordat CLI.

This module is the public façade. Configuration persistence lives in
:mod:`concordat.estate_config`, the exception taxonomy in
:mod:`concordat.estate_errors`, and the GitHub/git repository lifecycle in
:mod:`concordat.estate_repository`. What remains here is orchestration: the
estate-init transaction and the inventory lookup the CLI calls.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from . import estate_repository, xdg

# Configuration persistence and migration live in `estate_config`; these names
# are imported so `concordat.estate` stays the public façade. Names used only
# for re-export keep the redundant-alias form to mark the re-export intent.
from .estate_config import (
    CONFIG_FILENAME as CONFIG_FILENAME,
)
from .estate_config import (
    DEFAULT_BRANCH,
    DEFAULT_INVENTORY_PATH,
    EstateRecord,
    _load_estates,
    get_active_estate,
    get_estate,
    register_estate,
)
from .estate_config import (
    default_config_path as default_config_path,
)
from .estate_config import (
    list_estates as list_estates,
)
from .estate_config import (
    migrate_legacy_config as migrate_legacy_config,
)
from .estate_config import (
    set_active_estate as set_active_estate,
)
from .estate_errors import (
    ActiveOwnerMismatchError,
    DuplicateEstateAliasError,
    EstateNotConfiguredError,
    MissingEstateAliasError,
    NoActiveEstateError,
)

# The repository-lifecycle errors are raised in `estate_repository`, but the
# taxonomy stays importable from this façade for existing callers and tests.
from .estate_errors import (
    EstateCreationAbortedError as EstateCreationAbortedError,
)
from .estate_errors import (
    EstateError as EstateError,
)
from .estate_errors import (
    EstateInventoryMissingError as EstateInventoryMissingError,
)
from .estate_errors import (
    GitHubAuthenticationError as GitHubAuthenticationError,
)
from .estate_errors import (
    GitHubClientInitializationError as GitHubClientInitializationError,
)
from .estate_errors import (
    GitHubOrganizationAuthenticationError as GitHubOrganizationAuthenticationError,
)
from .estate_errors import (
    GitHubOwnerConfirmationAbortedError as GitHubOwnerConfirmationAbortedError,
)
from .estate_errors import (
    GitHubRepositoryAuthenticationError as GitHubRepositoryAuthenticationError,
)
from .estate_errors import (
    # The redundant alias marks the re-export; the name is too long to wrap.
    GitHubRepositoryCreationAuthenticationError as GitHubRepositoryCreationAuthenticationError,  # noqa: E501
)
from .estate_errors import (
    MissingGitHubOwnerError as MissingGitHubOwnerError,
)
from .estate_errors import (
    MissingGitHubTokenError as MissingGitHubTokenError,
)
from .estate_errors import (
    NonEmptyRepositoryError as NonEmptyRepositoryError,
)
from .estate_errors import (
    RepositoryCreationPermissionError as RepositoryCreationPermissionError,
)
from .estate_errors import (
    RepositoryIdentityError as RepositoryIdentityError,
)
from .estate_errors import (
    RepositoryInaccessibleError as RepositoryInaccessibleError,
)
from .estate_errors import (
    RepositorySlugUnknownError as RepositorySlugUnknownError,
)
from .estate_errors import (
    RepositoryUnreachableError as RepositoryUnreachableError,
)
from .estate_errors import (
    TemplateMissingError as TemplateMissingError,
)
from .estate_errors import (
    TemplatePushError as TemplatePushError,
)
from .estate_errors import (
    UnsupportedRepositoryCreationError as UnsupportedRepositoryCreationError,
)

# Re-exported for callers (including the BDD suite) that build probe results.
from .estate_repository import (
    RemoteProbe as RemoteProbe,
)
from .platform_standards import parse_github_slug

if typ.TYPE_CHECKING:
    import github3


def list_enrolled_repositories(
    alias: str | None = None,
    *,
    config_path: Path | None = None,
) -> list[str]:
    """Return the Git URLs for repositories tracked by an estate."""
    record = None
    if alias:
        record = get_estate(alias, config_path=config_path)
        if not record:
            raise EstateNotConfiguredError(alias)
    else:
        record = get_active_estate(config_path)
        if not record:
            raise NoActiveEstateError
    return estate_repository._collect_inventory(record)


def init_estate(
    alias: str,
    repo_url: str,
    *,
    branch: str = DEFAULT_BRANCH,
    inventory_path: str = DEFAULT_INVENTORY_PATH,
    github_owner: str | None = None,
    github_token: str | None = None,
    template_root: Path | None = None,
    confirm: typ.Callable[[str], bool] | None = None,
    client_factory: typ.Callable[[str | None], github3.GitHub] | None = None,
    config_path: Path | None = None,
) -> EstateRecord:
    """Initialise an estate repository from the bundled template."""
    if not alias:
        raise MissingEstateAliasError

    confirmer = confirm or estate_repository._prompt_yes_no
    slug = parse_github_slug(repo_url)
    resolved_owner = estate_repository._resolve_and_confirm_owner(
        slug, github_owner, confirmer
    )
    estate_owner = estate_repository._require_owner(resolved_owner)
    resolved_config_path, owner_to_activate = _resolve_implicit_config_path(
        config_path,
        estate_owner,
    )
    records = _load_estates(resolved_config_path)
    if alias in records:
        raise DuplicateEstateAliasError(alias)
    repository_plan = estate_repository._prepare_repository(
        repo_url,
        slug,
        github_token,
        client_factory,
    )
    if repository_plan.needs_creation:
        estate_repository._ensure_repository_exists(
            estate_repository.RepositoryProvisioning(
                slug=slug,
                plan=repository_plan,
                github_token=github_token,
                client_factory=client_factory,
                confirmer=confirmer,
            )
        )

    bootstrap = estate_repository.TemplateBootstrap(
        branch=branch,
        template_root=template_root or estate_repository.default_template_root(),
        inventory_path=inventory_path,
        callbacks=estate_repository.build_remote_callbacks(repo_url),
    )
    estate_repository._bootstrap_template(repo_url, bootstrap)

    record = EstateRecord(
        alias=alias,
        repo_url=repo_url,
        branch=branch,
        inventory_path=inventory_path,
        github_owner=estate_owner,
    )
    register_estate(
        record,
        config_path=resolved_config_path,
        set_active_if_missing=True,
    )
    # Only now that validation, repository creation, bootstrap, and
    # registration have all succeeded is it safe to make this owner active: a
    # failure above (e.g. DuplicateEstateAliasError) must leave the active
    # owner untouched rather than stranding it on a half-initialised estate.
    if owner_to_activate is not None:
        xdg.set_active_owner(owner_to_activate)
    return record


def _resolve_implicit_config_path(
    config_path: Path | None,
    estate_owner: str,
) -> tuple[Path | None, str | None]:
    """Resolve the estate config path and the owner to activate on success.

    The duplicate-alias check and the eventual registration must read and
    write the same owner-namespaced file, so the path is resolved up front and
    any active-owner mismatch is rejected here. The active owner is NOT mutated:
    the returned ``owner_to_activate`` (non-``None`` only on the implicit path
    with no active owner yet) is committed by the caller after registration
    succeeds, so a failed init leaves ``xdg.get_active_owner()`` unchanged. An
    explicit *config_path* bypasses the owner namespace entirely.
    """
    match (config_path, xdg.get_active_owner()):
        case (Path() as explicit, _):
            return explicit, None
        case (None, None):
            return xdg.owner_config_path(estate_owner), estate_owner
        case (None, active_owner) if active_owner != estate_owner:
            raise ActiveOwnerMismatchError(active_owner, estate_owner)
        case _:
            return xdg.owner_config_path(estate_owner), None
