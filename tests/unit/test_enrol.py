"""Unit tests for concordat enrolment helpers."""

from __future__ import annotations

import pathlib
import typing as typ
from contextlib import suppress

import pygit2
import pytest
from ruamel.yaml import YAML

from concordat.enrol import (
    COMMIT_MESSAGE,
    CONCORDAT_DOCUMENT,
    CONCORDAT_FILENAME,
    DISENROL_COMMIT_MESSAGE,
    ConcordatError,
    _platform_pr_result,
    _slug_with_owner_guard,
    disenrol_repositories,
    enrol_repositories,
)
from concordat.platform_standards import (
    PlatformStandardsConfig,
    PlatformStandardsResult,
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


def _set_origin(remote_repo: pygit2.Repository, url: str) -> None:
    with suppress(KeyError):
        remote_repo.remotes.delete("origin")
    remote_repo.remotes.create("origin", url)


def _platform_config() -> PlatformStandardsConfig:
    return PlatformStandardsConfig(
        repo_url="git@github.com:example/platform-standards.git",
    )


def test_slug_with_owner_guard_skips_check_without_owner() -> None:
    """Slug guard leaves slug untouched when owner enforcement disabled."""
    slug = _slug_with_owner_guard("alpha/example", None, "repo")

    assert slug == "alpha/example"


def test_slug_with_owner_guard_respects_owner() -> None:
    """Slug guard enforces owner comparisons case-insensitively."""
    slug = _slug_with_owner_guard("Alpha/example", "alpha", "repo")

    assert slug == "Alpha/example"


def test_slug_with_owner_guard_requires_slug_when_owner_set() -> None:
    """Owner checks require a resolvable slug."""
    with pytest.raises(ConcordatError):
        _slug_with_owner_guard(None, "alpha", "repo")


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


def test_enrol_repositories_enforces_github_owner(git_repo: GitRepo) -> None:
    """Repositories outside the estate owner are rejected."""
    _set_origin(git_repo.repository, "git@github.com:alpha/example.git")

    with pytest.raises(ConcordatError) as caught:
        enrol_repositories([str(git_repo.path)], github_owner="bravo")

    assert "github_owner" in str(caught.value)


def test_enrol_repositories_require_slug_for_owner_guard(git_repo: GitRepo) -> None:
    """Owner enforcement requires a resolvable GitHub slug."""
    with suppress(KeyError):
        git_repo.repository.remotes.delete("origin")

    with pytest.raises(ConcordatError) as caught:
        enrol_repositories([str(git_repo.path)], github_owner="alpha")

    assert "GitHub slug" in str(caught.value)


def test_enrol_repositories_accept_matching_owner(git_repo: GitRepo) -> None:
    """Case-insensitive owner matches succeed."""
    _set_origin(git_repo.repository, "git@github.com:Example/demo.git")

    outcome = enrol_repositories([str(git_repo.path)], github_owner="example")[0]

    assert outcome.created is True


def test_platform_pr_result_skips_without_config() -> None:
    """Platform PR helper skips work when no config provided."""
    result = _platform_pr_result("example/repo", None)

    assert result is None


def test_platform_pr_result_requires_slug() -> None:
    """Platform PR helper fails fast when slug missing."""
    config = _platform_config()

    result = _platform_pr_result(None, config)

    assert result == PlatformStandardsResult(
        created=False,
        branch=None,
        pr_url=None,
        message="unable to determine GitHub slug",
    )


def test_platform_pr_result_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful ensure_repository_pr result is surfaced."""
    config = _platform_config()
    expected = PlatformStandardsResult(
        created=True,
        branch="feature/concordat",
        pr_url="https://github.com/example/platform/pull/1",
        message="opened",
    )

    def fake_ensure(
        repo_slug: str,
        *,
        config: PlatformStandardsConfig,
    ) -> PlatformStandardsResult:
        assert repo_slug == "example/repo"
        assert config is config_obj
        return expected

    config_obj = config
    monkeypatch.setattr("concordat.enrol.ensure_repository_pr", fake_ensure)

    result = _platform_pr_result("example/repo", config_obj)

    assert result is expected


def test_platform_pr_result_coerces_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Errors from ensure_repository_pr are converted to results."""
    config = _platform_config()

    def fake_ensure(*args: object, **kwargs: object) -> PlatformStandardsResult:
        raise ConcordatError("boom")

    monkeypatch.setattr("concordat.enrol.ensure_repository_pr", fake_ensure)

    result = _platform_pr_result("example/repo", config)

    assert result == PlatformStandardsResult(
        created=False,
        branch=None,
        pr_url=None,
        message="boom",
    )


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
