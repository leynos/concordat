"""Unit tests for estate execution helpers."""

from __future__ import annotations

import dataclasses
import io
import os
import shutil
import typing as typ
from types import SimpleNamespace

import pygit2
import pytest

from concordat.estate import EstateRecord
from concordat.estate_execution import (
    ALL_BACKEND_ENV_VARS,
    EstateExecutionError,
    ExecutionIO,
    ExecutionOptions,
    _resolve_backend_environment,
    cache_root,
    ensure_estate_cache,
    estate_workspace,
    run_plan,
)
from tests.helpers.persistence import (
    PersistenceTestConfig,
    seed_invalid_persistence_manifest,
    seed_persistence_files,
)

if typ.TYPE_CHECKING:
    from pathlib import Path
else:  # pragma: no cover - runtime fallback
    Path = typ.Any


class GitRepo(typ.Protocol):
    """Structural type for the git_repo fixture."""

    path: Path
    repository: pygit2.Repository


@dataclasses.dataclass
class BackendConfigTestCase:
    """Test case for backend config validation scenarios."""

    backend_config_path: str
    create_backend_file: bool
    expected_error_fragments: list[str]


@dataclasses.dataclass
class BackendEnvTestCase:
    """Test case for backend environment sourcing."""

    env_setup: dict[str, str]
    options_environment: dict[str, str] | None
    expected_access: str
    expected_secret: str


@pytest.fixture
def fake_tofu(monkeypatch: pytest.MonkeyPatch) -> list[typ.Any]:
    """Provide a reusable FakeTofu stub and capture created instances."""
    created: list[typ.Any] = []

    class _FakeTofu:
        def __init__(self, cwd: str, env: dict[str, str]) -> None:
            self.cwd = cwd
            self.env = env
            self.calls: list[list[str]] = []
            created.append(self)

        def _run(self, args: list[str], *, raise_on_error: bool = False) -> object:
            self.calls.append(args)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("concordat.estate_execution.Tofu", _FakeTofu)
    return created


def _make_record(repo_path: Path, alias: str = "core") -> EstateRecord:
    return EstateRecord(
        alias=alias,
        repo_url=str(repo_path),
        github_owner="example",
    )


def _run_plan_test(
    git_repo: GitRepo,
    monkeypatch: pytest.MonkeyPatch,
    fake_tofu: list[typ.Any],
    *,
    options_environment: dict[str, str] | None = None,
) -> tuple[int, ExecutionIO, typ.Any]:
    """Execute run_plan with common test setup and return results."""
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )

    options = ExecutionOptions(
        github_owner="example",
        github_token="token",  # noqa: S106
        environment=options_environment,
    )
    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())

    exit_code, _ = run_plan(_make_record(git_repo.path), options, io_streams)
    return exit_code, io_streams, fake_tofu[-1]


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
    fake_tofu: list[typ.Any],
) -> None:
    """Plan passes backend config and maps SCW credentials to AWS env vars."""
    seed_persistence_files(git_repo.path)
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setenv("SCW_ACCESS_KEY", "scw-access")
    monkeypatch.setenv("SCW_SECRET_KEY", "scw-secret")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    options = ExecutionOptions(
        github_owner="example",
        github_token="token",  # noqa: S106
    )
    io_streams = ExecutionIO(stdout=stdout_buffer, stderr=stderr_buffer)

    exit_code, _ = run_plan(_make_record(git_repo.path), options, io_streams)

    assert exit_code == 0
    tofu = fake_tofu[-1]
    assert tofu.env["AWS_ACCESS_KEY_ID"] == "scw-access"
    assert tofu.env["AWS_SECRET_ACCESS_KEY"] == "scw-secret"  # noqa: S105
    assert [
        "init",
        "-input=false",
        "-backend-config=backend/core.tfbackend",
    ] in tofu.calls
    stderr_output = stderr_buffer.getvalue()
    assert "bucket=df12-tfstate" in stderr_output
    assert "estates/example/main/terraform.tfstate" in stderr_output
    assert "scw-secret" not in stderr_output
    assert "SCW_SECRET_KEY" not in stderr_output
    assert "SCW_ACCESS_KEY" not in stderr_output
    assert "scw-access" not in stderr_output


@pytest.mark.parametrize(
    "test_case",
    [
        pytest.param(
            BackendEnvTestCase(
                env_setup={
                    "SPACES_ACCESS_KEY_ID": "spaces-access",
                    "SPACES_SECRET_ACCESS_KEY": "spaces-secret",
                },
                options_environment=None,
                expected_access="spaces-access",
                expected_secret="spaces-secret",  # noqa: S106
            ),
            id="spaces-env",
        ),
        pytest.param(
            BackendEnvTestCase(
                env_setup={},
                options_environment={
                    "SCW_ACCESS_KEY": "options-access",
                    "SCW_SECRET_KEY": "options-secret",
                },
                expected_access="options-access",
                expected_secret="options-secret",  # noqa: S106
            ),
            id="options-mapping",
        ),
    ],
)
def test_run_plan_backend_env_sources(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
    fake_tofu: list[typ.Any],
    test_case: BackendEnvTestCase,
) -> None:
    """run_plan maps backend credentials from env or options environment."""
    seed_persistence_files(git_repo.path)
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    for variable in ALL_BACKEND_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)

    for key, value in test_case.env_setup.items():
        monkeypatch.setenv(key, value)

    exit_code, _, tofu = _run_plan_test(
        git_repo,
        monkeypatch,
        fake_tofu,
        options_environment=test_case.options_environment,
    )

    assert exit_code == 0
    assert tofu.env["AWS_ACCESS_KEY_ID"] == test_case.expected_access
    assert tofu.env["AWS_SECRET_ACCESS_KEY"] == test_case.expected_secret
    assert [
        "init",
        "-input=false",
        "-backend-config=backend/core.tfbackend",
    ] in tofu.calls


@pytest.mark.parametrize(
    ("env_setup", "expected_overrides", "test_id"),
    [
        pytest.param(
            {
                "set": {
                    "AWS_ACCESS_KEY_ID": "aws-access",
                    "AWS_SECRET_ACCESS_KEY": "aws-secret",
                    "SCW_ACCESS_KEY": "scw-access",
                    "SCW_SECRET_KEY": "scw-secret",
                    "SPACES_ACCESS_KEY_ID": "spaces-access",
                    "SPACES_SECRET_ACCESS_KEY": "spaces-secret",
                },
                "delete": [],
            },
            {},
            "prefers_aws",
        ),
        pytest.param(
            {
                "set": {
                    "SCW_ACCESS_KEY": "scw-access",
                    "SCW_SECRET_KEY": "scw-secret",
                    "SPACES_ACCESS_KEY_ID": "spaces-access",
                    "SPACES_SECRET_ACCESS_KEY": "spaces-secret",
                },
                "delete": [
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                ],
            },
            {
                "AWS_ACCESS_KEY_ID": "scw-access",
                "AWS_SECRET_ACCESS_KEY": "scw-secret",
            },
            "scw_over_spaces",
        ),
    ],
)
def test_resolve_backend_environment_precedence(
    monkeypatch: pytest.MonkeyPatch,
    env_setup: dict[str, dict[str, str] | list[str]],
    expected_overrides: dict[str, str],
    test_id: str,
) -> None:
    """Backend environment resolution follows AWS > SCW > SPACES precedence."""
    set_values = typ.cast("dict[str, str]", env_setup["set"])
    for key, value in set_values.items():
        monkeypatch.setenv(key, value)
    delete_keys = typ.cast("list[str]", env_setup["delete"])
    for key in delete_keys:
        monkeypatch.delenv(key, raising=False)

    resolved = _resolve_backend_environment(os.environ)

    assert resolved == expected_overrides


def test_resolve_backend_environment_ignores_blank_scw_and_uses_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank SCW_* values fall back to SPACES_* credentials."""
    for variable in ALL_BACKEND_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)

    monkeypatch.setenv("SCW_ACCESS_KEY", "   ")
    monkeypatch.setenv("SCW_SECRET_KEY", "")
    monkeypatch.setenv("SPACES_ACCESS_KEY_ID", "spaces-access")
    monkeypatch.setenv("SPACES_SECRET_ACCESS_KEY", "spaces-secret")

    resolved = _resolve_backend_environment(os.environ)

    assert resolved == {
        "AWS_ACCESS_KEY_ID": "spaces-access",
        "AWS_SECRET_ACCESS_KEY": "spaces-secret",
    }


def test_resolve_backend_environment_raises_when_all_aliases_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace aliases without AWS_* raise an execution error."""
    for variable in ALL_BACKEND_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)

    monkeypatch.setenv("SCW_ACCESS_KEY", "   ")
    monkeypatch.setenv("SCW_SECRET_KEY", "   ")
    monkeypatch.setenv("SPACES_ACCESS_KEY_ID", "")
    monkeypatch.setenv("SPACES_SECRET_ACCESS_KEY", "  ")

    with pytest.raises(EstateExecutionError):
        _resolve_backend_environment(os.environ)


def test_run_plan_requires_backend_credentials(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """Plan aborts before init when backend credentials are missing."""
    seed_persistence_files(git_repo.path)
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    for variable in ALL_BACKEND_ENV_VARS:
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


@pytest.mark.parametrize(
    "test_case",
    [
        pytest.param(
            BackendConfigTestCase(
                backend_config_path="backend/missing.tfbackend",
                create_backend_file=False,
                expected_error_fragments=[
                    "Remote backend config",
                    "backend/missing.tfbackend",
                ],
            ),
            id="missing_config_file",
        ),
        pytest.param(
            BackendConfigTestCase(
                backend_config_path="../outside.tfbackend",
                create_backend_file=False,
                expected_error_fragments=[
                    "Remote backend config must live inside the estate workspace",
                    "../outside.tfbackend",
                ],
            ),
            id="config_outside_workspace",
        ),
    ],
)
def test_run_plan_backend_config_validation(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
    test_case: BackendConfigTestCase,
) -> None:
    """Backend config validation aborts before tofu initialises."""
    seed_persistence_files(
        git_repo.path,
        PersistenceTestConfig(
            backend_config_path=test_case.backend_config_path,
            create_backend_file=test_case.create_backend_file,
        ),
    )
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setenv("SCW_ACCESS_KEY", "scw-access")
    monkeypatch.setenv("SCW_SECRET_KEY", "scw-secret")

    def _fail_init(*args: object, **kwargs: object) -> object:
        pytest.fail("Tofu must not be initialised when backend config is invalid")

    monkeypatch.setattr("concordat.estate_execution.Tofu", _fail_init)
    options = ExecutionOptions(
        github_owner="example",
        github_token="token",  # noqa: S106
    )
    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())

    with pytest.raises(EstateExecutionError) as excinfo:
        run_plan(_make_record(git_repo.path), options, io_streams)

    message = str(excinfo.value)
    for fragment in test_case.expected_error_fragments:
        assert fragment in message


def test_run_plan_skips_disabled_persistence(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
    fake_tofu: list[typ.Any],
) -> None:
    """Disabled persistence manifests fall back to local state handling."""
    seed_persistence_files(git_repo.path, PersistenceTestConfig(enabled=False))
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    exit_code, _, tofu = _run_plan_test(git_repo, monkeypatch, fake_tofu)

    assert exit_code == 0
    assert ["init", "-input=false"] in tofu.calls
    assert all("-backend-config" not in call for call in tofu.calls), (
        "init should not receive backend config when disabled"
    )


def test_run_plan_uses_local_state_when_persistence_manifest_missing(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
    fake_tofu: list[typ.Any],
) -> None:
    """Missing persistence manifest falls back to local state."""
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    exit_code, _, tofu = _run_plan_test(git_repo, monkeypatch, fake_tofu)

    assert exit_code == 0
    init_calls = [call for call in tofu.calls if call and call[0] == "init"]
    assert init_calls, "expected init to be invoked"
    assert all("-backend-config" not in call for call in init_calls)


def test_run_plan_respects_options_environment_mapping(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
    fake_tofu: list[typ.Any],
) -> None:
    """ExecutionOptions.environment is used as the env source for tofu."""
    seed_persistence_files(git_repo.path)
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    for variable in ALL_BACKEND_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)

    env_mapping = {
        "SCW_ACCESS_KEY": "options-access",
        "SCW_SECRET_KEY": "options-secret",
    }
    exit_code, _, tofu = _run_plan_test(
        git_repo,
        monkeypatch,
        fake_tofu,
        options_environment=env_mapping,
    )

    assert exit_code == 0
    assert tofu.env["AWS_ACCESS_KEY_ID"] == "options-access"
    assert tofu.env["AWS_SECRET_ACCESS_KEY"] == "options-secret"  # noqa: S105
    assert [
        "init",
        "-input=false",
        "-backend-config=backend/core.tfbackend",
    ] in tofu.calls


def test_run_plan_rejects_invalid_persistence_manifest(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
    fake_tofu: list[typ.Any],
) -> None:
    """Invalid persistence manifest surfaces as an execution error."""
    seed_invalid_persistence_manifest(git_repo.path)
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )

    with pytest.raises(EstateExecutionError):
        _run_plan_test(git_repo, monkeypatch, fake_tofu)
