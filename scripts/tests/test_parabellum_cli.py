"""Unit tests for the Parabellum sweep command-line surface."""

from __future__ import annotations

import typing as typ

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
        for name in ("only", "limit", "force", "estate", "ledger"):
            assert f"--{name}" in rendered, f"--{name} missing from:\n{rendered}"
            # A namespaced flag renders as `--<prefix>.<name>`; the default
            # paths in the help carry dots only before file extensions.
            assert f".{name}" not in rendered, (
                f"--{name} should not be namespaced:\n{rendered}"
            )
