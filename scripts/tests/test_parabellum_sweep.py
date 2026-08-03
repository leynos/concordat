"""Unit tests for the Operation Parabellum sweep orchestration loop."""

from __future__ import annotations

import json
import typing as typ

import pytest

from scripts import parabellum_sweep as sweep

if typ.TYPE_CHECKING:
    import pathlib


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


class TestLedgerDecoding:
    """`_load_ledger` validates each line before treating it as a record."""

    @pytest.mark.parametrize(
        ("line", "reason"),
        [
            pytest.param('"a string"', "is not a JSON object", id="string"),
            pytest.param("[1, 2]", "is not a JSON object", id="array"),
            pytest.param("7", "is not a JSON object", id="number"),
            pytest.param('{"repository": "leynos/x"}', "is missing", id="missing-keys"),
        ],
    )
    def test_malformed_line_is_rejected(
        self,
        ledger_path: pathlib.Path,
        line: str,
        reason: str,
    ) -> None:
        """A truncated or hand-edited ledger line raises rather than typing in.

        The ledger is read back on every sweep, so a bad line must be reported
        against the file rather than trusted into the typed flow by a cast.
        """
        ledger_path.write_text(line + "\n", encoding="utf-8")

        with pytest.raises(sweep.OperationalRuleError, match=reason) as info:
            sweep._load_ledger(ledger_path)

        assert info.value.operation == "load-ledger", (
            "a bad ledger line should be tagged as a ledger-load failure"
        )
        assert info.value.resource == ledger_path, (
            "the failure should name the ledger it could not decode"
        )

    def test_a_complete_record_round_trips(self, ledger_path: pathlib.Path) -> None:
        """A record written by the sweep decodes back unchanged."""
        record = sweep._excluded_record("leynos/gauss", "migration in flight")
        ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        assert sweep._load_ledger(ledger_path) == [record], "record should round-trip"
