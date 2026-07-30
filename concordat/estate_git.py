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


def _bootstrap_template(
    repo_url: str,
    bootstrap: TemplateBootstrap,
) -> None:
    """Seed *repo_url* from the bundled template as one atomic operation.

    Validates template availability, copies and sanitizes the template,
    initialises and commits a Git repository, pushes the target branch, and
    sets the local remote HEAD where applicable.
    """
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
