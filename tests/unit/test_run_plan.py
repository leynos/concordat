"""Unit tests covering run_plan execution paths."""

from __future__ import annotations

import dataclasses
import io
import typing as typ

import pytest

from concordat.estate_execution import (
    ALL_BACKEND_ENV_VARS,
    AWS_SESSION_TOKEN_VAR,
    EstateExecutionError,
    ExecutionIO,
    ExecutionOptions,
    run_plan,
)
from tests.helpers.persistence import (
    PersistenceTestConfig,
    seed_invalid_persistence_manifest,
    seed_persistence_files,
)
from tests.unit.conftest import _make_record

if typ.TYPE_CHECKING:  # pragma: no cover - type checking only
    from tests.conftest import GitRepo


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


@dataclasses.dataclass
class SessionTokenForwardingTestCase:
    """Test case for session token forwarding behavior to tofu."""

    session_token_value: str
    expect_in_env: bool


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


@pytest.mark.parametrize(
    "test_case",
    [
        pytest.param(
            SessionTokenForwardingTestCase(
                session_token_value="sts-session-token",  # noqa: S106
                expect_in_env=True,
            ),
            id="forwards_token",
        ),
        pytest.param(
            SessionTokenForwardingTestCase(
                session_token_value="   ",  # noqa: S106
                expect_in_env=False,
            ),
            id="omits_blank",
        ),
    ],
)
def test_run_plan_session_token_forwarding(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
    fake_tofu: list[typ.Any],
    test_case: SessionTokenForwardingTestCase,
) -> None:
    """Session token propagation to tofu matches blank/valued inputs."""
    seed_persistence_files(git_repo.path)
    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    for variable in ALL_BACKEND_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SCW_ACCESS_KEY", "scw-access")
    monkeypatch.setenv("SCW_SECRET_KEY", "scw-secret")
    monkeypatch.setenv(AWS_SESSION_TOKEN_VAR, test_case.session_token_value)

    exit_code, _, tofu = _run_plan_test(git_repo, monkeypatch, fake_tofu)

    assert exit_code == 0
    if test_case.expect_in_env:
        assert tofu.env[AWS_SESSION_TOKEN_VAR] == test_case.session_token_value.strip()
    else:
        assert AWS_SESSION_TOKEN_VAR not in tofu.env


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
