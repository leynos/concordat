"""Git operations used by the persistence workflow."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import concordat.persistence.gitops as gitops
from tests.unit.conftest import _make_repo


def test_commit_changes_creates_branch(tmp_path: Path) -> None:
    """_commit_changes creates and checks out a persistence branch."""
    tmp_path = Path(tmp_path)
    repo = _make_repo(tmp_path)
    target_file = tmp_path / "file.txt"
    target_file.write_text("content", encoding="utf-8")
    branch_name = gitops._commit_changes(
        repo,
        "main",
        [target_file],
        timestamp_factory=lambda: dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
    )
    assert branch_name.startswith("estate/persist-")
    assert branch_name in repo.branches.local
