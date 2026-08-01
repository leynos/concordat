"""Unit tests for the Operation Parabellum sweep driver."""

from __future__ import annotations

import json
import typing as typ

import pytest

from scripts import parabellum_sweep as sweep

if typ.TYPE_CHECKING:
    import pathlib

ESTATE_YAML = """\
---
schema_version: 1
owner: leynos
repositories:
  - name: wireframe
  - name: gauss
    excluded: test-framework migration in flight
  - name: statelet
"""


@pytest.fixture
def estate_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write a small estate inventory and return its path."""
    path = tmp_path / "estate.yaml"
    path.write_text(ESTATE_YAML)
    return path


@pytest.fixture
def ledger_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a ledger path inside the test's temporary directory."""
    return tmp_path / "ledger.jsonl"


def _ledger_lines(path: pathlib.Path) -> list[dict[str, typ.Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _fake_result(verdict: str) -> sweep.RuleRunResult:
    return sweep.RuleRunResult(
        rule_package="rust-makefile-baseline",
        verdict=verdict,
        findings=(),
    )


class TestLoadEstate:
    """Parsing of the estate inventory."""

    def test_parses_names_and_exclusions(self, estate_path: pathlib.Path) -> None:
        """Names and exclusion reasons round-trip from YAML."""
        estate = sweep.load_estate(estate_path)
        assert estate.owner == "leynos", "estate owner should parse from the manifest"
        names = [entry.name for entry in estate.repositories]
        assert names == ["wireframe", "gauss", "statelet"], (
            "repository names should round-trip from YAML in order"
        )
        assert estate.repositories[1].excluded == (
            "test-framework migration in flight"
        ), "gauss should carry its exclusion reason"
        assert estate.repositories[0].excluded is None, (
            "wireframe should have no exclusion reason"
        )


class TestSweep:
    """Behaviour of the sweep loop with patched git and audit calls."""

    @pytest.fixture(autouse=True)
    def patch_externals(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace network and audit boundaries with deterministic fakes."""
        monkeypatch.setattr(sweep, "resolve_head", lambda owner, name: "a" * 40)
        monkeypatch.setattr(
            sweep,
            "clone_and_audit",
            lambda owner, name: ("a" * 40, _fake_result("compliant")),
        )

    def test_records_one_line_per_repository(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """Each audited repository appends one ledger record."""
        appended = sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)
        records = _ledger_lines(ledger_path)
        assert len(appended) == len(records) == 3, (
            "every repository should append exactly one ledger record"
        )
        audited = [r for r in records if r["verdict"] == "compliant"]
        assert {r["repository"] for r in audited} == {
            "leynos/wireframe",
            "leynos/statelet",
        }, "both auditable repositories should be recorded compliant"
        assert all(r["commit_sha"] == "a" * 40 for r in audited), (
            "each audit should record the resolved commit sha"
        )
        assert all(r["makeutil_rev"] == sweep.MAKEUTIL_REV for r in audited), (
            "each audit should record the pinned makeutil revision"
        )

    def test_excluded_repository_gets_reasoned_record(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """An excluded repository is recorded with its reason, not audited."""
        sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)
        records = _ledger_lines(ledger_path)
        excluded = [r for r in records if r["verdict"] == "excluded"]
        assert len(excluded) == 1, "exactly one repository (gauss) should be excluded"
        assert excluded[0]["repository"] == "leynos/gauss", (
            "the excluded record should be gauss"
        )
        assert excluded[0]["exclusion_reason"] == (
            "test-framework migration in flight"
        ), "the exclusion reason should be preserved in the ledger"

    def test_second_run_is_idempotent(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """A repository already ledgered at the same commit is skipped."""
        sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)
        appended = sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)
        assert appended == [], (
            "a repeated sweep at the same commit should append nothing"
        )
        assert len(_ledger_lines(ledger_path)) == 3, (
            "the ledger should still hold the original three records"
        )

    def test_force_reaudits_seen_commit(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """--force re-audits ledgered commits without duplicating exclusions."""
        sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            options=sweep.SweepOptions(force=True),
        )
        assert [r["repository"] for r in appended] == [
            "leynos/wireframe",
            "leynos/statelet",
        ], "--force should re-audit both auditable repositories"

    def test_only_filter_limits_scope(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """--only restricts the sweep to the named repositories."""
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            options=sweep.SweepOptions(only=frozenset({"statelet"})),
        )
        assert [r["repository"] for r in appended] == ["leynos/statelet"], (
            "--only should restrict the sweep to statelet"
        )

    def test_limit_bounds_audits(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """--limit bounds the number of audited repositories."""
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            options=sweep.SweepOptions(limit=1),
        )
        audited = [r for r in appended if r["verdict"] != "excluded"]
        assert len(audited) == 1, "--limit=1 should bound audits to one repository"

    def test_limit_records_intervening_exclusion_then_stops(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """A full budget still records a later exclusion before stopping."""
        # Estate order is wireframe (audited), gauss (excluded), statelet
        # (auditable). With limit=1 the wireframe audit spends the budget, the
        # gauss exclusion is still recorded, and statelet is never reached.
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            options=sweep.SweepOptions(limit=1),
        )
        repositories = [r["repository"] for r in appended]
        assert repositories == ["leynos/wireframe", "leynos/gauss"], (
            "the audit and the intervening exclusion should both be recorded"
        )
        verdicts = {r["repository"]: r["verdict"] for r in appended}
        assert verdicts["leynos/wireframe"] == "compliant", (
            "wireframe should be audited compliant"
        )
        assert verdicts["leynos/gauss"] == "excluded", (
            "gauss should be recorded as excluded"
        )
        assert "leynos/statelet" not in repositories, (
            "statelet should not be reached once the audit budget is spent"
        )

    def test_exclusion_does_not_consume_limit(
        self,
        tmp_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """An excluded entry ahead of an audit still leaves the slot free."""
        # gauss (excluded) precedes wireframe, so a limit of 1 must still audit
        # wireframe: the exclusion must not spend the single audit slot.
        estate_path = tmp_path / "excluded-first.yaml"
        estate_path.write_text(
            "---\n"
            "schema_version: 1\n"
            "owner: leynos\n"
            "repositories:\n"
            "  - name: gauss\n"
            "    excluded: test-framework migration in flight\n"
            "  - name: wireframe\n"
        )
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            options=sweep.SweepOptions(limit=1),
        )
        verdicts = [r["verdict"] for r in appended]
        assert verdicts.count("excluded") == 1, "gauss should be excluded exactly once"
        assert [r["repository"] for r in appended if r["verdict"] != "excluded"] == [
            "leynos/wireframe"
        ], "wireframe should still be audited despite the preceding exclusion"

    def test_head_resolution_failure_records_error(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A resolve_head failure is ledgered without cloning or auditing."""

        def raise_resolve(owner: str, name: str) -> typ.NoReturn:
            message = "gh api head resolution failed"
            raise sweep.OperationalRuleError(
                message,
                operation="resolve-git-head",
                tool="git",
                resource=f"{owner}/{name}",
            )

        def fail_if_audited(owner: str, name: str) -> typ.NoReturn:
            message = "clone_and_audit must not run on head failure"
            raise AssertionError(message)

        monkeypatch.setattr(sweep, "resolve_head", raise_resolve)
        monkeypatch.setattr(sweep, "clone_and_audit", fail_if_audited)
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            options=sweep.SweepOptions(only=frozenset({"wireframe"})),
        )
        assert len(appended) == 1, "only wireframe's error record should be appended"
        assert appended[0]["verdict"] == "error", (
            "a head-resolution failure should ledger an error verdict"
        )
        assert "head resolution failed" in appended[0]["error_detail"], (
            "the error detail should carry the resolve_head failure message"
        )

    def test_head_resolution_failure_consumes_audit_slot(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A head-resolution failure spends one audit slot under --limit."""

        def raise_resolve(owner: str, name: str) -> typ.NoReturn:
            message = "boom"
            raise sweep.OperationalRuleError(
                message,
                operation="resolve-git-head",
                tool="git",
                resource=f"{owner}/{name}",
            )

        monkeypatch.setattr(sweep, "resolve_head", raise_resolve)
        # Estate order is wireframe, gauss (excluded), statelet. With limit=1
        # the wireframe failure consumes the slot, so statelet is never reached.
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            options=sweep.SweepOptions(limit=1),
        )
        error_records = [r for r in appended if r["verdict"] == "error"]
        assert len(error_records) == 1, (
            "the wireframe head failure should ledger exactly one error record"
        )
        assert not any(r["repository"] == "leynos/statelet" for r in appended), (
            "statelet should not be reached after the failure consumes the slot"
        )

    def test_operational_error_records_error_verdict(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An audit failure is ledgered as an error record, not raised."""

        def raise_operational(owner: str, name: str) -> typ.NoReturn:
            message = "conftest is required"
            raise sweep.OperationalRuleError(
                message,
                operation="invoke-conftest",
                tool="conftest",
                resource=f"{owner}/{name}",
            )

        monkeypatch.setattr(sweep, "clone_and_audit", raise_operational)
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            options=sweep.SweepOptions(only=frozenset({"wireframe"})),
        )
        assert len(appended) == 1, "only wireframe should be recorded"
        assert appended[0]["verdict"] == "error", (
            "an audit failure should be ledgered with an error verdict"
        )
        assert "conftest" in appended[0]["error_detail"], (
            "the error detail should carry the clone/audit failure message"
        )


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

    def test_git_timeout_becomes_operational_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A git timeout is translated instead of aborting the sweep."""

        def raise_timeout(*args: object, **kwargs: object) -> typ.NoReturn:
            raise sweep.subprocess.TimeoutExpired(cmd="git", timeout=sweep.GIT_TIMEOUT)

        monkeypatch.setattr(sweep.subprocess, "run", raise_timeout)
        with pytest.raises(sweep.OperationalRuleError, match="timed out") as exc_info:
            sweep.resolve_head("leynos", "ghost")
        error = exc_info.value
        assert error.operation == "resolve-git-head", (
            "a git timeout should keep the resolve-git-head operation tag"
        )
        assert error.tool == "git", "a git timeout should identify git as the tool"
        assert error.resource == "leynos/ghost", (
            "a git timeout should identify the affected repository"
        )

    def test_missing_git_executable_becomes_operational_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A missing git binary is translated instead of aborting the sweep."""

        def raise_missing(*args: object, **kwargs: object) -> typ.NoReturn:
            raise FileNotFoundError("git")

        monkeypatch.setattr(sweep.subprocess, "run", raise_missing)
        with pytest.raises(
            sweep.OperationalRuleError, match="not found on PATH"
        ) as exc_info:
            sweep.resolve_head("leynos", "ghost")
        assert exc_info.value.tool == "git", (
            "a missing git binary should identify git as the tool"
        )


class TestReport:
    """Baseline report generation from the ledger."""

    def _record(
        self,
        repository: str,
        verdict: str,
        **extra: str,
    ) -> dict[str, typ.Any]:
        finding = {
            "rule_id": "QG-001",
            "severity": "error",
            "verdict": "noncompliant",
            "path": "Makefile",
            "line": 1,
            "message": "gate-critical variable uses ?=",
        }
        record: dict[str, typ.Any] = {
            "schema_version": 1,
            "repository": repository,
            "commit_sha": "b" * 40,
            "audited_at": "2026-07-19T16:00:00Z",
            "rule_package": "rust-makefile-baseline",
            "rule_version": "0.1.0",
            "makeutil_rev": sweep.MAKEUTIL_REV,
            "verdict": verdict,
            "findings": [finding] if verdict == "noncompliant" else [],
        }
        record.update(extra)
        return record

    def test_report_uses_latest_record_per_repository(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """The latest ledger record per repository wins."""
        records = [
            self._record("leynos/alpha", "noncompliant"),
            self._record(
                "leynos/alpha",
                "compliant",
                audited_at="2026-07-19T17:00:00Z",
            ),
            self._record("leynos/beta", "indeterminate"),
            self._record(
                "leynos/gamma",
                "excluded",
                exclusion_reason="not ready",
            ),
        ]
        ledger_path.write_text("".join(json.dumps(record) + "\n" for record in records))
        report = sweep.render_report(ledger_path)
        assert "| leynos/alpha | compliant |" in report, (
            "alpha's latest (compliant) record should win over its earlier one"
        )
        assert "| leynos/beta | indeterminate |" in report, (
            "beta should be reported as indeterminate"
        )
        assert "| leynos/gamma | excluded |" in report, (
            "gamma should be reported as excluded"
        )
        assert "compliant: 1" in report, (
            "the summary should count one compliant repository"
        )
        assert "indeterminate: 1" in report, (
            "the summary should count one indeterminate repository"
        )
        assert "excluded: 1" in report, (
            "the summary should count one excluded repository"
        )


class TestSweepCommand:
    """Option parsing for the default `parabellum-sweep` command.

    These drive the Cyclopts app directly so the flattened
    `SweepCommandOptions` parameter is exercised through real argument
    parsing rather than by calling `sweep_command` with a constructed object.
    """

    @staticmethod
    def _capture(
        monkeypatch: pytest.MonkeyPatch,
    ) -> list[dict[str, typ.Any]]:
        """Patch `run_sweep`, recording the arguments it is handed."""
        calls: list[dict[str, typ.Any]] = []

        def fake_run_sweep(
            *,
            estate_path: pathlib.Path,
            ledger_path: pathlib.Path,
            options: sweep.SweepOptions,
        ) -> list[dict[str, typ.Any]]:
            calls.append(
                {
                    "estate_path": estate_path,
                    "ledger_path": ledger_path,
                    "options": options,
                }
            )
            return []

        monkeypatch.setattr(sweep, "run_sweep", fake_run_sweep)
        return calls

    @staticmethod
    def _invoke(tokens: list[str]) -> int:
        """Run the app, normalising Cyclopts' `SystemExit` into a code."""
        try:
            sweep.app(tokens)
        except SystemExit as exc:
            return 0 if exc.code is None else int(exc.code)
        return 0

    def test_flags_reach_run_sweep(
        self,
        monkeypatch: pytest.MonkeyPatch,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """Every top-level flag is parsed and forwarded unchanged."""
        calls = self._capture(monkeypatch)

        exit_code = self._invoke(
            [
                "--only",
                "statelet",
                "--limit",
                "1",
                "--force",
                "--estate",
                str(estate_path),
                "--ledger",
                str(ledger_path),
            ]
        )

        assert exit_code == 0, exit_code
        assert len(calls) == 1, calls
        call = calls[0]
        assert call["estate_path"] == estate_path, call
        assert call["ledger_path"] == ledger_path, call
        assert call["options"] == sweep.SweepOptions(
            only=frozenset({"statelet"}),
            limit=1,
            force=True,
        ), call["options"]

    def test_only_list_is_split_and_cleaned(
        self,
        monkeypatch: pytest.MonkeyPatch,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """`--only` splits on commas, trimming blanks and collapsing repeats."""
        calls = self._capture(monkeypatch)

        self._invoke(
            [
                "--only",
                "alpha, beta, ,alpha",
                "--estate",
                str(estate_path),
                "--ledger",
                str(ledger_path),
            ]
        )

        assert calls[0]["options"].only == frozenset({"alpha", "beta"}), calls[0]

    def test_empty_only_selects_every_repository(
        self,
        monkeypatch: pytest.MonkeyPatch,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """An empty `--only` value filters nothing rather than everything."""
        calls = self._capture(monkeypatch)

        self._invoke(
            [
                "--only",
                "",
                "--estate",
                str(estate_path),
                "--ledger",
                str(ledger_path),
            ]
        )

        assert calls[0]["options"].only is None, calls[0]

    def test_defaults_apply_without_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With no flags the command still runs, using the packaged defaults."""
        calls = self._capture(monkeypatch)

        exit_code = self._invoke([])

        assert exit_code == 0, exit_code
        call = calls[0]
        assert call["estate_path"] == sweep.DEFAULT_ESTATE_PATH, call
        assert call["ledger_path"] == sweep.DEFAULT_LEDGER_PATH, call
        assert call["options"] == sweep.SweepOptions(), call["options"]

    def test_summary_line_names_the_ledger(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """The command reports how many records it appended, and where."""
        monkeypatch.setattr(
            sweep,
            "run_sweep",
            lambda **_kwargs: [{"repository": "leynos/statelet"}],
        )

        self._invoke(
            [
                "--estate",
                str(estate_path),
                "--ledger",
                str(ledger_path),
            ]
        )

        captured = capsys.readouterr().out
        assert f"appended 1 record(s) to {ledger_path}" in captured, captured

    def test_flags_stay_flat_and_top_level(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The options object is flattened onto the command, not nested.

        `SweepCommandOptions` carries `@Parameter(name="*")` precisely so the
        CLI keeps its original flags. Without it Cyclopts namespaces them as
        `--<prefix>.only` and every documented invocation breaks, so the flag
        surface is asserted rather than assumed.
        """
        self._invoke(["--help"])

        rendered = capsys.readouterr().out
        for name in ("only", "limit", "force", "estate", "ledger"):
            assert f"--{name}" in rendered, f"--{name} missing from:\n{rendered}"
            # A namespaced flag renders as `--<prefix>.<name>`; the default
            # paths in the help carry dots only before file extensions.
            assert f".{name}" not in rendered, (
                f"--{name} should not be namespaced:\n{rendered}"
            )
