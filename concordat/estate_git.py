"""git operations against estate remotes: probing, inventory, and templates.

This module owns the pygit2 work behind ``concordat estate init`` and
``concordat ls``: observing a remote's state, cloning an estate to read its
inventory, and seeding a new estate from the bundled template. It knows
nothing about the GitHub API, the estate-init decision flow, or
:mod:`concordat.estate`.
"""

from __future__ import annotations

import dataclasses
import shutil
import typing as typ
from pathlib import Path
from tempfile import TemporaryDirectory

import pygit2
from pygit2 import RemoteCallbacks

from .estate_config import EstateRecord, _yaml
from .estate_errors import (
    EstateInventoryMissingError,
    TemplateMissingError,
    TemplatePushError,
)
from .gitutils import build_remote_callbacks

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@dataclasses.dataclass(frozen=True, slots=True)
class RemoteProbe:
    """Describe the observed state of a remote repository."""

    reachable: bool
    exists: bool
    empty: bool
    error: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class TemplateBootstrap:
    """Inputs controlling one estate-template bootstrap operation."""

    branch: str
    template_root: Path
    inventory_path: str
    callbacks: RemoteCallbacks | None


def default_template_root() -> Path:
    """Return the repository template bundled with concordat."""
    return Path(__file__).resolve().parents[1] / "platform-standards"


def _probe_remote(repo_url: str) -> RemoteProbe:
    """Report whether *repo_url* can be listed and whether it holds refs."""
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
    """Clone an estate and return its enrolled repository URLs."""
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
            raise EstateInventoryMissingError(
                record.alias,
                record.inventory_path,
            )
        return _inventory_urls(inventory_path)


def _inventory_slugs(entries: cabc.Iterable[object]) -> cabc.Iterator[str]:
    """Yield the trimmed name of each usable inventory entry."""
    for entry in entries:
        match entry:
            case {"name": str() as name} if name.strip():
                yield name.strip()


def _inventory_urls(inventory_path: Path) -> list[str]:
    """Load, normalize, deduplicate, and sort repository URLs from inventory."""
    # `or {}` only rescues a falsy decode. A truthy scalar, string, or list
    # at the document root is still not a mapping, and `.get` on one raises
    # `AttributeError`; an inventory that is not a mapping has no
    # repositories, so it reads as empty.
    match _yaml.load(inventory_path.read_text(encoding="utf-8")):
        case dict() as contents:
            pass
        case _:
            contents = {}
    # Only a list is an inventory. A scalar such as `5` would raise on
    # iteration, and a bare string would be walked character by character and
    # silently yield nothing; both are malformed and read as empty.
    match contents.get("repositories"):
        case list() as repositories:
            pass
        case _:
            repositories = []
    slugs = set(_inventory_slugs(repositories))
    return sorted(_slug_to_git_url(slug) for slug in slugs)


def _slug_to_git_url(slug: str) -> str:
    """Return *slug* as a Git URL, qualifying bare ``owner/name`` slugs."""
    if slug.startswith("git@") or slug.startswith("ssh://"):
        return slug
    if slug.startswith("https://") or slug.startswith("http://"):
        return slug
    return f"git@github.com:{slug}.git"


def _bootstrap_template(
    repo_url: str,
    bootstrap: TemplateBootstrap,
) -> None:
    """Seed *repo_url* from the bundled template as one atomic operation."""
    branch = bootstrap.branch
    if not bootstrap.template_root.exists():
        raise TemplateMissingError(bootstrap.template_root)
    with TemporaryDirectory(prefix="concordat-estate-template-") as temp_root:
        target = Path(temp_root, "estate")
        shutil.copytree(bootstrap.template_root, target, dirs_exist_ok=True)
        _sanitize_inventory(target / bootstrap.inventory_path)
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
            repo_remote.push([refspec], callbacks=bootstrap.callbacks)
        except pygit2.GitError as error:
            raise TemplatePushError(str(error)) from error
        _set_remote_head_if_local(repo_url, branch)


def _sanitize_inventory(path: Path) -> None:
    """Rewrite the template inventory at *path* with no enrolled repositories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: object = {}
    if path.exists():
        raw = _yaml.load(path.read_text(encoding="utf-8")) or {}
    match raw:
        case dict() as loaded:
            pass
        case _:
            loaded = {}
    loaded.setdefault("schema_version", 1)
    loaded["repositories"] = []
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(loaded, handle)


def _set_remote_head_if_local(repo_url: str, branch: str) -> None:
    """Point a local remote's HEAD at *branch*, ignoring non-local remotes."""
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
