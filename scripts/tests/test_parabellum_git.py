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

        monkeypatch.setattr(sweep.bounded_subprocess, "run_bounded", fake_run)
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
        monkeypatch.setattr(
            sweep.bounded_subprocess,
            "run_bounded",
            _raise(failure),
        )

        with pytest.raises(sweep.OperationalRuleError, match=message) as exc_info:
            sweep.resolve_head("leynos", "ghost")

        _assert_git_error_context(exc_info.value)


class TestCloneAndAuditPackage:
    """`clone_and_audit` audits the package it was asked for."""

    @staticmethod
    def _fake_git(
        args: typ.Sequence[str],
        **kwargs: object,
    ) -> sweep.subprocess.CompletedProcess[str]:
        """Answer every git invocation with a plausible success."""
        del kwargs
        argv = list(args)
        # `_git` builds the full argv, so the subcommand follows the binary.
        if argv[:2] == ["git", "rev-parse"]:
            return sweep.subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout="a" * 40 + "\n",
                stderr="",
            )
        return sweep.subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="", stderr=""
        )

    def test_the_default_package_is_the_campaign_package(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two positional arguments keep auditing the baseline package."""
        audited: list[str] = []
        monkeypatch.setattr(sweep.bounded_subprocess, "run_bounded", self._fake_git)
        monkeypatch.setattr(
            sweep,
            "run_rule",
            lambda package, checkout: audited.append(package) or _result(package),
        )

        sweep.clone_and_audit("leynos", "wireframe")

        assert audited == [sweep.RULE_PACKAGE], audited

    def test_the_selected_package_reaches_run_rule(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A survey package is passed through to the rule runner unchanged."""
        seen: list[tuple[str, object]] = []
        monkeypatch.setattr(sweep.bounded_subprocess, "run_bounded", self._fake_git)

        def fake_run_rule(package: str, checkout: object) -> sweep.RuleRunResult:
            seen.append((package, checkout))
            return _result(package)

        monkeypatch.setattr(sweep, "run_rule", fake_run_rule)

        sha, result = sweep.clone_and_audit(
            "leynos", "wireframe", package="nested-cargo-offline-tests"
        )

        assert sha == "a" * 40, sha
        assert [package for package, _ in seen] == ["nested-cargo-offline-tests"], seen
        assert result.rule_package == "nested-cargo-offline-tests", result


def _result(package: str) -> sweep.RuleRunResult:
    """Return a clean result for *package*."""
    return sweep.RuleRunResult(rule_package=package, verdict="compliant", findings=())


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
