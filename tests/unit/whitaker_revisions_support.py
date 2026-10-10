"""Shared history builder and fixtures for the revision-list tests."""

from __future__ import annotations

import re
import sys
import typing as typ

import pygit2
import pytest

from concordat.rules import packages
from concordat.rules.whitaker_revisions import replace_refs
from scripts import whitaker_revisions

if typ.TYPE_CHECKING:
    import pathlib

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

    def commit(self, files: dict[str, str], message: str) -> str:
        """Commit *files* as the whole tree and return the new commit id."""
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

    def _tree(self, files: dict[str, str]) -> pygit2.Oid:
        """Write a nested tree for *files* and return its id."""
        builder = self.repository.TreeBuilder()
        nested: dict[str, dict[str, str]] = {}
        for name, text in files.items():
            head, _, tail = name.partition("/")
            if tail:
                nested.setdefault(head, {})[tail] = text
            else:
                blob = self.repository.create_blob(text.encode())
                builder.insert(name, blob, pygit2.GIT_FILEMODE_BLOB)
        for head, children in nested.items():
            builder.insert(head, self._tree(children), pygit2.GIT_FILEMODE_TREE)
        return builder.write()


def action_files(action: str, other: str = "a") -> dict[str, str]:
    """Return a tree holding *action* in the directory and *other* elsewhere."""
    return {f"{DIRECTORY}/action.yml": action, "README.md": other}


def rule_copy(tmp_path: pathlib.Path, root: str) -> pathlib.Path:
    """Copy the shipped rule manifest with *root* as its only approved root."""
    real = packages.rule_package_dir("whitaker-provisioning")
    package = tmp_path / "package"
    package.mkdir()
    text = (real / "rule.yaml").read_text("utf-8")
    text = re.sub(
        r'(install_whitaker_roots:\n)((?:      - "[0-9a-f]{40}"\n)+)',
        lambda m: m.group(1) + f'      - "{root}"\n',
        text,
        count=1,
    )
    text = replace_refs(text, [root])
    (package / "rule.yaml").write_text(text, "utf-8")
    return package


def run_main(
    monkeypatch: pytest.MonkeyPatch, clone: pathlib.Path | str, command: str
) -> int:
    """Run the real entry point as `whitaker_revisions.py <command> <clone>`."""
    monkeypatch.setattr(
        sys, "argv", ["whitaker_revisions", command, str(clone), "--tip", "main"]
    )
    return whitaker_revisions.main()


def without_key(manifest: pathlib.Path, key: str) -> None:
    """Remove a default parameter, with any list items, from the manifest."""
    text = manifest.read_text("utf-8")
    stripped = re.sub(
        rf"^    {key}:.*\n(?:      - .*\n)*", "", text, flags=re.MULTILINE
    )
    if stripped == text:
        pytest.fail(f"{key} is not a default parameter of the manifest")
    manifest.write_text(stripped, "utf-8")


def set_default(manifest: pathlib.Path, key: str, replacement: str) -> None:
    """Replace a default parameter, with any list items, by *replacement* lines."""
    text = manifest.read_text("utf-8")
    changed = re.sub(
        rf"^    {key}:.*\n(?:      - .*\n)*",
        replacement,
        text,
        flags=re.MULTILINE,
    )
    if changed == text:
        pytest.fail(f"{key} is not a default parameter of the manifest")
    manifest.write_text(changed, "utf-8")
