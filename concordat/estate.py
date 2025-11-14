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

_yaml = YAML(typ="safe")
_yaml.version = (1, 2)
_yaml.default_flow_style = False


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


@dataclasses.dataclass(frozen=True, slots=True)
class RemoteProbe:
    """Describe the observed state of a remote repository."""

    reachable: bool
    exists: bool
    empty: bool
    error: str | None = None


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
        raise ConcordatError(f"Estate {alias!r} is not configured.")
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
        raise ConcordatError(f"Estate alias {record.alias!r} already exists.")
    estates[record.alias] = {
        "repo_url": record.repo_url,
        "branch": record.branch,
        "inventory_path": record.inventory_path,
    }
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
            raise ConcordatError(f"Estate {alias!r} is not configured.")
    else:
        record = get_active_estate(config_path)
        if not record:
            raise ConcordatError(
                "No active estate configured; run `concordat estate use` first."
            )
    return _collect_inventory(record)


def init_estate(
    alias: str,
    repo_url: str,
    *,
    branch: str = DEFAULT_BRANCH,
    inventory_path: str = DEFAULT_INVENTORY_PATH,
    github_token: str | None = None,
    template_root: Path | None = None,
    confirm: typ.Callable[[str], bool] | None = None,
    client_factory: typ.Callable[[str | None], github3.GitHub] | None = None,
    config_path: Path | None = None,
) -> EstateRecord:
    """Initialise an estate repository from the bundled template."""
    if not alias:
        raise ConcordatError("Estate alias is required.")
    records = _load_estates(config_path)
    if alias in records:
        raise ConcordatError(f"Estate alias {alias!r} already configured.")

    probe = _probe_remote(repo_url)
    slug = parse_github_slug(repo_url)
    needs_creation = not probe.exists

    client: github3.GitHub | None = None
    owner = name = None
    if needs_creation:
        if not slug:
            raise ConcordatError(
                "Only GitHub repositories can be created automatically."
            )
        owner, name = slug.split("/", 1)
    elif probe.reachable and not probe.empty:
        raise ConcordatError(
            f"Repository {repo_url!r} already contains commits; "
            "estate init requires an empty repository.",
        )
    elif not probe.reachable:
        if not slug:
            raise ConcordatError(
                f"Cannot reach {repo_url!r}; provide a GitHub SSH URL."
            )
        client = _build_client(github_token, client_factory)
        owner, name = slug.split("/", 1)
        if client.repository(owner, name):
            raise ConcordatError(
                f"Repository {repo_url!r} exists but could not be reached via SSH; "
                "ensure your agent exposes the required key."
            )
        needs_creation = True

    if needs_creation:
        if not slug:
            raise ConcordatError(
                "Unable to determine repository slug for automatic creation."
            )
        client = client or _build_client(github_token, client_factory)
        if not confirm:
            confirm = _prompt_yes_no
        if not confirm(
            f"Create GitHub repository {slug}? [y/N]: ",
        ):
            raise ConcordatError("Estate creation aborted by user.")
        _create_repository(client, owner, name)

    callbacks = build_remote_callbacks(repo_url)
    _bootstrap_template(
        repo_url,
        branch=branch,
        template_root=template_root or default_template_root(),
        callbacks=callbacks,
    )

    record = EstateRecord(
        alias=alias,
        repo_url=repo_url,
        branch=branch,
        inventory_path=inventory_path,
    )
    register_estate(record, config_path=config_path, set_active_if_missing=True)
    return record


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
            raise ConcordatError(
                f"Inventory {record.inventory_path!r} missing from estate "
                f"{record.alias!r}.",
            )
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
    except github3.exceptions.NotFoundError:
        org = None

    if org:
        org.create_repository(
            name,
            private=True,
            auto_init=False,
            description="Platform standards repository managed by concordat",
        )
        return

    user = client.me()
    if not user or user.login != owner:
        raise ConcordatError(
            f"Authenticated user cannot create repositories under {owner!r}."
        )
    client.create_repository(
        name,
        private=True,
        auto_init=False,
        description="Platform standards repository managed by concordat",
    )


def _bootstrap_template(
    repo_url: str,
    *,
    branch: str,
    template_root: Path,
    callbacks: RemoteCallbacks | None,
) -> None:
    if not template_root.exists():
        raise ConcordatError(f"Template directory {template_root} is missing.")
    with TemporaryDirectory(prefix="concordat-estate-template-") as temp_root:
        target = Path(temp_root, "estate")
        shutil.copytree(template_root, target, dirs_exist_ok=True)
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
            raise ConcordatError(f"Failed to push estate template: {error}") from error


def _build_client(
    token: str | None,
    client_factory: typ.Callable[[str | None], github3.GitHub] | None = None,
) -> github3.GitHub:
    if not token and client_factory is None:
        raise ConcordatError(
            "GITHUB_TOKEN is required to create repositories automatically."
        )
    factory = client_factory or github3.login
    return factory(token)


def _load_config(config_path: Path | None) -> dict[str, typ.Any]:
    path = config_path or default_config_path()
    provider = _YamlConfig(path, must_exist=False)
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
                record = EstateRecord(
                    alias=alias,
                    repo_url=repo_url,
                    branch=str(branch),
                    inventory_path=str(inventory_path),
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
    response = input(message)  # noqa: S322
    return response.strip().lower() in {"y", "yes"}
