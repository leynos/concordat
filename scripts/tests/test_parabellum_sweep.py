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
        assert estate.owner == "leynos"
        names = [entry.name for entry in estate.repositories]
        assert names == ["wireframe", "gauss", "statelet"]
        assert estate.repositories[1].excluded == ("test-framework migration in flight")
        assert estate.repositories[0].excluded is None


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
        assert len(appended) == len(records) == 3
        audited = [r for r in records if r["verdict"] == "compliant"]
        assert {r["repository"] for r in audited} == {
            "leynos/wireframe",
            "leynos/statelet",
        }
        assert all(r["commit_sha"] == "a" * 40 for r in audited)
        assert all(r["makeutil_rev"] == sweep.MAKEUTIL_REV for r in audited)

    def test_excluded_repository_gets_reasoned_record(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """An excluded repository is recorded with its reason, not audited."""
        sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)
        records = _ledger_lines(ledger_path)
        excluded = [r for r in records if r["verdict"] == "excluded"]
        assert len(excluded) == 1
        assert excluded[0]["repository"] == "leynos/gauss"
        assert excluded[0]["exclusion_reason"] == ("test-framework migration in flight")

    def test_second_run_is_idempotent(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """A repository already ledgered at the same commit is skipped."""
        sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)
        appended = sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)
        assert appended == []
        assert len(_ledger_lines(ledger_path)) == 3

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
            force=True,
        )
        assert [r["repository"] for r in appended] == [
            "leynos/wireframe",
            "leynos/statelet",
        ]

    def test_only_filter_limits_scope(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """--only restricts the sweep to the named repositories."""
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            only={"statelet"},
        )
        assert [r["repository"] for r in appended] == ["leynos/statelet"]

    def test_limit_bounds_audits(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """--limit bounds the number of audited repositories."""
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            limit=1,
        )
        audited = [r for r in appended if r["verdict"] != "excluded"]
        assert len(audited) == 1

    def test_operational_error_records_error_verdict(
        self,
        estate_path: pathlib.Path,
        ledger_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An audit failure is ledgered as an error record, not raised."""

        def raise_operational(owner: str, name: str) -> typ.NoReturn:
            message = "conftest is required"
            raise sweep.OperationalRuleError(message)

        monkeypatch.setattr(sweep, "clone_and_audit", raise_operational)
        appended = sweep.run_sweep(
            estate_path=estate_path,
            ledger_path=ledger_path,
            only={"wireframe"},
        )
        assert len(appended) == 1
        assert appended[0]["verdict"] == "error"
        assert "conftest" in appended[0]["error_detail"]
