"""Unit tests for the Parabellum sweep command-line surface."""

from __future__ import annotations

import io
import typing as typ
from contextlib import redirect_stderr

import pytest

from scripts import parabellum_sweep as sweep

if typ.TYPE_CHECKING:
    import pathlib


class _SweepCall(typ.TypedDict):
    """One recorded `run_sweep` invocation.

    Named so the captured arguments carry the same types the real `run_sweep`
    declares; a `dict[str, typ.Any]` let an assertion compare a path against
    an option object without the type checker objecting.
    """

    estate_path: pathlib.Path
    ledger_path: pathlib.Path
    options: sweep.SweepOptions


class TestSweepCommand:
    """Option parsing for the default `parabellum-sweep` command.

    These drive the Cyclopts app directly so the flattened
    `SweepCommandOptions` parameter is exercised through real argument
    parsing rather than by calling `sweep_command` with a constructed object.
    """

    @staticmethod
    def _capture(
        monkeypatch: pytest.MonkeyPatch,
    ) -> list[_SweepCall]:
        """Patch `run_sweep`, recording the arguments it is handed."""
        calls: list[_SweepCall] = []

        # The stub stands in for `run_sweep`, so it returns a ledger — not one
        # of the captured calls above. The two are deliberately distinct types.
        def fake_run_sweep(
            *,
            estate_path: pathlib.Path,
            ledger_path: pathlib.Path,
            options: sweep.SweepOptions,
        ) -> sweep.Ledger:
            calls.append({
                "estate_path": estate_path,
                "ledger_path": ledger_path,
                "options": options,
            })
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

        exit_code = self._invoke([
            "--only",
            "statelet",
            "--limit",
            "1",
            "--force",
            "--estate",
            str(estate_path),
            "--ledger",
            str(ledger_path),
        ])

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

    @pytest.mark.parametrize(
        ("only", "expected"),
        [
            pytest.param(
                "alpha, beta, ,alpha",
                frozenset({"alpha", "beta"}),
                id="split-trim-and-deduplicate",
            ),
            pytest.param("", None, id="empty-selects-all"),
        ],
    )
    def test_only_flag_is_parsed_into_a_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        only: str,
        expected: frozenset[str] | None,
    ) -> None:
        """`--only` becomes a repository filter, or none at all when empty.

        Commas separate names, blanks are discarded and repeats collapse; an
        empty value means "sweep everything" rather than "sweep nothing".

        The paths are built here rather than taken from the `estate_path` and
        `ledger_path` fixtures only because `run_sweep` is stubbed out, so they
        need to round-trip through the parser and nothing more; two fixtures
        would push this past the argument-count budget.
        """
        calls = self._capture(monkeypatch)

        self._invoke([
            "--only",
            only,
            "--estate",
            str(tmp_path / "estate.yaml"),
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
        ])

        assert len(calls) == 1, calls
        assert calls[0]["options"].only == expected, calls[0]

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

        self._invoke([
            "--estate",
            str(estate_path),
            "--ledger",
            str(ledger_path),
        ])

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
        for name in ("only", "limit", "force", "package", "estate", "ledger"):
            assert f"--{name}" in rendered, f"--{name} missing from:\n{rendered}"
            # A namespaced flag renders as `--<prefix>.<name>`; the default
            # paths in the help carry dots only before file extensions.
            assert f".{name}" not in rendered, (
                f"--{name} should not be namespaced:\n{rendered}"
            )


class TestPackageFlag:
    """`--package` selects the rule package the sweep audits."""

    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch) -> list[sweep.SweepOptions]:
        """Patch `run_sweep`, returning the options each call is handed."""
        seen: list[sweep.SweepOptions] = []

        def fake_run_sweep(
            *,
            estate_path: pathlib.Path,
            ledger_path: pathlib.Path,
            options: sweep.SweepOptions,
        ) -> sweep.Ledger:
            del estate_path, ledger_path
            seen.append(options)
            return []

        monkeypatch.setattr(sweep, "run_sweep", fake_run_sweep)
        return seen

    def test_the_package_flag_reaches_the_sweep_options(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The flag is parsed and carried into `SweepOptions` unchanged."""
        seen = self._capture(monkeypatch)

        TestSweepCommand._invoke(["--package", "nested-cargo-offline-tests"])

        assert [options.package for options in seen] == [
            "nested-cargo-offline-tests"
        ], seen

    def test_the_default_is_the_baseline_package(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without the flag the sweep audits the campaign package."""
        seen = self._capture(monkeypatch)

        TestSweepCommand._invoke([])

        assert [options.package for options in seen] == [sweep.RULE_PACKAGE], seen


def _run_report(*tokens: str) -> tuple[int, str]:
    """Run the `report` subcommand, returning its status and standard error.

    The subcommand is always supplied here rather than by the caller, so a
    test that forgets it cannot fall through to the app's *default* command
    and start a real estate sweep.

    The status is read from `SystemExit` rather than from the return value:
    Cyclopts raises with the command's return as the exit code, so a test
    that only looked at the return would pass for a command that does not
    refuse anything.

    Returns
    -------
    tuple[int, str]
        The command's exit status, and everything it wrote to standard error.
    """
    error = io.StringIO()
    with redirect_stderr(error):
        try:
            sweep.app(["report", *tokens])
        except SystemExit as exc:
            status = 0 if exc.code is None else int(exc.code)
        else:
            status = 0
    return status, error.getvalue()


class TestReportCommandPackageFlag:
    """`report --package` narrows the rendered report to one package."""

    @staticmethod
    def _invoke(
        tokens: list[str],
        *,
        ledger: pathlib.Path,
        output: pathlib.Path,
    ) -> int:
        """Run the app with the report's paths filled in, returning its code."""
        status, _ = _run_report(
            *tokens, "--ledger", str(ledger), "--output", str(output)
        )
        return status

    def test_the_package_reaches_render_report(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """The report command forwards its package selection to the renderer."""
        seen: list[str] = []

        def fake_render(
            _ledger_path: pathlib.Path, package: str = sweep.RULE_PACKAGE
        ) -> str:
            seen.append(package)
            return "report\n"

        monkeypatch.setattr(sweep, "render_report", fake_render)
        output_path = tmp_path / "report.md"

        status = self._invoke(
            ["--package", "nested-cargo-offline-tests"],
            ledger=tmp_path / "ledger.jsonl",
            output=output_path,
        )

        assert status == 0, status
        assert seen == ["nested-cargo-offline-tests"], seen
        assert output_path.read_text(encoding="utf-8") == "report\n"

    def test_the_report_defaults_to_the_campaign_package(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Without a package the renderer is asked for the baseline campaign."""
        seen: list[str] = []

        def fake_render(
            _ledger_path: pathlib.Path, package: str = sweep.RULE_PACKAGE
        ) -> str:
            seen.append(package)
            return "report\n"

        monkeypatch.setattr(sweep, "render_report", fake_render)

        status = self._invoke(
            [],
            ledger=tmp_path / "ledger.jsonl",
            output=tmp_path / "report.md",
        )

        assert status == 0, status
        assert seen == [sweep.RULE_PACKAGE], seen

    def test_a_survey_package_will_not_overwrite_the_baseline_report(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """A non-default package cannot write to the tracked baseline report.

        The default output path is the campaign's checked-in
        `docs/parabellum/baseline-report.md`. Writing a survey there would
        silently replace the baseline with a different package's figures, and
        the report's own heading would still call it a baseline. An operator
        must name where a survey report goes.

        The baseline is redirected to *tmp_path* rather than left alone: the
        test that a survey is refused would otherwise pass while having
        overwritten the checked-in report on the way to being refused.
        """
        rendered: list[pathlib.Path] = []

        def fake_render(
            _ledger_path: pathlib.Path, package: str = sweep.RULE_PACKAGE
        ) -> str:
            rendered.append(_ledger_path)
            return "survey\n"

        monkeypatch.setattr(sweep, "render_report", fake_render)
        monkeypatch.setattr(sweep, "DEFAULT_REPORT_PATH", tmp_path / "baseline.md")

        status, error = _run_report(
            "--package",
            "nested-cargo-offline-tests",
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
        )

        assert status != 0, "a survey package exited successfully"
        assert rendered == [], "the renderer ran before the path was checked"
        assert not (tmp_path / "baseline.md").exists(), "the baseline was written"
        assert "output" in error, f"the refusal should name the missing flag: {error!r}"

    def test_the_baseline_package_still_writes_without_an_output_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """The campaign's own report keeps its documented no-flag invocation.

        The guard exists to stop a survey landing on the baseline, not to make
        every caller name a path, so the default package must still work with
        only `--ledger` given.
        """
        rendered: list[str] = []

        def fake_render(
            _ledger_path: pathlib.Path, package: str = sweep.RULE_PACKAGE
        ) -> str:
            rendered.append(package)
            return "baseline\n"

        monkeypatch.setattr(sweep, "render_report", fake_render)
        monkeypatch.setattr(sweep, "DEFAULT_REPORT_PATH", tmp_path / "baseline.md")

        status, error = _run_report("--ledger", str(tmp_path / "ledger.jsonl"))

        assert status == 0, error
        assert rendered == [sweep.RULE_PACKAGE], rendered
        assert (tmp_path / "baseline.md").read_text(encoding="utf-8") == "baseline\n"
