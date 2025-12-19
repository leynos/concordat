"""Estate management helpers for the concordat CLI."""

from __future__ import annotations

import dataclasses
import os
import shutil
import typing as typ
from pathlib import Path
from tempfile import TemporaryDirectory

import github3
import pygit2
from cyclopts import config as cyclopts_config
from github3 import exceptions as github3_exceptions
from pygit2 import RemoteCallbacks
from ruamel.yaml import YAML

from .errors import ConcordatError
from .gitutils import build_remote_callbacks
from .platform_standards import parse_github_slug

DEFAULT_BRANCH = "main"
DEFAULT_INVENTORY_PATH = "tofu/inventory/repositories.yaml"
CONFIG_FILENAME = "config.yaml"
ESTATE_SECTION = "estate"
ESTATE_COLLECTION_KEY = "estates"
ACTIVE_ESTATE_KEY = "active_estate"
ERROR_OWNER_REQUIRED = (
    "Unable to determine github_owner for the estate. Provide --github-owner "
    "when the remote URL is not a GitHub repository."
)

_yaml = YAML(typ="safe")
_yaml.default_flow_style = False
_yaml.explicit_start = False
_yaml.explicit_end = False
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.sort_base_mapping_type_on_output = False


class EstateError(ConcordatError):
    """Base class for estate-related Concordat errors."""


class EstateNotConfiguredError(EstateError):
    """Raised when referring to an unknown estate alias."""

    def __init__(self, alias: str) -> None:
        """Initialise the error with the missing alias."""
        super().__init__(f"Estate {alias!r} is not configured.")


class DuplicateEstateAliasError(EstateError):
    """Raised when attempting to register an alias twice."""

    def __init__(self, alias: str) -> None:
        """Initialise the error with the duplicate alias."""
        super().__init__(f"Estate alias {alias!r} already exists.")


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
        super().__init__(
            f"Repository {repo_url!r} already contains commits; "
            "estate init requires an empty repository."
        )


class RepositoryUnreachableError(EstateError):
    """Raised when the estate repo cannot be reached via SSH."""

    def __init__(self, repo_url: str) -> None:
        """Initialise the error with the unreachable repository."""
        super().__init__(f"Cannot reach {repo_url!r}; provide a GitHub SSH URL.")


class RepositoryInaccessibleError(EstateError):
    """Raised when GitHub reports a repo exists but SSH access fails."""

    def __init__(self, repo_url: str) -> None:
        """Initialise the error with the inaccessible repository."""
        super().__init__(
            f"Repository {repo_url!r} exists but could not be reached via SSH; "
            "ensure your agent exposes the required key."
        )


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
        super().__init__(f"Inventory {path!r} missing from estate {alias!r}.")


class RepositoryCreationPermissionError(EstateError):
    """Raised when neither the org nor the authenticated user can create the repo."""

    def __init__(self, owner: str) -> None:
        """Initialise the error with the owner namespace."""
        super().__init__(
            f"Authenticated user cannot create repositories under {owner!r}."
        )


class TemplateMissingError(EstateError):
    """Raised when the bundled template cannot be located."""

    def __init__(self, template_root: Path) -> None:
        """Initialise the error with the template path."""
        super().__init__(f"Template directory {template_root} is missing.")


class TemplatePushError(EstateError):
    """Raised when pushing the bootstrapped template fails."""

    def __init__(self, detail: str) -> None:
        """Initialise the error with the push failure detail."""
        super().__init__(f"Failed to push estate template: {detail}")


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


class MissingGitHubOwnerError(EstateError):
    """Raised when github_owner cannot be resolved for an estate."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__(ERROR_OWNER_REQUIRED)


class GitHubOrganizationAuthenticationError(GitHubAuthenticationError):
    """Raised when organisation-level auth fails."""

    def __init__(self, owner: str) -> None:
        """Initialise the error with the organisation owner."""
        super().__init__(
            f"GitHub authentication failed accessing organization {owner!r}. "
            "Ensure GITHUB_TOKEN includes the 'repo' scope and is valid."
        )


class GitHubRepositoryCreationAuthenticationError(GitHubAuthenticationError):
    """Raised when repo creation fails due to authentication."""

    def __init__(self, owner: str, name: str) -> None:
        """Initialise the error with the repository slug."""
        super().__init__(
            f"GitHub authentication failed creating {owner}/{name}. "
            "Ensure GITHUB_TOKEN includes the 'repo' scope and is valid."
        )


class GitHubRepositoryAuthenticationError(GitHubAuthenticationError):
    """Raised when generic repository operations fail during authentication."""

    def __init__(self) -> None:
        """Initialise the error message."""
        super().__init__(
            "GitHub authentication failed when creating the repository. "
            "Ensure GITHUB_TOKEN includes the 'repo' scope and is valid."
        )


class _YamlConfig(cyclopts_config.ConfigFromFile):
    """Cyclopts config provider backed by ruamel.yaml."""

    def _load_config(self, path: Path) -> dict[str, typ.Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            contents = _yaml.load(handle) or {}
        return dict(contents) if isinstance(contents, dict) else {}


@dataclasses.dataclass(frozen=True, slots=True)
class EstateRecord:
    """Configuration for a managed estate repository."""

    alias: str
    repo_url: str
    branch: str = DEFAULT_BRANCH
    inventory_path: str = DEFAULT_INVENTORY_PATH
    github_owner: str | None = None


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


def default_config_path() -> Path:
    """Return the path to the concordat configuration file."""
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".config"
    return base / "concordat" / CONFIG_FILENAME


def list_estates(config_path: Path | None = None) -> list[EstateRecord]:
    """Return every configured estate sorted by alias."""
    records = _load_estates(config_path)
    return sorted(records.values(), key=lambda record: record.alias)


def get_estate(
    alias: str,
    *,
    config_path: Path | None = None,
) -> EstateRecord | None:
    """Look up a specific estate by alias."""
    if not alias:
        return None
    return _load_estates(config_path).get(alias)


def get_active_estate(config_path: Path | None = None) -> EstateRecord | None:
    """Return the currently active estate."""
    estates = _load_estates(config_path)
    metadata = _load_metadata(config_path)
    active_alias = metadata.get(ACTIVE_ESTATE_KEY)
    if not active_alias:
        return None
    return estates.get(active_alias)


def set_active_estate(
    alias: str,
    *,
    config_path: Path | None = None,
) -> EstateRecord:
    """Mark the provided alias as the active estate."""
    estates = _load_estates(config_path)
    record = estates.get(alias)
    if not record:
        raise EstateNotConfiguredError(alias)
    data = _load_config(config_path)
    estate_section = data.setdefault(ESTATE_SECTION, {})
    estate_section[ACTIVE_ESTATE_KEY] = alias
    _write_config(data, config_path)
    return record


def register_estate(
    record: EstateRecord,
    *,
    config_path: Path | None = None,
    set_active_if_missing: bool = True,
) -> None:
    """Persist a new estate entry and optionally set it active."""
    data = _load_config(config_path)
    estate_section = data.setdefault(ESTATE_SECTION, {})
    estates = estate_section.setdefault(ESTATE_COLLECTION_KEY, {})
    if record.alias in estates:
        raise DuplicateEstateAliasError(record.alias)
    entry = {
        "repo_url": record.repo_url,
        "branch": record.branch,
        "inventory_path": record.inventory_path,
    }
    if record.github_owner:
        entry["github_owner"] = record.github_owner
    estates[record.alias] = entry
    if set_active_if_missing and not estate_section.get(ACTIVE_ESTATE_KEY):
        estate_section[ACTIVE_ESTATE_KEY] = record.alias
    _write_config(data, config_path)


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
    return _collect_inventory(record)


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
    records = _load_estates(config_path)
    if alias in records:
        raise DuplicateEstateAliasError(alias)

    confirmer = confirm or _prompt_yes_no
    slug = parse_github_slug(repo_url)
    resolved_owner = _resolve_and_confirm_owner(slug, github_owner, confirmer)
    estate_owner = _require_owner(resolved_owner)
    repository_plan = _prepare_repository(
        repo_url,
        slug,
        github_token,
        client_factory,
    )
    if repository_plan.needs_creation:
        _ensure_repository_exists(
            slug,
            repository_plan.owner,
            repository_plan.name,
            repository_plan.client,
            github_token,
            client_factory,
            confirmer,
        )

    callbacks = build_remote_callbacks(repo_url)
    _bootstrap_template(
        repo_url,
        branch=branch,
        template_root=template_root or default_template_root(),
        inventory_path=inventory_path,
        callbacks=callbacks,
    )

    record = EstateRecord(
        alias=alias,
        repo_url=repo_url,
        branch=branch,
        inventory_path=inventory_path,
        github_owner=estate_owner,
    )
    register_estate(record, config_path=config_path, set_active_if_missing=True)
    return record


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
    needs_creation = not probe.exists

    client: github3.GitHub | None = None
    owner = name = None

    if needs_creation:
        if not slug:
            raise UnsupportedRepositoryCreationError
        owner, name = _split_slug(slug)
    elif probe.reachable and not probe.empty:
        raise NonEmptyRepositoryError(repo_url)
    elif not probe.reachable:
        if not slug:
            raise RepositoryUnreachableError(repo_url)
        client = _build_client(github_token, client_factory)
        owner, name = _split_slug(slug)
        if client.repository(owner, name):
            raise RepositoryInaccessibleError(repo_url)
        needs_creation = True

    return RepositoryPlan(
        needs_creation=needs_creation,
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


def _load_config(config_path: Path | None) -> dict[str, typ.Any]:
    path = config_path or default_config_path()
    provider = _YamlConfig(path=str(path), must_exist=False)
    raw = provider.config or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _write_config(data: dict[str, typ.Any], config_path: Path | None) -> None:
    path = config_path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(data, handle)


def _load_estates(config_path: Path | None) -> dict[str, EstateRecord]:
    data = _load_config(config_path)
    estate_section = data.get(ESTATE_SECTION, {})
    raw_estates = estate_section.get(ESTATE_COLLECTION_KEY, {})
    result: dict[str, EstateRecord] = {}
    if isinstance(raw_estates, dict):
        for alias, payload in raw_estates.items():
            if isinstance(payload, str):
                record = EstateRecord(alias=alias, repo_url=payload)
            elif isinstance(payload, dict):
                repo_url = payload.get("repo_url")
                if not isinstance(repo_url, str):
                    continue
                branch = payload.get("branch", DEFAULT_BRANCH)
                inventory_path = payload.get(
                    "inventory_path",
                    DEFAULT_INVENTORY_PATH,
                )
                owner = payload.get("github_owner")
                record = EstateRecord(
                    alias=alias,
                    repo_url=repo_url,
                    branch=str(branch),
                    inventory_path=str(inventory_path),
                    github_owner=_normalise_owner(owner),
                )
            else:
                continue
            result[alias] = record
    return result


def _load_metadata(config_path: Path | None) -> dict[str, typ.Any]:
    data = _load_config(config_path)
    section = data.get(ESTATE_SECTION, {})
    return section if isinstance(section, dict) else {}


def _prompt_yes_no(message: str) -> bool:
    response = input(message)
    return response.strip().lower() in {"y", "yes"}


def _normalise_owner(owner: str | None) -> str | None:
    if owner is None:
        return None
    trimmed = owner.strip()
    return trimmed or None


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
