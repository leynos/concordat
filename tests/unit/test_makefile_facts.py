"""Unit tests for the makeutil boundary and envelope construction.

`inspect_makefile` runs the pinned external parser and validates its report;
`build_envelope` assembles the policy input from a checkout. Both are the
concordat side of that boundary, so they are tested together.

Shared values and helpers live in `rule_test_support`; the report builders
below have only this module as a consumer, so they stay here.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.makefile_facts import inspect_makefile
from tests.unit.rule_test_support import (
    MINIMAL_REPORT,
    SpawnFailureCase,
    _assert_operational_context,
    _raise_process_error,
    _write_checkout,
)

if typ.TYPE_CHECKING:
    import pathlib

    from tests.conftest import CmdMox


def _report_with(**overrides: object) -> dict[str, object]:
    """Return an isolated minimal makeutil report with selected overrides."""
    return dict(MINIMAL_REPORT, **overrides)


# A rule and a recipe that pass validation, so a nested-shape case can replace
# exactly one field and leave the rest well-formed. Every field the policy
# reads is present here; that is what makes a single override the variable
# under test.
VALID_RECIPE: typ.Final = {
    "text": "whitaker --all",
    "ignore_errors": False,
    "location": {"start_line": 2},
}

VALID_RULE: typ.Final = {
    "targets": ["lint"],
    "prerequisites": [],
    "conditions": [],
    "recipes": [VALID_RECIPE],
    "location": {"start_line": 1},
    "double_colon": False,
}


def _recipe_with(**overrides: object) -> dict[str, object]:
    """Return an isolated valid recipe with selected overrides."""
    return dict(VALID_RECIPE, **overrides)


def _rule_with(**overrides: object) -> dict[str, object]:
    """Return an isolated valid rule with selected overrides."""
    return dict(VALID_RULE, **overrides)


# A case supplies stdout either verbatim (non-JSON or hand-built payloads) or as
# a factory, so each parametrized run gets its own report mapping.
type MakeutilOutput = str | typ.Callable[[], dict[str, object]]


@dataclasses.dataclass(frozen=True)
class MakeutilFailureCase:
    """A malformed or rejected makeutil subprocess result."""

    exit_code: int
    stdout: MakeutilOutput
    stderr: str
    message: str


class TestInspectMakefile:
    """Behaviour of the makeutil subprocess boundary."""

    def test_complete_parse_returns_report(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Complete parse returns report."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        cmd_mox.mock("makeutil").with_args("parse", "Makefile").returns(
            stdout=json.dumps(MINIMAL_REPORT)
        )
        cmd_mox.replay()
        facts = inspect_makefile(tmp_path / "Makefile")
        cmd_mox.verify()
        assert facts.status == "complete", facts.status
        assert facts.report["schema_version"] == 1, facts.report

    def test_recovered_parse_is_retained(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Recovered parse is retained."""
        _write_checkout(tmp_path, cargo=False, makefile=True)
        report = dict(MINIMAL_REPORT, parse={"status": "recovered", "diagnostics": []})
        cmd_mox.mock("makeutil").returns(exit_code=1, stdout=json.dumps(report))
        cmd_mox.replay()
        facts = inspect_makefile(tmp_path / "Makefile")
        assert facts.status == "recovered", facts.status

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                SpawnFailureCase(FileNotFoundError("makeutil"), "makeutil"),
                id="missing-binary",
            ),
            pytest.param(
                SpawnFailureCase(
                    subprocess.TimeoutExpired(cmd="makeutil", timeout=10.0),
                    "timed out",
                ),
                id="timeout",
            ),
            pytest.param(
                SpawnFailureCase(
                    PermissionError("makeutil"),
                    "could not launch makeutil",
                ),
                id="launch-oserror",
            ),
        ],
    )
    def test_spawn_failure_raises_operational_error(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        case: SpawnFailureCase,
    ) -> None:
        """Every makeutil launch failure carries parse-makefile context.

        ``_run_makeutil`` translates a missing binary, a timeout, and a generic
        ``OSError`` such as a permission denial; each must reach the CLI's
        operational-error boundary naming the Makefile it was parsing.
        """
        _write_checkout(tmp_path, cargo=False, makefile=True)
        monkeypatch.setattr(subprocess, "run", _raise_process_error(case.error))

        with pytest.raises(OperationalRuleError, match=case.match) as exc_info:
            inspect_makefile(tmp_path / "Makefile")

        _assert_operational_context(
            exc_info.value,
            operation="parse-makefile",
            tool="makeutil",
            resource=tmp_path / "Makefile",
        )

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                MakeutilFailureCase(
                    exit_code=2,
                    stdout="",
                    stderr="makeutil: source-utf8: invalid",
                    message="source-utf8",
                ),
                id="fatal-exit",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(schema_version=99),
                    stderr="",
                    message="schema",
                ),
                id="unsupported-schema",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout="not json at all",
                    stderr="",
                    message="invalid JSON",
                ),
                id="invalid-json",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=json.dumps([1, 2, 3]),
                    stderr="",
                    message="not a JSON object",
                ),
                id="non-object-json",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(parse="complete"),
                    stderr="",
                    message="no `parse` object",
                ),
                id="non-object-parse",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(
                        parse={"status": "bogus", "diagnostics": []}
                    ),
                    stderr="",
                    message="unknown parse status",
                ),
                id="unknown-status",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=1,
                    stdout=lambda: _report_with(
                        parse={"status": "complete", "diagnostics": []}
                    ),
                    stderr="",
                    message="disagrees with its exit code",
                ),
                id="exit-status-disagreement",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(source="Makefile"),
                    stderr="",
                    message="malformed `source`",
                ),
                id="malformed-source",
            ),
            pytest.param(
                MakeutilFailureCase(
                    exit_code=0,
                    stdout=lambda: _report_with(rules={"not": "a list"}),
                    stderr="",
                    message="malformed `rules`",
                ),
                id="malformed-rules",
            ),
        ],
    )
    def test_rejects_invalid_makeutil_output(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
        case: MakeutilFailureCase,
    ) -> None:
        """Invalid makeutil output raises a parse-makefile operational error.

        Each case receives an isolated report mapping (callables are resolved
        per invocation), so no parametrization mutates ``MINIMAL_REPORT``.
        """
        _write_checkout(tmp_path, cargo=False, makefile=True)
        match case.stdout:
            case str() as verbatim:
                stdout_text = verbatim
            case build_report:
                stdout_text = json.dumps(build_report())
        cmd_mox.mock("makeutil").returns(
            exit_code=case.exit_code,
            stdout=stdout_text,
            stderr=case.stderr,
        )
        cmd_mox.replay()
        with pytest.raises(OperationalRuleError, match=case.message) as exc_info:
            inspect_makefile(tmp_path / "Makefile")
        error = exc_info.value
        assert error.operation == "parse-makefile", error.operation
        assert error.tool == "makeutil", error.tool
        assert error.resource == tmp_path / "Makefile", error.resource

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            pytest.param(
                _report_with(rules=["not-an-object"]),
                r"malformed `rules\[0\]`",
                id="rule-not-an-object",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(targets="lint")]),
                r"malformed `rules\[0\]\.targets`",
                id="rule-targets-not-a-list",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(targets=["lint", 7])]),
                r"malformed `rules\[0\]\.targets\[1\]`",
                id="rule-targets-entry-not-a-string",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(prerequisites={"a": 1})]),
                r"malformed `rules\[0\]\.prerequisites`",
                id="rule-prerequisites-not-a-list",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(prerequisites=[None])]),
                r"malformed `rules\[0\]\.prerequisites\[0\]`",
                id="rule-prerequisites-entry-not-a-string",
            ),
            pytest.param(
                # `conditions` reaches Rego's `count()`, which raises on a
                # scalar rather than degrading to undefined.
                _report_with(rules=[_rule_with(conditions=0)]),
                r"malformed `rules\[0\]\.conditions`",
                id="rule-conditions-not-a-list",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(recipes="lint")]),
                r"malformed `rules\[0\]\.recipes`",
                id="rule-recipes-not-a-list",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(location=[1])]),
                r"malformed `rules\[0\]\.location`",
                id="rule-location-not-an-object",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(location={"start_line": "3"})]),
                r"malformed `rules\[0\]\.location\.start_line`",
                id="rule-location-start-line-not-an-integer",
            ),
            pytest.param(
                # `bool` subclasses `int`, so a boolean line number would slip
                # past a bare `isinstance(..., int)` check.
                _report_with(rules=[_rule_with(location={"start_line": True})]),
                r"malformed `rules\[0\]\.location\.start_line`",
                id="rule-location-start-line-boolean",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(double_colon="false")]),
                r"malformed `rules\[0\]\.double_colon`",
                id="rule-double-colon-not-a-bool",
            ),
            pytest.param(
                # The policy tests `double_colon == true`, and in Rego `1` is
                # not `true` — an integer here would silently stop the
                # ambiguity check from firing, so it is refused.
                _report_with(rules=[_rule_with(double_colon=1)]),
                r"malformed `rules\[0\]\.double_colon`",
                id="rule-double-colon-integer",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(recipes=["echo hi"])]),
                r"malformed `rules\[0\]\.recipes\[0\]`",
                id="recipe-not-an-object",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(recipes=[_recipe_with(text=3)])]),
                r"malformed `rules\[0\]\.recipes\[0\]\.text`",
                id="recipe-text-not-a-string",
            ),
            pytest.param(
                _report_with(
                    rules=[_rule_with(recipes=[_recipe_with(ignore_errors="no")])]
                ),
                r"malformed `rules\[0\]\.recipes\[0\]\.ignore_errors`",
                id="recipe-ignore-errors-not-a-bool",
            ),
            pytest.param(
                # `ignore_errors == true` is likewise a boolean comparison: an
                # integer `1` would read as "not ignored" and hide a soft-
                # skipped gate.
                _report_with(
                    rules=[_rule_with(recipes=[_recipe_with(ignore_errors=1)])]
                ),
                r"malformed `rules\[0\]\.recipes\[0\]\.ignore_errors`",
                id="recipe-ignore-errors-integer",
            ),
            pytest.param(
                _report_with(rules=[_rule_with(recipes=[_recipe_with(location=None)])]),
                r"malformed `rules\[0\]\.recipes\[0\]\.location`",
                id="recipe-location-not-an-object",
            ),
            pytest.param(
                _report_with(
                    rules=[_rule_with(recipes=[_recipe_with(location={})])],
                ),
                r"malformed `rules\[0\]\.recipes\[0\]\.location\.start_line`",
                id="recipe-location-start-line-absent",
            ),
            pytest.param(
                _report_with(includes=["Makefile.local"]),
                r"malformed `includes\[0\]`",
                id="include-not-an-object",
            ),
            pytest.param(
                _report_with(includes=[{"location": {"start_line": None}}]),
                r"malformed `includes\[0\]\.location\.start_line`",
                id="include-location-start-line-not-an-integer",
            ),
            pytest.param(
                _report_with(includes=[{}]),
                r"malformed `includes\[0\]\.location`",
                id="include-location-absent",
            ),
        ],
    )
    def test_rejects_malformed_nested_report_data(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
        payload: dict[str, object],
        message: str,
    ) -> None:
        """Nested rule, recipe, and include shapes are validated before the cast.

        The policy reads every one of these fields without guarding its type.
        A wrong shape mostly makes the Rego expression undefined rather than
        raising, so the audit would report a verdict computed from data it
        could not actually read — worse than refusing. The label in each
        message points at the exact offending path.
        """
        _write_checkout(tmp_path, cargo=False, makefile=True)
        cmd_mox.mock("makeutil").returns(
            exit_code=0, stdout=json.dumps(payload), stderr=""
        )
        cmd_mox.replay()

        with pytest.raises(OperationalRuleError, match=message) as exc_info:
            inspect_makefile(tmp_path / "Makefile")

        _assert_operational_context(
            exc_info.value,
            operation="parse-makefile",
            tool="makeutil",
            resource=tmp_path / "Makefile",
        )

    def test_accepts_a_fully_populated_report_unchanged(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """A valid report is forwarded verbatim, unknown keys included.

        Validation only inspects. Anything makeutil adds beyond the fields the
        policy consumes has to survive to Conftest, so this asserts identity
        with the payload rather than merely that no error was raised.
        """
        payload = _report_with(
            rules=[_rule_with(diagnostics=["unknown-rule-key"])],
            includes=[{"location": {"start_line": 4}, "path": "Makefile.local"}],
            tool={"name": "makeutil", "version": "0.1.0"},
        )
        _write_checkout(tmp_path, cargo=False, makefile=True)
        cmd_mox.mock("makeutil").returns(
            exit_code=0, stdout=json.dumps(payload), stderr=""
        )
        cmd_mox.replay()

        facts = inspect_makefile(tmp_path / "Makefile")

        assert facts.status == "complete", facts.status
        assert facts.report == payload, (
            "the validated report must reach Conftest unchanged, keeping keys "
            f"the policy does not read: {facts.report}"
        )
