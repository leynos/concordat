"""GitHub and git repository lifecycle for estate initialisation.

This module owns the side-effecting repository work behind
``concordat estate init``: probing the remote, provisioning it through the
GitHub API, bootstrapping the bundled template, and reading an estate's
inventory. :mod:`concordat.estate` is the façade that orchestrates these
steps; it imports this module, never the reverse.
"""

from __future__ import annotations

import dataclasses
import shutil
import typing as typ
from pathlib import Path
from tempfile import TemporaryDirectory

import github3
import pygit2
from github3 import exceptions as github3_exceptions
from pygit2 import RemoteCallbacks

from .estate_config import EstateRecord, _normalise_owner, _yaml
from .estate_errors import (
    EstateCreationAbortedError,
    EstateInventoryMissingError,
    GitHubAuthenticationError,
    GitHubClientInitializationError,
    GitHubOrganizationAuthenticationError,
    GitHubOwnerConfirmationAbortedError,
    GitHubRepositoryAuthenticationError,
    GitHubRepositoryCreationAuthenticationError,
    MissingGitHubOwnerError,
    MissingGitHubTokenError,
    NonEmptyRepositoryError,
    RepositoryCreationPermissionError,
    RepositoryIdentityError,
    RepositoryInaccessibleError,
    RepositorySlugUnknownError,
    RepositoryUnreachableError,
    TemplateMissingError,
    TemplatePushError,
    UnsupportedRepositoryCreationError,
)
from .gitutils import build_remote_callbacks


@dataclasses.dataclass(frozen=True, slots=True)
class RemoteProbe:
    """Describe the observed state of a remote repository."""

    reachable: bool
    exists: bool
    empty: bool
    error: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RepositoryPlan:
    """Describe the steps required to prepare an estate repository."""

    needs_creation: bool
    owner: str | None
    name: str | None
    client: github3.GitHub | None


def default_template_root() -> Path:
    """Return the repository template bundled with concordat."""
    return Path(__file__).resolve().parents[1] / "platform-standards"


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
    if client.repository(owner, name):
        raise RepositoryInaccessibleError(repo_url)
    return RepositoryPlan(
        needs_creation=True,
        owner=owner,
        name=name,
        client=client,
    )


def _ensure_repository_exists(
    slug: str | None,
    owner: str | None,
    name: str | None,
    client: github3.GitHub | None,
    github_token: str | None,
    client_factory: typ.Callable[[str | None], github3.GitHub] | None,
    confirmer: typ.Callable[[str], bool],
) -> None:
    """Create the GitHub repository when it does not yet exist."""
    if not slug:
        raise RepositorySlugUnknownError

    resolved_client = client or _build_client(github_token, client_factory)
    if not confirmer(
        f"Create GitHub repository {slug}? [y/N]: ",
    ):
        raise EstateCreationAbortedError
    if not owner or not name:
        raise RepositoryIdentityError
    _create_repository(resolved_client, owner, name)
    return


def _probe_remote(repo_url: str) -> RemoteProbe:
    callbacks = build_remote_callbacks(repo_url)
    with TemporaryDirectory(prefix="concordat-estate-probe-") as temp_root:
        repository = pygit2.init_repository(temp_root)
        remote = repository.remotes.create("origin", repo_url)
        try:
            refs = remote.ls_remotes(callbacks=callbacks)
        except pygit2.GitError as error:
            return RemoteProbe(
                reachable=False,
                exists=False,
                empty=True,
                error=str(error),
            )
    return RemoteProbe(reachable=True, exists=True, empty=not refs)


def _collect_inventory(record: EstateRecord) -> list[str]:
    callbacks = build_remote_callbacks(record.repo_url)
    with TemporaryDirectory(prefix="concordat-estate-") as temp_root:
        repository = pygit2.clone_repository(
            record.repo_url,
            temp_root,
            callbacks=callbacks,
        )
        workdir = Path(repository.workdir or temp_root)
        inventory_path = workdir / record.inventory_path
        if not inventory_path.exists():
            raise EstateInventoryMissingError(record.alias, record.inventory_path)
        contents = _yaml.load(inventory_path.read_text(encoding="utf-8")) or {}
        repos = contents.get("repositories") or []
        slugs: set[str] = set()
        for entry in repos:
            if not isinstance(entry, dict):
                continue
            slug = entry.get("name")
            if isinstance(slug, str) and slug.strip():
                slugs.add(slug.strip())
        return sorted(_slug_to_git_url(slug) for slug in slugs)


def _slug_to_git_url(slug: str) -> str:
    if slug.startswith("git@") or slug.startswith("ssh://"):
        return slug
    if slug.startswith("https://") or slug.startswith("http://"):
        return slug
    return f"git@github.com:{slug}.git"


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


def _bootstrap_template(
    repo_url: str,
    *,
    branch: str,
    template_root: Path,
    inventory_path: str,
    callbacks: RemoteCallbacks | None,
) -> None:
    if not template_root.exists():
        raise TemplateMissingError(template_root)
    with TemporaryDirectory(prefix="concordat-estate-template-") as temp_root:
        target = Path(temp_root, "estate")
        shutil.copytree(template_root, target, dirs_exist_ok=True)
        _sanitize_inventory(target / inventory_path)
        repository = pygit2.init_repository(str(target), initial_head=branch)
        index = repository.index
        index.add_all()
        index.write()
        tree_oid = index.write_tree()
        signature = pygit2.Signature("concordat", "concordat@local")
        repository.create_commit(
            f"refs/heads/{branch}",
            signature,
            signature,
            "chore: bootstrap platform-standards template",
            tree_oid,
            [],
        )
        repo_remote = repository.remotes.create("origin", repo_url)
        refspec = f"refs/heads/{branch}:refs/heads/{branch}"
        try:
            repo_remote.push([refspec], callbacks=callbacks)
        except pygit2.GitError as error:
            raise TemplatePushError(str(error)) from error
        _set_remote_head_if_local(repo_url, branch)


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


def _sanitize_inventory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    loaded: object = {}
    if path.exists():
        loaded = _yaml.load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        loaded = {}
    loaded.setdefault("schema_version", 1)
    loaded["repositories"] = []
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(loaded, handle)


def _set_remote_head_if_local(repo_url: str, branch: str) -> None:
    path = Path(repo_url)
    if not path.exists():
        return
    try:
        remote = pygit2.Repository(str(path))
    except pygit2.GitError:
        return
    try:
        remote.set_head(f"refs/heads/{branch}")
    except pygit2.GitError:
        # Ignore repositories that refuse head updates (e.g., already configured).
        return


def _prompt_yes_no(message: str) -> bool:
    response = input(message)
    return response.strip().lower() in {"y", "yes"}


def _owner_from_slug(slug: str | None) -> str | None:
    if not slug:
        return None
    owner, _, _ = slug.partition("/")
    return _normalise_owner(owner)


def _resolve_github_owner(
    slug: str | None,
    explicit_owner: str | None,
) -> str | None:
    if explicit_owner is not None:
        if owner := _normalise_owner(explicit_owner):
            return owner
        raise MissingGitHubOwnerError
    return _owner_from_slug(slug)


def _require_owner(owner: str | None) -> str:
    if not owner:
        raise MissingGitHubOwnerError
    return owner
