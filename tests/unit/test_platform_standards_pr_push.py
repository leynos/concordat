"""Tests for platform-standards PR helpers that need real git remotes."""

from __future__ import annotations

import pathlib
import typing as typ

import pygit2
import pytest

from concordat.platform_standards import PlatformStandardsConfig, ensure_repository_pr


def _commit_file(
    repository: pygit2.Repository,
    *,
    relative_path: str,
    contents: str,
    message: str,
) -> pygit2.Oid:
    repo_path = pathlib.Path(repository.workdir or ".")
    target = repo_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")

    index = repository.index
    index.add(relative_path)
    index.write()
    tree_oid = index.write_tree()

    signature = repository.default_signature
    parents: list[pygit2.Oid] = []
    if not repository.head_is_unborn:
        parents = [typ.cast("pygit2.Oid", repository.head.target)]

    return repository.create_commit(
        "HEAD",
        signature,
        signature,
        message,
        tree_oid,
        parents,
    )


def _push_branch(repository: pygit2.Repository, *, origin: str, branch: str) -> None:
    try:
        remote = repository.remotes["origin"]
    except KeyError:
        remote = repository.remotes.create("origin", origin)
    refspec = f"refs/heads/{branch}:refs/heads/{branch}"
    remote.push([refspec])


@pytest.fixture
def platform_origin(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """Create a local 'platform-standards' bare repo with a main branch."""
    origin_path = tmp_path / "platform-standards.git"
    pygit2.init_repository(str(origin_path), bare=True, initial_head="main")

    seed_path = tmp_path / "seed"
    seed_repo = pygit2.init_repository(str(seed_path), initial_head="main")
    seed_repo.config["user.name"] = "Test User"
    seed_repo.config["user.email"] = "test@example.com"

    inventory = "\n".join(
        [
            "schema_version: 1",
            "repositories: []",
            "",
        ]
    )
    _commit_file(
        seed_repo,
        relative_path="tofu/inventory/repositories.yaml",
        contents=inventory,
        message="seed inventory",
    )

    _push_branch(seed_repo, origin=str(origin_path), branch="main")
    return origin_path, str(origin_path)


def test_ensure_repository_pr_updates_existing_remote_branch_without_non_fast_forward(
    platform_origin: tuple[pathlib.Path, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Existing PR branches should be updated with a fast-forward push."""
    origin_path, origin_url = platform_origin
    branch_name = "concordat/enrol/test-owner-test-repo"

    # Create an existing remote branch with an extra commit so a naive push from
    # main would be rejected as non-fast-forward.
    work_path = tmp_path / "work"
    work_repo = pygit2.clone_repository(origin_url, str(work_path))
    work_repo.config["user.name"] = "Test User"
    work_repo.config["user.email"] = "test@example.com"

    base = work_repo.branches["main"].peel(pygit2.Commit)
    local_branch = work_repo.create_branch(branch_name, base)
    work_repo.checkout(local_branch)
    _commit_file(
        work_repo,
        relative_path="README.md",
        contents="existing branch\n",
        message="existing branch commit",
    )
    _push_branch(work_repo, origin=origin_url, branch=branch_name)

    # Avoid calling external tooling during the test.
    monkeypatch.setattr("concordat.platform_standards._run_cmd", lambda *a, **k: None)
    monkeypatch.setattr(
        "concordat.platform_standards._run_tflint", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "concordat.platform_standards._run_tofu_validate",
        lambda *a, **k: None,
    )

    # Avoid GitHub API calls while still exercising the push.
    monkeypatch.setattr(
        "concordat.platform_standards._github_slug_from_url",
        lambda _url: ("example", "platform-standards"),
    )

    class _FakePR:
        html_url = "https://example.com/pr/1"

    class _FakeRepo:
        def create_pull(self, *args: object, **kwargs: object) -> _FakePR:
            return _FakePR()

    class _FakeClient:
        def repository(self, owner: str, name: str) -> _FakeRepo:
            assert owner == "example"
            assert name == "platform-standards"
            return _FakeRepo()

    monkeypatch.setattr(
        "concordat.platform_standards.github3.login", lambda **_: _FakeClient()
    )

    result = ensure_repository_pr(
        "test-owner/test-repo",
        config=PlatformStandardsConfig(
            repo_url=origin_url,
            base_branch="main",
            inventory_path="tofu/inventory/repositories.yaml",
            github_token="fake-token",  # noqa: S106
        ),
    )

    assert result.created is True
    assert result.branch == branch_name
    assert result.pr_url == "https://example.com/pr/1"

    # Ensure the remote branch advanced (i.e., push succeeded).
    origin_repo = pygit2.Repository(str(origin_path))
    assert origin_repo.lookup_reference(f"refs/heads/{branch_name}")


def test_ensure_repository_pr_reports_existing_branch_pr_when_not_merged(
    platform_origin: tuple[pathlib.Path, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Surface the PR when the branch already contains the change.

    The base branch inventory may still be missing the entry until merged.
    """
    origin_path, origin_url = platform_origin
    branch_name = "concordat/enrol/test-owner-test-repo"

    work_path = tmp_path / "existing"
    work_repo = pygit2.clone_repository(origin_url, str(work_path))
    work_repo.config["user.name"] = "Test User"
    work_repo.config["user.email"] = "test@example.com"

    base = work_repo.branches["main"].peel(pygit2.Commit)
    local_branch = work_repo.create_branch(branch_name, base)
    work_repo.checkout(local_branch)

    inventory_with_repo = "\n".join(
        [
            "schema_version: 1",
            "repositories:",
            "  - name: test-owner/test-repo",
            "",
        ]
    )
    _commit_file(
        work_repo,
        relative_path="tofu/inventory/repositories.yaml",
        contents=inventory_with_repo,
        message="add inventory entry",
    )
    _push_branch(work_repo, origin=origin_url, branch=branch_name)

    monkeypatch.setattr(
        "concordat.platform_standards._github_slug_from_url",
        lambda _url: ("example", "platform-standards"),
    )

    class _FakePR:
        html_url = "https://example.com/pr/42"

    class _FakeRepo:
        def create_pull(self, *args: object, **kwargs: object) -> _FakePR:
            return _FakePR()

    class _FakeClient:
        def repository(self, owner: str, name: str) -> _FakeRepo:
            assert owner == "example"
            assert name == "platform-standards"
            return _FakeRepo()

    monkeypatch.setattr(
        "concordat.platform_standards.github3.login", lambda **_: _FakeClient()
    )

    result = ensure_repository_pr(
        "test-owner/test-repo",
        config=PlatformStandardsConfig(
            repo_url=origin_url,
            base_branch="main",
            inventory_path="tofu/inventory/repositories.yaml",
            github_token="fake-token",  # noqa: S106
        ),
    )

    assert result.created is True
    assert result.branch == branch_name
    assert result.pr_url == "https://example.com/pr/42"

    origin_repo = pygit2.Repository(str(origin_path))
    assert origin_repo.lookup_reference(f"refs/heads/{branch_name}")
