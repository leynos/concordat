"""Unit tests for concordat enrolment helpers."""

from __future__ import annotations

import pathlib
import typing as typ

import pygit2
import pytest
from ruamel.yaml import YAML

from concordat.enrol import (
    COMMIT_MESSAGE,
    CONCORDAT_DOCUMENT,
    CONCORDAT_FILENAME,
    DISENROL_COMMIT_MESSAGE,
    ConcordatError,
    disenrol_repositories,
    enrol_repositories,
)

if typ.TYPE_CHECKING:
    from tests.conftest import GitRepo
else:
    GitRepo = typ.Any  # pragma: no cover - runtime fallback for type hints


def _load_yaml(path: pathlib.Path) -> dict[str, object]:
    path = pathlib.Path(path)
    parser = YAML(typ="safe")
    parser.version = (1, 2)
    parser.default_flow_style = False
    with path.open("r", encoding="utf-8") as handle:
        data = parser.load(handle)
    return dict(data)


def test_enrol_creates_document_and_commit(git_repo: GitRepo) -> None:
    """Create the enrolment file and record a commit."""
    outcomes = enrol_repositories([str(git_repo.path)])
    outcome = outcomes[0]

    assert outcome.created is True
    assert outcome.committed is True
    assert outcome.pushed is False

    document_path = git_repo.path / CONCORDAT_FILENAME
    assert document_path.exists()
    assert _load_yaml(document_path) == CONCORDAT_DOCUMENT

    commit = git_repo.repository[git_repo.repository.head.target]
    assert isinstance(commit, pygit2.Commit)
    assert commit.message == COMMIT_MESSAGE


def test_enrol_is_idempotent(git_repo: GitRepo) -> None:
    """Re-enrolling a repository performs no additional work."""
    first_outcome = enrol_repositories([str(git_repo.path)])[0]
    assert first_outcome.created is True

    original_head = git_repo.repository.head.target
    second_outcome = enrol_repositories([str(git_repo.path)])[0]

    assert second_outcome.created is False
    assert second_outcome.committed is False
    assert second_outcome.pushed is False
    assert git_repo.repository.head.target == original_head


def test_enrol_requires_repository() -> None:
    """The command requires at least one repository."""
    with pytest.raises(ConcordatError):
        enrol_repositories([])


def test_enrol_remote_repository_pushes(
    git_repo: GitRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote repositories trigger a push after enrolment."""
    pushed: list[bool] = []

    def fake_clone(*args: object, **kwargs: object) -> object:
        return git_repo.repository

    def fake_push(repository: object, callbacks: object) -> None:
        pushed.append(True)

    monkeypatch.setattr("concordat.enrol.pygit2.clone_repository", fake_clone)
    monkeypatch.setattr("concordat.enrol._push_document", fake_push)

    outcomes = enrol_repositories(["git@github.com:example/repo.git"])

    assert outcomes[0].created is True
    assert outcomes[0].pushed is True
    assert pushed == [True]


def test_disenrol_updates_document_and_commit(git_repo: GitRepo) -> None:
    """Disenrolment flips the flag and records a commit."""
    enrol_repositories([str(git_repo.path)])

    outcomes = disenrol_repositories([str(git_repo.path)])
    outcome = outcomes[0]

    assert outcome.updated is True
    assert outcome.committed is True
    assert outcome.pushed is False

    document_path = git_repo.path / CONCORDAT_FILENAME
    data = _load_yaml(document_path)
    assert data["enrolled"] is False

    commit = git_repo.repository[git_repo.repository.head.target]
    assert isinstance(commit, pygit2.Commit)
    assert commit.message == DISENROL_COMMIT_MESSAGE


def test_disenrol_is_idempotent(git_repo: GitRepo) -> None:
    """Repeated disenrolment performs no additional work."""
    enrol_repositories([str(git_repo.path)])

    first_outcome = disenrol_repositories([str(git_repo.path)])[0]
    assert first_outcome.updated is True

    original_head = git_repo.repository.head.target
    second_outcome = disenrol_repositories([str(git_repo.path)])[0]

    assert second_outcome.updated is False
    assert second_outcome.committed is False
    assert second_outcome.pushed is False
    assert git_repo.repository.head.target == original_head


def test_disenrol_requires_existing_document(git_repo: GitRepo) -> None:
    """A repository must be enrolled before it can be disenrolled."""
    with pytest.raises(ConcordatError):
        disenrol_repositories([str(git_repo.path)])


def test_disenrol_remote_repository_pushes(
    git_repo: GitRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote repositories trigger a push after disenrolment."""
    enrol_repositories([str(git_repo.path)])

    pushed: list[bool] = []

    def fake_clone(*args: object, **kwargs: object) -> object:
        return git_repo.repository

    def fake_push(repository: object, callbacks: object) -> None:
        pushed.append(True)

    monkeypatch.setattr("concordat.enrol.pygit2.clone_repository", fake_clone)
    monkeypatch.setattr("concordat.enrol._push_document", fake_push)

    outcomes = disenrol_repositories(["git@github.com:example/repo.git"])

    assert outcomes[0].updated is True
    assert outcomes[0].pushed is True
    assert pushed == [True]
