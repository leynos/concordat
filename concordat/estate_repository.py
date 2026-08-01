"""Estate-init decisions: owner resolution and repository provisioning plans.

This module decides *what* ``concordat estate init`` must do — which owner an
estate belongs to, and whether its remote needs provisioning — and delegates
the *how* to :mod:`concordat.estate_github` (GitHub API) and
:mod:`concordat.estate_git` (git operations).

The names imported below are deliberate, not incidental. They keep this module
the single lookup site the façade calls through (``estate_repository.<name>``)
and the single seam the test suite monkeypatches, so the split stays invisible
to both. :mod:`concordat.estate` imports this module, never the reverse.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from github3 import exceptions as github3_exceptions

from .estate_config import _normalise_owner
from .estate_errors import (
    EstateCreationAbortedError,
    GitHubAuthenticationError,
    GitHubOwnerConfirmationAbortedError,
    MissingGitHubOwnerError,
    NonEmptyRepositoryError,
    RepositoryIdentityError,
    RepositoryInaccessibleError,
    RepositorySlugUnknownError,
    RepositoryUnreachableError,
    UnsupportedRepositoryCreationError,
)

# Used directly below; also the seam tests patch as
# ``estate_repository._probe_remote`` / ``._build_client`` / ``._create_repository``.
from .estate_git import (
    RemoteProbe,
    _probe_remote,
)

# Re-exported for the façade to call and for tests to patch: `init_estate`
# invokes `estate_repository._bootstrap_template` / `.default_template_root` /
# `.TemplateBootstrap`, and `list_enrolled_repositories` invokes
# `estate_repository._collect_inventory`.
from .estate_git import (
    TemplateBootstrap as TemplateBootstrap,
)
from .estate_git import (
    _bootstrap_template as _bootstrap_template,
)
from .estate_git import (
    _collect_inventory as _collect_inventory,
)
from .estate_git import (
    default_template_root as default_template_root,
)
from .estate_github import _build_client, _create_repository

# `init_estate` builds its push callbacks through this façade lookup site too;
# re-exported from `gitutils` exactly as before the GitHub/git split.
from .gitutils import build_remote_callbacks as build_remote_callbacks

if typ.TYPE_CHECKING:
    import github3


@dataclasses.dataclass(frozen=True, slots=True)
class RepositoryPlan:
    """Describe the steps required to prepare an estate repository.

    ``slug``, ``owner``, and ``name`` are the one repository identity the plan
    resolved — ``owner``/``name`` are split from ``slug`` — so provisioning
    reads them all from here rather than receiving parallel arguments.
    """

    needs_creation: bool
    slug: str | None
    owner: str | None
    name: str | None
    client: github3.GitHub | None


def _resolve_and_confirm_owner(
    slug: str | None,
    github_owner: str | None,
    confirmer: typ.Callable[[str], bool],
) -> str | None:
    """Resolve github_owner and prompt when inferred from the estate slug."""
    if github_owner is not None:
        return _resolve_github_owner(slug, github_owner)

    inferred_owner = _owner_from_slug(slug)
    if inferred_owner and not confirmer(
        "Inferred github_owner "
        f"{inferred_owner!r} from estate repo {slug!r}. "
        "Use this? [y/N]: ",
    ):
        raise GitHubOwnerConfirmationAbortedError
    return inferred_owner


def _split_slug(slug: str) -> tuple[str, str]:
    """Return owner/name for a GitHub slug or raise when invalid."""
    if slug.count("/") != 1:
        raise RepositoryIdentityError
    owner, name = slug.split("/", 1)
    if not owner or not name:
        raise RepositoryIdentityError
    return owner, name


def _prepare_repository(
    repo_url: str,
    slug: str | None,
    github_token: str | None,
    client_factory: typ.Callable[[str | None], github3.GitHub] | None,
) -> RepositoryPlan:
    """Probe the remote repository and decide whether provisioning is required."""
    probe = _probe_remote(repo_url)
    if not probe.exists:
        return _plan_missing_repository(slug)
    if not probe.reachable:
        return _plan_unreachable_repository(
            repo_url,
            slug,
            github_token,
            client_factory,
        )
    return _plan_reachable_repository(repo_url, probe)


def _plan_missing_repository(slug: str | None) -> RepositoryPlan:
    """Plan provisioning for a remote the probe reports as absent."""
    if not slug:
        raise UnsupportedRepositoryCreationError
    owner, name = _split_slug(slug)
    return RepositoryPlan(
        needs_creation=True,
        slug=slug,
        owner=owner,
        name=name,
        client=None,
    )


def _plan_reachable_repository(repo_url: str, probe: RemoteProbe) -> RepositoryPlan:
    """Plan for a reachable, existing remote; only empty ones are usable.

    Callers must route only reachable probes here, so no reachability check is
    repeated. A non-empty remote is rejected without contacting GitHub.
    """
    if not probe.empty:
        raise NonEmptyRepositoryError(repo_url)
    return RepositoryPlan(
        needs_creation=False,
        slug=None,
        owner=None,
        name=None,
        client=None,
    )


def _plan_unreachable_repository(
    repo_url: str,
    slug: str | None,
    github_token: str | None,
    client_factory: typ.Callable[[str | None], github3.GitHub] | None,
) -> RepositoryPlan:
    """Plan for an unreachable remote by asking GitHub whether it exists.

    A repository GitHub can see but SSH cannot reach is inaccessible rather
    than missing. The constructed client is returned in the plan so
    ``_ensure_repository_exists`` reuses it instead of authenticating twice.
    """
    if not slug:
        raise RepositoryUnreachableError(repo_url)
    client = _build_client(github_token, client_factory)
    owner, name = _split_slug(slug)
    if _lookup_repository(client, repo_url, owner, name):
        raise RepositoryInaccessibleError(repo_url)
    return RepositoryPlan(
        needs_creation=True,
        slug=slug,
        owner=owner,
        name=name,
        client=client,
    )


def _lookup_repository(
    client: github3.GitHub,
    repo_url: str,
    owner: str,
    name: str,
) -> object | None:
    """Return the GitHub repository *owner*/*name*, or ``None`` when absent.

    ``GitHub.repository`` signals an absent repository by raising rather than
    returning ``None``, so a ``NotFoundError`` is the answer "it is not there"
    and must not escape. Every other GitHub failure leaves the remote's state
    unknown, so it is translated rather than surfaced raw: a rejected call is
    an authentication problem, and anything else means the lookup itself could
    not be completed.
    """
    try:
        return client.repository(owner, name)
    except github3_exceptions.NotFoundError:
        return None
    except (
        github3_exceptions.AuthenticationFailed,
        github3_exceptions.ForbiddenError,
    ) as error:
        raise GitHubAuthenticationError from error
    except github3_exceptions.GitHubException as error:
        raise RepositoryUnreachableError(repo_url) from error


def _ensure_repository_exists(
    plan: RepositoryPlan,
    github_token: str | None,
    client_factory: typ.Callable[[str | None], github3.GitHub] | None,
    confirmer: typ.Callable[[str], bool],
) -> None:
    """Create the GitHub repository described by *plan*.

    The step order is load-bearing: identity is required before any client is
    built, the operator is prompted before the owner/name pair is validated,
    and nothing is created until the prompt is accepted.
    """
    slug = _require_repository_slug(plan.slug)
    client = plan.client or _build_client(github_token, client_factory)
    _confirm_repository_creation(slug, confirmer)
    owner, name = _require_repository_identity(plan)
    _create_repository(client, owner, name)


def _require_repository_slug(slug: str | None) -> str:
    """Return a creation slug or raise when repository identity is absent."""
    if not slug:
        raise RepositorySlugUnknownError
    return slug


def _confirm_repository_creation(
    slug: str,
    confirmer: typ.Callable[[str], bool],
) -> None:
    """Require confirmation before creating the repository."""
    if not confirmer(f"Create GitHub repository {slug}? [y/N]: "):
        raise EstateCreationAbortedError


def _require_repository_identity(
    plan: RepositoryPlan,
) -> tuple[str, str]:
    """Return the owner/name pair required by the GitHub creation API."""
    if not plan.owner or not plan.name:
        raise RepositoryIdentityError
    return plan.owner, plan.name


def _prompt_yes_no(message: str) -> bool:
    """Return whether the operator answered *message* affirmatively."""
    response = input(message)
    return response.strip().lower() in {"y", "yes"}


def _owner_from_slug(slug: str | None) -> str | None:
    """Return the normalised owner half of *slug*, or ``None`` without one."""
    if not slug:
        return None
    owner, _, _ = slug.partition("/")
    return _normalise_owner(owner)


def _resolve_github_owner(
    slug: str | None,
    explicit_owner: str | None,
) -> str | None:
    """Return the explicit owner when given, else the one inferred from *slug*."""
    if explicit_owner is not None:
        if owner := _normalise_owner(explicit_owner):
            return owner
        raise MissingGitHubOwnerError
    return _owner_from_slug(slug)


def _require_owner(owner: str | None) -> str:
    """Return *owner* or reject the estate as having no resolvable owner."""
    if not owner:
        raise MissingGitHubOwnerError
    return owner
