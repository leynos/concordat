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

    def test_duplicate_auditable_entry_is_audited_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """A repeated auditable entry is cloned and audited once, not twice.

        The excluded path writes a record without doing any work; this one
        clones over the network and runs the rule package, so a duplicate here
        costs a second clone and a second audit slot. `_persist` adds the
        record to `self.ledger` — the view `_already_ledgered` reads — which is
        what stops the second mention.
        """
        head = "c" * 40
        calls: list[tuple[str, str]] = []

        def fake_clone_and_audit(
            owner: str, name: str
        ) -> tuple[str, sweep.RuleRunResult]:
            calls.append((owner, name))
            return head, _fake_result("compliant")

        monkeypatch.setattr(sweep, "resolve_head", lambda owner, name: head)
        monkeypatch.setattr(sweep, "clone_and_audit", fake_clone_and_audit)

        estate_path = tmp_path / "estate.yaml"
        estate_path.write_text(
            "schema_version: 1\nowner: leynos\nrepositories:\n"
            "  - name: wireframe\n"
            "  - name: wireframe\n",
            encoding="utf-8",
        )

        appended = sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)

        assert calls == [("leynos", "wireframe")], (
            f"the repeated entry should be audited exactly once, got {calls}"
        )
        assert len(appended) == 1, (
            f"the repeated entry should be recorded once, got {appended}"
        )
        records = _ledger_lines(ledger_path)
        assert len(records) == 1, (
            f"the ledger should hold one line for the repeated entry, got {records}"
        )
        record = records[0]
        assert record["repository"] == "leynos/wireframe", record
        assert record["commit_sha"] == head, record
        assert record["verdict"] == "compliant", record

    def test_a_repository_named_twice_is_processed_once(
        self,
        tmp_path: pathlib.Path,
        ledger_path: pathlib.Path,
    ) -> None:
        """A duplicate manifest entry does not produce a duplicate record.

        The duplicate check reads the ledger loaded before the run started, so
        a record written during the run was invisible to it: the second
        mention of the same name was processed and appended again.
        """
        estate_path = tmp_path / "estate.yaml"
        estate_path.write_text(
            "schema_version: 1\nowner: leynos\nrepositories:\n"
            "  - name: gauss\n    excluded: migration in flight\n"
            "  - name: gauss\n    excluded: migration in flight\n",
            encoding="utf-8",
        )

        appended = sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)

        assert len(appended) == 1, (
            f"the repeated entry should be recorded once, got {appended}"
        )
        records = _ledger_lines(ledger_path)
        assert len(records) == 1, (
            f"the ledger should hold one line for the repeated entry, got {records}"
        )

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

    @staticmethod
    def _line(**overrides: object) -> str:
        """Return one JSON ledger line, with selected fields overridden."""
        record: dict[str, object] = {
            "schema_version": 1,
            "repository": "leynos/x",
            "commit_sha": "a" * 40,
            "audited_at": "2026-07-19T16:00:00Z",
            "rule_package": "rust-makefile-baseline",
            "rule_version": "0.2.0",
            "makeutil_rev": "abc",
            "verdict": "compliant",
            "findings": [],
        }
        record.update(overrides)
        return json.dumps(record)

    def test_a_line_that_is_not_json_names_the_ledger(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """A truncated line is reported against the file, not as a decode error.

        Decoding inside the comprehension let `json.JSONDecodeError` escape
        naming neither the ledger nor the line number.
        """
        ledger_path.write_text("{oops\n", encoding="utf-8")

        with pytest.raises(sweep.OperationalRuleError, match="line 1") as info:
            sweep._load_ledger(ledger_path)

        assert info.value.operation == "load-ledger", info.value.operation
        assert info.value.resource == ledger_path, info.value.resource

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            pytest.param({"repository": 7}, "'repository' is int", id="repository"),
            pytest.param(
                {"schema_version": "1"},
                "'schema_version' is str",
                id="schema-version-string",
            ),
            pytest.param(
                {"schema_version": True},
                "'schema_version' is bool",
                id="schema-version-boolean",
            ),
            pytest.param({"commit_sha": 7}, "'commit_sha' is int", id="commit-sha"),
            pytest.param({"findings": "none"}, "'findings' is str", id="findings"),
            pytest.param(
                {"exclusion_reason": 7},
                "'exclusion_reason' is int",
                id="optional-field",
            ),
            pytest.param(
                {"findings": [7]}, r"findings\[0\] is int", id="finding-entry"
            ),
            pytest.param(
                {"findings": [{"rule_id": "A"}]},
                "missing 'severity'",
                id="finding-missing-key",
            ),
        ],
    )
    def test_malformed_field_types_are_rejected(
        self,
        ledger_path: pathlib.Path,
        overrides: dict[str, object],
        reason: str,
    ) -> None:
        """Key presence is not enough; each field's type is checked too.

        A hand-edited ledger with a numeric `repository` or a string
        `findings` satisfied the key check and then failed far from here — in
        the report renderer, or as a `TypeError` mid-sweep.
        """
        ledger_path.write_text(self._line(**overrides) + "\n", encoding="utf-8")

        with pytest.raises(sweep.OperationalRuleError, match=reason) as info:
            sweep._load_ledger(ledger_path)

        assert info.value.operation == "load-ledger", info.value.operation
        assert info.value.resource == ledger_path, info.value.resource

    def test_a_finding_field_of_the_wrong_type_is_rejected(
        self,
        ledger_path: pathlib.Path,
    ) -> None:
        """A finding's `line` must be a number, as the renderer assumes."""
        finding = {
            "rule_id": "QG-001",
            "severity": "error",
            "verdict": "noncompliant",
            "path": "Makefile",
            "line": "9",
            "message": "m",
        }
        ledger_path.write_text(self._line(findings=[finding]) + "\n", encoding="utf-8")

        with pytest.raises(sweep.OperationalRuleError, match="'line' is str") as info:
            sweep._load_ledger(ledger_path)

        assert info.value.operation == "load-ledger", info.value.operation

    def test_a_complete_record_round_trips(self, ledger_path: pathlib.Path) -> None:
        """A record written by the sweep decodes back unchanged."""
        record = sweep._excluded_record("leynos/gauss", "migration in flight")
        ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        assert sweep._load_ledger(ledger_path) == [record], "record should round-trip"
