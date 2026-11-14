"""Shared history builder and fixtures for the revision-list tests."""

from __future__ import annotations

import sys
import typing as typ

import pygit2

from scripts import whitaker_revisions

if typ.TYPE_CHECKING:
    import pathlib

    import pytest

DIRECTORY: typ.Final = ".github/actions/install-whitaker"
ACTION_V1: typ.Final = "name: install-whitaker\nversion: 1\n"
ACTION_V2: typ.Final = "name: install-whitaker\nversion: 2\n"
SIGNATURE: typ.Final = pygit2.Signature(
    "Test", "test@example.invalid", 1_700_000_000, 0
)


class History:
    """Build a linear shared-actions history one commit at a time."""

    def __init__(self, path: pathlib.Path) -> None:
        """Initialise an empty repository at *path*."""
        self.repository = pygit2.init_repository(str(path), initial_head="main")
        self.parents: list[pygit2.Oid] = []
        self.executable: frozenset[str] = frozenset()

    def commit(
        self,
        files: dict[str, str],
        message: str,
        executable: frozenset[str] = frozenset(),
    ) -> str:
        """Commit *files* as the whole tree and return the new commit id.

        Paths in *executable* get the executable file mode.

        Returns
        -------
        str
            The id of the new commit.
        """
        self.executable = executable
        tree = self._tree(files)
        oid = self.repository.create_commit(
            "refs/heads/main", SIGNATURE, SIGNATURE, message, tree, self.parents
        )
        self.parents = [oid]
        return str(oid)

    def branch(self, files: dict[str, str], message: str, parent: str) -> str:
        """Commit *files* on a side branch off *parent* without moving main."""
        oid = self.repository.create_commit(
            None,
            SIGNATURE,
            SIGNATURE,
            message,
            self._tree(files),
            [pygit2.Oid(hex=parent)],
        )
        return str(oid)

    def merge(self, files: dict[str, str], message: str, second: str) -> str:
        """Commit a merge of *second* into main, with main as first parent."""
        oid = self.repository.create_commit(
            "refs/heads/main",
            SIGNATURE,
            SIGNATURE,
            message,
            self._tree(files),
            [*self.parents, pygit2.Oid(hex=second)],
        )
        self.parents = [oid]
        return str(oid)

    def _tree(self, files: dict[str, str], prefix: str = "") -> pygit2.Oid:
        """Write a nested tree for *files* and return its id."""
        builder = self.repository.TreeBuilder()
        nested: dict[str, dict[str, str]] = {}
        for name, text in files.items():
            head, _, tail = name.partition("/")
            if tail:
                nested.setdefault(head, {})[tail] = text
            else:
                blob = self.repository.create_blob(text.encode())
                mode = (
                    pygit2.GIT_FILEMODE_BLOB_EXECUTABLE
                    if f"{prefix}{name}" in self.executable
                    else pygit2.GIT_FILEMODE_BLOB
                )
                builder.insert(name, blob, mode)
        for head, children in nested.items():
            builder.insert(
                head, self._tree(children, f"{prefix}{head}/"), pygit2.GIT_FILEMODE_TREE
            )
        return builder.write()


def action_files(action: str, other: str = "a") -> dict[str, str]:
    """Return a tree holding *action* in the directory and *other* elsewhere."""
    return {f"{DIRECTORY}/action.yml": action, "README.md": other}


def tag(history: History, name: str, commit: str, *, annotated: bool = True) -> None:
    """Tag *commit* as *name*, annotated by default as a maintainer would."""
    repository = history.repository
    oid = pygit2.Oid(hex=commit)
    if annotated:
        repository.create_tag(
            name, oid, pygit2.enums.ObjectType.COMMIT, SIGNATURE, f"Certify {name}"
        )
    else:
        repository.references.create(f"refs/tags/{name}", oid)


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    clone: pathlib.Path | str,
    command: str,
    *options: str,
) -> int:
    """Run the real entry point as `whitaker_revisions.py <command> <clone>`.

    The tip is `main` unless *options* name one.

    Returns
    -------
    int
        The command's exit status.
    """
    tip = [] if "--tip" in options else ["--tip", "main"]
    monkeypatch.setattr(
        sys, "argv", ["whitaker_revisions", command, str(clone), *options, *tip]
    )
    return whitaker_revisions.main()
