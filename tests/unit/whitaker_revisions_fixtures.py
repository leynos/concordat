"""Fixtures for the revision-list tests, registered as a pytest plugin."""

from __future__ import annotations

import pathlib
import re
import sys
import typing as typ

import pytest

from concordat.rules import packages
from scripts import whitaker_revisions
from tests.unit.whitaker_revisions_support import (
    ACTION_V1,
    History,
    action_files,
    rule_copy,
    run_main,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@pytest.fixture
def history(tmp_path: pathlib.Path) -> History:
    """Return an empty synthetic shared-actions history."""
    return History(tmp_path)


@pytest.fixture
def rule_package(
    tmp_path: pathlib.Path, history: History, monkeypatch: pytest.MonkeyPatch
) -> tuple[pathlib.Path, str, str]:
    """Point the command at a copy of the rule manifest rooted in *history*."""
    root = history.commit(action_files(ACTION_V1), "root")
    later = history.commit(action_files(ACTION_V1, "b"), "readme only")
    package = rule_copy(tmp_path, root)
    monkeypatch.setattr(packages, "rule_package_dir", lambda _rule_id: package)
    return package, root, later


@pytest.fixture
def rootless_package(
    rule_package: tuple[pathlib.Path, str, str],
) -> tuple[pathlib.Path, str, str]:
    """Return *rule_package* with its manifest declaring no approved roots."""
    manifest = rule_package[0] / "rule.yaml"
    text = manifest.read_text("utf-8")
    emptied = re.sub(
        r'(install_whitaker_roots:)\n(?:      - "[0-9a-f]{40}"\n)+', r"\1 []\n", text
    )
    if emptied == text:
        pytest.fail("the manifest has no approved roots to remove")
    manifest.write_text(emptied, "utf-8")
    return rule_package


@pytest.fixture
def cli(history: History, monkeypatch: pytest.MonkeyPatch) -> cabc.Callable[[str], int]:
    """Return a runner that executes one command against the history's clone."""
    clone = pathlib.Path(history.repository.workdir)
    return lambda command: run_main(monkeypatch, clone, command)


@pytest.fixture
def default_tip_cli(
    rule_package: tuple[pathlib.Path, str, str],
    history: History,
    monkeypatch: pytest.MonkeyPatch,
) -> cabc.Callable[[str], int]:
    """Return a runner for a command with no `--tip`; `origin/main` is the root."""
    _package, root, _later = rule_package
    history.repository.references.create("refs/remotes/origin/main", root)
    clone = pathlib.Path(history.repository.workdir)

    def run(command: str) -> int:
        monkeypatch.setattr(sys, "argv", ["whitaker_revisions", command, str(clone)])
        return whitaker_revisions.main()

    return run
