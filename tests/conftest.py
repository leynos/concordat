"""Shared pytest fixtures for concordat tests."""

from __future__ import annotations

import dataclasses
import pathlib

import pygit2
import pytest


@dataclasses.dataclass(slots=True)
class GitRepo:
    """Expose repository handle and path for tests."""

    repository: pygit2.Repository
    path: pathlib.Path

    def read_text(self, relative_path: str) -> str:
        """Read a file relative to the repository root."""
        return (self.path / relative_path).read_text(encoding="utf-8")


@pytest.fixture
def git_repo(tmp_path: pathlib.Path) -> GitRepo:
    """Initialise a git repository with an initial commit for testing."""
    repo_path = pathlib.Path(tmp_path, "repo")
    repo_path.mkdir()
    repository = pygit2.init_repository(str(repo_path), initial_head="main")

    config = repository.config
    config["user.name"] = "Test User"
    config["user.email"] = "test@example.com"

    seed_file = repo_path / "README.md"
    seed_file.write_text("seed\n", encoding="utf-8")

    index = repository.index
    index.add("README.md")
    index.write()
    tree_oid = index.write_tree()

    signature = pygit2.Signature("Test User", "test@example.com")
    repository.create_commit(
        "refs/heads/main",
        signature,
        signature,
        "initial commit",
        tree_oid,
        [],
    )

    repository.set_head("refs/heads/main")

    return GitRepo(repository=repository, path=repo_path)
