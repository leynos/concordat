"""Unit tests for the Parabellum Git boundary and clone-destination safety."""

from __future__ import annotations

import typing as typ

import pytest

from scripts import parabellum_sweep as sweep


def _raise(error: Exception) -> typ.Callable[..., typ.NoReturn]:
    """Return a subprocess replacement that raises *error*."""

    def raise_error(*_args: object, **_kwargs: object) -> typ.NoReturn:
        raise error

    return raise_error


def _assert_git_error_context(error: sweep.OperationalRuleError) -> None:
    """Assert the context used by `resolve_head` git launch failures."""
    assert error.operation == "resolve-git-head", error.operation
    assert error.tool == "git", error.tool
    assert error.resource == "leynos/ghost", error.resource


class TestGitOperations:
    """Structured context on git-backed operational failures."""

    def test_resolve_head_git_failure_has_structured_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed git ls-remote surfaces operation/tool/resource context."""

        def fake_run(
            *args: object,
            **kwargs: object,
        ) -> sweep.subprocess.CompletedProcess[str]:
            return sweep.subprocess.CompletedProcess(
                args=["git"],
                returncode=128,
                stdout="",
                stderr="fatal: repository not found",
            )

        monkeypatch.setattr(sweep.subprocess, "run", fake_run)
        with pytest.raises(sweep.OperationalRuleError) as exc_info:
            sweep.resolve_head("leynos", "ghost")
        error = exc_info.value
        assert error.operation == "resolve-git-head", (
            "the git failure should tag the resolve-git-head operation"
        )
        assert error.tool == "git", "the failure should identify git as the tool"
        assert error.resource == "leynos/ghost", (
            "the failure should identify the affected repository"
        )

    @pytest.mark.parametrize(
        ("failure", "message"),
        [
            pytest.param(
                sweep.subprocess.TimeoutExpired(
                    cmd="git",
                    timeout=sweep.GIT_TIMEOUT,
                ),
                "timed out",
                id="timeout",
            ),
            pytest.param(
                FileNotFoundError("git"),
                "not found on PATH",
                id="missing-executable",
            ),
        ],
    )
    def test_git_process_failure_becomes_operational_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        failure: Exception,
        message: str,
    ) -> None:
        """A git process failure is translated instead of aborting the sweep.

        A hung remote and an absent binary both surface through `_git`, and
        both must carry the same context so the sweep can record which
        repository the lookup was for.
        """
        monkeypatch.setattr(sweep.subprocess, "run", _raise(failure))

        with pytest.raises(sweep.OperationalRuleError, match=message) as exc_info:
            sweep.resolve_head("leynos", "ghost")

        _assert_git_error_context(exc_info.value)


class TestCloneDestination:
    """`clone_and_audit` refuses identifiers it was handed directly."""

    @pytest.mark.parametrize(
        ("owner", "name"),
        [
            pytest.param("leynos", "../escape", id="name-traversal"),
            pytest.param("leynos", "nested/name", id="name-separator"),
            pytest.param("leynos", "..", id="name-dot-dot"),
            pytest.param("../evil", "wireframe", id="owner-traversal"),
        ],
    )
    def test_invalid_identifiers_never_reach_git(
        self,
        monkeypatch: pytest.MonkeyPatch,
        owner: str,
        name: str,
    ) -> None:
        """A crafted identifier is refused before any git process starts.

        `load_estate` already rejects these, so this guards the direct entry
        point: nothing may clone into a path outside the scratch root.
        """
        calls: list[object] = []
        monkeypatch.setattr(sweep, "_git", lambda *a, **k: calls.append((a, k)))

        with pytest.raises(sweep.OperationalRuleError):
            sweep.clone_and_audit(owner, name)

        assert calls == [], f"git should not run for {owner}/{name}, got {calls}"
