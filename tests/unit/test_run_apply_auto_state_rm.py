"""Unit tests covering automatic state removal during run_apply."""

from __future__ import annotations

import dataclasses
import io
import typing as typ
from types import SimpleNamespace

if typ.TYPE_CHECKING:  # pragma: no cover
    import pytest

    from tests.conftest import GitRepo

from concordat.estate_execution import ExecutionIO, ExecutionOptions, run_apply
from tests.unit.conftest import _make_record


@dataclasses.dataclass(slots=True)
class _TofuMockResponses:
    """Configuration for Tofu mock responses."""

    apply_responses: list[SimpleNamespace]
    state_list_response: SimpleNamespace | None
    state_rm_response: SimpleNamespace | None


class _BaseTofuMock:
    """Base mock class for Tofu with configurable responses."""

    responses: _TofuMockResponses
    calls: list[list[str]]

    def __init__(self, cwd: str, env: dict[str, str]) -> None:
        self.cwd = cwd
        self.env = env
        self._apply_count = 0

    def _handle_apply(self) -> SimpleNamespace:
        """Handle apply command with sequential response tracking."""
        if self._apply_count < len(self.responses.apply_responses):
            response = self.responses.apply_responses[self._apply_count]
            self._apply_count += 1
            return response
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    def _handle_state_list(self) -> SimpleNamespace:
        """Handle state list command."""
        if self.responses.state_list_response is not None:
            return self.responses.state_list_response
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    def _handle_state_rm(self) -> SimpleNamespace:
        """Handle state rm command."""
        if self.responses.state_rm_response is not None:
            return self.responses.state_rm_response
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    def _is_state_list(self, args: list[str]) -> bool:
        """Check if args represent a 'state list' command."""
        return len(args) >= 2 and args[0] == "state" and args[1] == "list"

    def _is_state_rm(self, args: list[str]) -> bool:
        """Check if args represent a 'state rm' command."""
        return len(args) >= 2 and args[0] == "state" and args[1] == "rm"

    def _run(
        self,
        args: list[str],
        *,
        raise_on_error: bool = False,
    ) -> SimpleNamespace:
        self.calls.append(list(args))
        verb = args[0] if args else ""

        if verb == "apply":
            return self._handle_apply()

        if self._is_state_list(args):
            return self._handle_state_list()

        if self._is_state_rm(args):
            return self._handle_state_rm()

        return SimpleNamespace(stdout="", stderr="", returncode=0)


class TofuMockBuilder:
    """Builder for creating Tofu mock classes with configurable behavior."""

    def __init__(self) -> None:
        """Initialize an empty builder."""
        self._apply_responses: list[SimpleNamespace] = []
        self._state_list_response: SimpleNamespace | None = None
        self._state_rm_response: SimpleNamespace | None = None

    def _make_response(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
    ) -> SimpleNamespace:
        """Create a SimpleNamespace response object."""
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    def with_apply_response(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> TofuMockBuilder:
        """Add an apply response (called in sequence for multiple applies)."""
        self._apply_responses.append(self._make_response(stdout, stderr, returncode))
        return self

    def with_state_list_response(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> TofuMockBuilder:
        """Set the state list response."""
        self._state_list_response = self._make_response(stdout, stderr, returncode)
        return self

    def with_state_rm_response(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> TofuMockBuilder:
        """Set the state rm response."""
        self._state_rm_response = self._make_response(stdout, stderr, returncode)
        return self

    def build(self, calls: list[list[str]]) -> type:
        """Build the Tofu mock class."""
        responses = _TofuMockResponses(
            apply_responses=list(self._apply_responses),
            state_list_response=self._state_list_response,
            state_rm_response=self._state_rm_response,
        )
        return type(
            "_MockTofu",
            (_BaseTofuMock,),
            {"responses": responses, "calls": calls},
        )


def _setup_test_environment(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
    *,
    can_prompt: bool,
    stdin_input: str,
) -> tuple[list[list[str]], TofuMockBuilder, ExecutionIO, ExecutionOptions]:
    """Set up common test environment for run_apply tests.

    Returns:
        Tuple of (calls list, mock_builder, io_streams, options).

    """
    tofu_root = git_repo.path / "tofu"
    tofu_root.mkdir()
    (tofu_root / "main.tofu").write_text("terraform {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "concordat.estate_execution.ensure_estate_cache",
        lambda *_, **__: git_repo.path,
    )
    monkeypatch.setattr(
        "concordat.estate_execution._can_prompt",
        lambda: can_prompt,
    )
    monkeypatch.setattr(
        "concordat.user_interaction.sys.stdin", io.StringIO(stdin_input)
    )

    calls: list[list[str]] = []
    mock_builder = TofuMockBuilder()

    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=io.StringIO())
    options = ExecutionOptions(
        github_owner="leynos",
        github_token="token",  # noqa: S106
        extra_args=("-auto-approve",),
    )

    return calls, mock_builder, io_streams, options


# Common error message used across tests
_PREVENT_DESTROY_ERROR = (
    "Error: Instance cannot be destroyed\n"
    'Resource module.repository[\\"leynos/test-repo\\"].'
    "github_repository.this has lifecycle.prevent_destroy set\n"
)

_STATE_LIST_OUTPUT = 'module.repository["leynos/test-repo"].github_repository.this\n'


def test_run_apply_offers_to_forget_resources_on_prevent_destroy(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """When prevent_destroy blocks deletes, concordat offers `tofu state rm`."""
    calls, builder, io_streams, options = _setup_test_environment(
        monkeypatch, git_repo, can_prompt=True, stdin_input="y\n"
    )

    tofu_mock = (
        builder.with_apply_response(stderr=_PREVENT_DESTROY_ERROR, returncode=1)
        .with_apply_response(returncode=0)
        .with_state_list_response(stdout=_STATE_LIST_OUTPUT)
        .with_state_rm_response(returncode=0)
        .build(calls)
    )

    monkeypatch.setattr("concordat.estate_execution.Tofu", tofu_mock)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", tofu_mock)

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code == 0
    assert ["state", "list"] in calls
    assert [
        "state",
        "rm",
        'module.repository["leynos/test-repo"].github_repository.this',
    ] in calls
    assert calls.count(["apply", "-auto-approve"]) == 2


def test_run_apply_prevent_destroy_non_interactive_no_state_rm(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """Non-interactive runs should not invoke state list/rm, but show suggestion."""
    calls, builder, _, options = _setup_test_environment(
        monkeypatch, git_repo, can_prompt=False, stdin_input=""
    )

    tofu_mock = builder.with_apply_response(
        stderr=_PREVENT_DESTROY_ERROR, returncode=1
    ).build(calls)

    monkeypatch.setattr("concordat.estate_execution.Tofu", tofu_mock)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", tofu_mock)

    stderr_buffer = io.StringIO()
    io_streams = ExecutionIO(stdout=io.StringIO(), stderr=stderr_buffer)
    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert not any(call[0] == "state" for call in calls)
    assert "tofu state rm" in stderr_buffer.getvalue()


def test_run_apply_prevent_destroy_user_answers_no(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """User declining state removal preserves failure and skips state commands."""
    calls, builder, io_streams, options = _setup_test_environment(
        monkeypatch, git_repo, can_prompt=True, stdin_input="n\n"
    )

    tofu_mock = builder.with_apply_response(
        stderr=_PREVENT_DESTROY_ERROR, returncode=1
    ).build(calls)

    monkeypatch.setattr("concordat.estate_execution.Tofu", tofu_mock)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", tofu_mock)

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert not any(call[0] == "state" for call in calls)
    assert calls.count(["apply", "-auto-approve"]) == 1


def test_run_apply_prevent_destroy_state_list_no_matches(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """When state list finds no matching addresses, no state rm and no retry."""
    calls, builder, io_streams, options = _setup_test_environment(
        monkeypatch, git_repo, can_prompt=True, stdin_input="y\n"
    )

    tofu_mock = (
        builder.with_apply_response(stderr=_PREVENT_DESTROY_ERROR, returncode=1)
        .with_state_list_response(stdout="")  # Empty state list
        .build(calls)
    )

    monkeypatch.setattr("concordat.estate_execution.Tofu", tofu_mock)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", tofu_mock)

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert ["state", "list"] in calls
    assert calls.count(["apply", "-auto-approve"]) == 1


def test_run_apply_prevent_destroy_state_rm_failure(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """When state rm fails, the overall exit code should be non-zero."""
    calls, builder, io_streams, options = _setup_test_environment(
        monkeypatch, git_repo, can_prompt=True, stdin_input="y\n"
    )

    tofu_mock = (
        builder.with_apply_response(stderr=_PREVENT_DESTROY_ERROR, returncode=1)
        .with_state_list_response(stdout=_STATE_LIST_OUTPUT)
        .with_state_rm_response(
            stderr="Error: failed to remove state entry", returncode=1
        )
        .build(calls)
    )

    monkeypatch.setattr("concordat.estate_execution.Tofu", tofu_mock)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", tofu_mock)

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert ["state", "list"] in calls
    assert any(call[0] == "state" and call[1] == "rm" for call in calls)


def test_run_apply_state_list_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: GitRepo,
) -> None:
    """When state list itself fails, no state rm should be attempted."""
    calls, builder, io_streams, options = _setup_test_environment(
        monkeypatch, git_repo, can_prompt=True, stdin_input="y\n"
    )

    tofu_mock = (
        builder.with_apply_response(stderr=_PREVENT_DESTROY_ERROR, returncode=1)
        .with_state_list_response(stderr="Error: failed to list state", returncode=1)
        .build(calls)
    )

    monkeypatch.setattr("concordat.estate_execution.Tofu", tofu_mock)
    monkeypatch.setattr("concordat.tofu_runner.Tofu", tofu_mock)

    exit_code, _ = run_apply(_make_record(git_repo.path), options, io_streams)

    assert exit_code != 0
    assert ["state", "list"] in calls
    assert not any(
        call[0] == "state" and len(call) > 1 and call[1] == "rm" for call in calls
    )
