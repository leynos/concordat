"""Shared pytest fixtures for concordat tests."""

from __future__ import annotations

import collections
import collections.abc as cabc
import dataclasses
import pathlib
import subprocess

import pygit2
import pytest


@dataclasses.dataclass
class _Expectation:
    """Describe an anticipated subprocess invocation."""

    command: str
    args: tuple[str, ...] | None
    exit_code: int
    stdout: str
    stderr: str


class CmdMoxError(AssertionError):
    """Base class for harness-specific assertion failures."""


class UnexpectedCommandError(CmdMoxError):
    """Raised when a command executes without a matching expectation."""

    def __init__(self, args: tuple[str, ...]) -> None:
        """Capture the unexpected invocation for easier debugging."""
        human_args = " ".join(args) if args else "<empty>"
        super().__init__(f"Unexpected command invocation: {human_args}")


class CommandMismatchError(CmdMoxError):
    """Raised when the invoked binary differs from the expectation."""

    def __init__(self, expected: str, actual: str) -> None:
        """Record the expected and actual commands."""
        super().__init__(f"Expected {expected!r} but received {actual!r}")


class ArgumentMismatchError(CmdMoxError):
    """Raised when subprocess arguments differ from the configured call."""

    def __init__(
        self,
        command: str,
        expected: tuple[str, ...],
        actual: tuple[str, ...],
    ) -> None:
        """Retain the diff for inspection."""
        super().__init__(f"{command!r} expected args {expected} but received {actual}")


class PendingExpectationError(CmdMoxError):
    """Raised when tests finish without consuming every expectation."""

    def __init__(self, expectations: list[_Expectation]) -> None:
        """Surface the unconsumed expectations in the assertion message."""
        super().__init__(f"Unconsumed expectations remain: {expectations}")


class CmdMox:
    """Record subprocess expectations and replay them via monkeypatch."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Initialise the harness and capture the monkeypatch helper."""
        self._monkeypatch = monkeypatch
        self._expectations: collections.deque[_Expectation] = collections.deque()
        self._active = False

    def mock(self, command: str) -> CmdMoxBuilder:
        """Begin configuring expectations for the provided command."""
        return CmdMoxBuilder(self, command)

    def enqueue(self, expectation: _Expectation) -> None:
        """Append a new expectation to the replay queue."""
        self._expectations.append(expectation)

    def replay(self) -> None:
        """Patch subprocess.run with the harness once per test."""
        if self._active:
            return
        self._active = True
        self._monkeypatch.setattr(subprocess, "run", self._run)

    def _run(
        self,
        args: cabc.Iterable[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[list[str]]:
        """Simulate subprocess.run while asserting the recorded calls."""
        if not self._expectations:
            raise UnexpectedCommandError(tuple(args))
        expectation = self._expectations.popleft()
        args_list = list(args)
        invoked_command = pathlib.Path(args_list[0]).name
        invoked_args = tuple(args_list[1:])

        if invoked_command != expectation.command:
            raise CommandMismatchError(expectation.command, invoked_command)
        if expectation.args is not None and invoked_args != expectation.args:
            raise ArgumentMismatchError(
                expectation.command,
                expectation.args,
                invoked_args,
            )

        if kwargs.get("check") and expectation.exit_code != 0:
            raise subprocess.CalledProcessError(
                expectation.exit_code,
                args_list,
                output=expectation.stdout,
                stderr=expectation.stderr,
            )
        return subprocess.CompletedProcess(args_list, expectation.exit_code)

    def verify(self) -> None:
        """Ensure every expectation was consumed by the test."""
        if self._expectations:
            raise PendingExpectationError(list(self._expectations))


class CmdMoxBuilder:
    """Fluent helper for programming CmdMox expectations."""

    def __init__(self, harness: CmdMox, command: str) -> None:
        """Store the harness and command for subsequent configuration."""
        self._harness = harness
        self._command = command
        self._args: tuple[str, ...] | None = None

    def with_args(self, *args: str) -> CmdMoxBuilder:
        """Record the arguments that must accompany the command."""
        self._args = args
        return self

    def returns(self, exit_code: int = 0, stdout: str = "", stderr: str = "") -> CmdMox:
        """Register the mocked process result and return the harness."""
        self._harness.enqueue(
            _Expectation(
                self._command,
                self._args,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
        )
        return self._harness


@pytest.fixture
def cmd_mox(monkeypatch: pytest.MonkeyPatch) -> CmdMox:
    """Expose the command mocking harness to tests."""
    return CmdMox(monkeypatch)


@dataclasses.dataclass(slots=True)
class GitRepo:
    """Expose repository handle and path for tests."""

    repository: pygit2.Repository
    path: pathlib.Path

    def read_text(self, relative_path: str) -> str:
        """Read a file relative to the repository root."""
        return (self.path / relative_path).read_text(encoding="utf-8")


@pytest.fixture
def git_repo(tmp_path: pathlib.Path) -> GitRepo:
    """Initialise a git repository with an initial commit for testing."""
    repo_path = pathlib.Path(tmp_path, "repo")
    repo_path.mkdir()
    repository = pygit2.init_repository(str(repo_path), initial_head="main")

    config = repository.config
    config["user.name"] = "Test User"
    config["user.email"] = "test@example.com"

    seed_file = repo_path / "README.md"
    seed_file.write_text("seed\n", encoding="utf-8")

    index = repository.index
    index.add("README.md")
    index.write()
    tree_oid = index.write_tree()

    signature = pygit2.Signature("Test User", "test@example.com")
    repository.create_commit(
        "refs/heads/main",
        signature,
        signature,
        "initial commit",
        tree_oid,
        [],
    )

    repository.set_head("refs/heads/main")

    return GitRepo(repository=repository, path=repo_path)
