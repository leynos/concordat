"""Unit tests for estate execution helpers."""

from __future__ import annotations

import io
import shutil
import typing as typ
from types import SimpleNamespace

import pygit2
import pytest

import concordat.persistence.models as persistence_models
from concordat.estate import EstateRecord
from concordat.estate_execution import (
    EstateExecutionError,
    ExecutionIO,
    ExecutionOptions,
    cache_root,
    ensure_estate_cache,
    estate_workspace,
    run_plan,
)

if typ.TYPE_CHECKING:
    from pathlib import Path
else:  # pragma: no cover - runtime fallback
    Path = typ.Any


class GitRepo(typ.Protocol):
    """Structural type for the git_repo fixture."""

    path: Path
    repository: pygit2.Repository


def _make_record(repo_path: Path, alias: str = "core") -> EstateRecord:
    return EstateRecord(
        alias=alias,
        repo_url=str(repo_path),
        github_owner="example",
    )


def _seed_persistence_files(repo_path: Path, *, enabled: bool = True) -> None:
    backend_dir = repo_path / "backend"
    backend_dir.mkdir(parents=True, exist_ok=True)
    tfbackend = backend_dir / "core.tfbackend"
    tfbackend.write_text('bucket = "df12-tfstate"\n', encoding="utf-8")
    manifest = {
        "schema_version": persistence_models.PERSISTENCE_SCHEMA_VERSION,
        "enabled": enabled,
        "bucket": "df12-tfstate",
        "key_prefix": "estates/example/main",
        "key_suffix": "terraform.tfstate",
        "region": "fr-par",
        "endpoint": "https://s3.fr-par.scw.cloud",
        "backend_config_path": "backend/core.tfbackend",
    }
    with (backend_dir / "persistence.yaml").open("w", encoding="utf-8") as handle:
        persistence_models._yaml.dump(manifest, handle)


def test_cache_root_honours_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cache path is derived from XDG_CACHE_HOME when provided."""
    cache_home = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))

    root = cache_root()

    assert root == cache_home / "concordat" / "estates"
    assert root.exists()


def test_ensure_estate_cache_clones_repository(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    """Cloning a repository populates the estate cache."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"

    workdir = ensure_estate_cache(record, cache_directory=cache_dir)

    assert workdir == cache_dir / record.alias
    assert (workdir / ".git").exists()


def test_ensure_estate_cache_bare_destination(
    git_repo: GitRepo,
    tmp_path: Path,
) -> None:
    """Bare repositories at the cache destination raise an error."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"
    bare_path = cache_dir / record.alias
    pygit2.init_repository(str(bare_path), bare=True)

    with pytest.raises(EstateExecutionError, match="bare"):
        ensure_estate_cache(record, cache_directory=cache_dir)


def test_ensure_estate_cache_fetches_updates(git_repo: GitRepo, tmp_path: Path) -> None:
    """Refreshing the cache resets it to the remote HEAD."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"

    workdir = ensure_estate_cache(record, cache_directory=cache_dir)
    cached_repo = pygit2.Repository(str(workdir))
    initial_head = cached_repo.head.target

    (git_repo.path / "NEW.txt").write_text("update\n", encoding="utf-8")
    repo = pygit2.Repository(str(git_repo.path))
    index = repo.index
    index.add("NEW.txt")
    index.write()
    tree_oid = index.write_tree()
    sig = pygit2.Signature("Test User", "test@example.com")
    repo.create_commit(
        "refs/heads/main", sig, sig, "update", tree_oid, [repo.head.target]
    )

    ensure_estate_cache(record, cache_directory=cache_dir)

    cached_repo = pygit2.Repository(str(workdir))
    assert cached_repo.head.target != initial_head


def test_ensure_estate_cache_requires_origin(git_repo: GitRepo, tmp_path: Path) -> None:
    """Missing origin remote triggers an execution error."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"
    workdir = ensure_estate_cache(record, cache_directory=cache_dir)
    repo = pygit2.Repository(str(workdir))
    repo.remotes.delete("origin")

    with pytest.raises(EstateExecutionError, match="origin"):
        ensure_estate_cache(record, cache_directory=cache_dir)


def test_estate_workspace_cleans_up(git_repo: GitRepo, tmp_path: Path) -> None:
    """Workspaces are removed when keep_workdir is False."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"

    with estate_workspace(record, cache_directory=cache_dir) as workdir:
        workspace_path = workdir
        assert (workspace_path / ".git").exists()

    assert not workspace_path.exists()


def test_estate_workspace_preserves_directory_when_requested(
    git_repo: GitRepo,
    tmp_path: Path,
) -> None:
    """Workspaces remain on disk when keep_workdir=True."""
    record = _make_record(git_repo.path)
    cache_dir = tmp_path / "cache"

    with estate_workspace(
        record,
        cache_directory=cache_dir,
        keep_workdir=True,
    ) as workdir:
        workspace_path = workdir
        marker = workspace_path / "marker.txt"
        marker.write_text("marker\n", encoding="utf-8")

    assert workspace_path.exists()
    shutil.rmtree(workspace_path)


def test_run_plan_uses_persistence_backend_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """Plan passes backend config and maps SCW credentials to AWS env vars."""
    _seed_persistence_files(git_repo.path)
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setenv("SCW_ACCESS_KEY", "scw-access")
    monkeypatch.setenv("SCW_SECRET_KEY", "scw-secret")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    created: list[FakeTofu] = []

    class FakeTofu:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.cwd = cwd
            self.env = env
            self.calls: list[list[str]] = []
            created.append(self)

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            self.calls.append(args)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", FakeTofu)

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    options = ExecutionOptions(
        github_owner="example",
        github_token="token",  # noqa: S106
    )
    io_streams = ExecutionIO(stdout=stdout_buffer, stderr=stderr_buffer)

    exit_code, _ = run_plan(_make_record(git_repo.path), options, io_streams)

    assert exit_code == 0
    tofu = created[-1]
    assert tofu.env["AWS_ACCESS_KEY_ID"] == "scw-access"
    assert tofu.env["AWS_SECRET_ACCESS_KEY"] == "scw-secret"  # noqa: S105
    assert [
        "init",
        "-input=false",
        "-backend-config=backend/core.tfbackend",
    ] in tofu.calls
    assert "bucket=df12-tfstate" in stderr_buffer.getvalue()
    assert "estates/example/main/terraform.tfstate" in stderr_buffer.getvalue()
    assert "scw-secret" not in stderr_buffer.getvalue()


def test_run_plan_requires_backend_credentials(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """Plan aborts before init when backend credentials are missing."""
    _seed_persistence_files(git_repo.path)
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    for variable in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "SCW_ACCESS_KEY",
        "SCW_SECRET_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)

    def _fail_init(*args: object, **kwargs: object) -> object:
        pytest.fail("Tofu should not be initialised without credentials")

    monkeypatch.setattr("concordat.estate_execution.Tofu", _fail_init)
    options = ExecutionOptions(
        github_owner="example",
        github_token="token",  # noqa: S106
    )
    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())

    with pytest.raises(EstateExecutionError, match="AWS_ACCESS_KEY_ID"):
        run_plan(_make_record(git_repo.path), options, io_streams)


def test_run_plan_skips_disabled_persistence(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """Disabled persistence manifests fall back to local state handling."""
    _seed_persistence_files(git_repo.path, enabled=False)
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    created: list[FakeTofu] = []

    class FakeTofu:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.env = env
            self.calls: list[list[str]] = []
            created.append(self)

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            self.calls.append(args)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", FakeTofu)

    options = ExecutionOptions(
        github_owner="example",
        github_token="token",  # noqa: S106
    )
    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())

    exit_code, _ = run_plan(_make_record(git_repo.path), options, io_streams)

    assert exit_code == 0
    tofu = created[-1]
    assert ["init", "-input=false"] in tofu.calls
    assert all("-backend-config" not in call for call in tofu.calls), (
        "init should not receive backend config when disabled"
    )
