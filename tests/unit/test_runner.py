"""Unit tests for the rule runner: Conftest invocation and rule execution.

These cover `concordat.rules.runner` end of the boundary — how Conftest is
launched, how its exit codes and output are interpreted, how a rule package
identifier is validated, and that the packages root is resolved lazily.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import runner
from concordat.rules.runner import run_rule
from tests.unit.rule_test_support import (
    MINIMAL_REPORT,
    SpawnFailureCase,
    _assert_operational_context,
    _raise_process_error,
    _write_checkout,
)

if typ.TYPE_CHECKING:
    import pathlib

    import pytest_mock

    from tests.conftest import CmdMox


class TestRunConftest:
    """The Conftest subprocess wrapper translates spawn failures."""

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                SpawnFailureCase(
                    FileNotFoundError("conftest"),
                    "conftest is required",
                ),
                id="missing-binary",
            ),
            pytest.param(
                SpawnFailureCase(
                    subprocess.TimeoutExpired(cmd="conftest", timeout=60.0),
                    "timed out",
                ),
                id="timeout",
            ),
        ],
    )
    def test_spawn_failure_translates(
        self,
        monkeypatch: pytest.MonkeyPatch,
        case: SpawnFailureCase,
    ) -> None:
        """Every conftest launch failure carries invoke-conftest context.

        Unlike ``_run_makeutil`` this wrapper translates only a missing binary
        and a timeout, so a generic ``OSError`` is deliberately not covered
        here; the rule ID stands in for the resource being evaluated.
        """
        monkeypatch.setattr(runner.subprocess, "run", _raise_process_error(case.error))

        with pytest.raises(OperationalRuleError, match=case.match) as exc_info:
            runner._run_conftest(["conftest", "test"], "rust-makefile-baseline")

        _assert_operational_context(
            exc_info.value,
            operation="invoke-conftest",
            tool="conftest",
            resource="rust-makefile-baseline",
        )


class TestRunRule:
    """End-to-end behaviour of run_rule with mocked externals."""

    def test_unknown_rule_package_raises(self, tmp_path: pathlib.Path) -> None:
        """Unknown rule package raises."""
        _write_checkout(tmp_path, cargo=True, makefile=False)
        with pytest.raises(OperationalRuleError, match="no-such-rule"):
            run_rule("no-such-rule", tmp_path)

    def test_failures_map_to_findings(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Failures map to findings."""
        _write_checkout(tmp_path, cargo=True, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        conftest_doc = json.dumps(
            [
                {
                    "filename": "envelope.json",
                    "namespace": "canon.lint_rules.rust_makefile_baseline",
                    "successes": 12,
                    "failures": [
                        {
                            "msg": 'required Make target "lint" is absent',
                            "metadata": {
                                "line": 0,
                                "path": "Makefile",
                                "rule_id": "FP-003",
                                "severity": "error",
                                "verdict": "noncompliant",
                            },
                        },
                        {
                            "msg": "cannot prove the gate",
                            "metadata": {
                                "line": 3,
                                "path": "Makefile",
                                "rule_id": "QG-001",
                                "severity": "error",
                                "verdict": "indeterminate",
                            },
                        },
                    ],
                }
            ]
        )
        cmd_mox.mock("conftest").returns(exit_code=1, stdout=conftest_doc)
        cmd_mox.replay()
        result = run_rule("rust-makefile-baseline", tmp_path)
        cmd_mox.verify()
        assert result.verdict == "noncompliant", result
        assert result.exit_code == 1, result
        assert [f.rule_id for f in result.findings] == ["FP-003", "QG-001"], (
            result.findings
        )
        assert result.findings[1].line == 3, result.findings[1]

    def test_clean_run_is_compliant(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Clean run is compliant."""
        _write_checkout(tmp_path, cargo=True, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        clean_doc = json.dumps(
            [
                {
                    "filename": "envelope.json",
                    "namespace": "canon.lint_rules.rust_makefile_baseline",
                    "successes": 14,
                }
            ]
        )
        cmd_mox.mock("conftest").returns(exit_code=0, stdout=clean_doc)
        cmd_mox.replay()
        result = run_rule("rust-makefile-baseline", tmp_path)
        assert result.verdict == "compliant", result
        assert result.exit_code == 0, result
        assert result.findings == (), result.findings

    def test_indeterminate_only_yields_indeterminate_verdict(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
    ) -> None:
        """Indeterminate only yields indeterminate verdict."""
        _write_checkout(tmp_path, cargo=True, makefile=True)
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        doc = json.dumps(
            [
                {
                    "filename": "envelope.json",
                    "namespace": "canon.lint_rules.rust_makefile_baseline",
                    "successes": 13,
                    "failures": [
                        {
                            "msg": "cannot prove the gate",
                            "metadata": {
                                "line": 0,
                                "path": "Makefile",
                                "rule_id": "QG-001",
                                "severity": "error",
                                "verdict": "indeterminate",
                            },
                        }
                    ],
                }
            ]
        )
        cmd_mox.mock("conftest").returns(exit_code=1, stdout=doc)
        cmd_mox.replay()
        result = run_rule("rust-makefile-baseline", tmp_path)
        assert result.verdict == "indeterminate", result
        assert result.exit_code == 1, result


class TestRulePackageIdentifier:
    """Validation of the rule package identifier before filesystem access."""

    @pytest.mark.parametrize(
        "rule_id",
        [
            pytest.param("../outside", id="parent-traversal"),
            pytest.param("nested/package", id="path-separator"),
            pytest.param("/etc/passwd", id="absolute-path"),
            pytest.param(".", id="dot"),
            pytest.param("..", id="dot-dot"),
            pytest.param("rule_id", id="underscore"),
            pytest.param("Rule-Id", id="upper-case"),
            pytest.param("-leading", id="leading-hyphen"),
            pytest.param("trailing-", id="trailing-hyphen"),
            pytest.param("double--hyphen", id="repeated-hyphen"),
            pytest.param("", id="empty"),
            pytest.param("back\\slash", id="backslash"),
        ],
    )
    def test_invalid_identifier_is_rejected_before_any_lookup(
        self,
        mocker: pytest_mock.MockFixture,
        rule_id: str,
    ) -> None:
        """A non-canonical identifier never reaches the policy machinery.

        Rejection must precede the manifest read and the Conftest call, so a
        crafted identifier cannot probe the filesystem or run a policy.
        """
        parameters = mocker.patch.object(runner, "_rule_parameters")
        conftest = mocker.patch.object(runner, "_run_conftest")

        with pytest.raises(OperationalRuleError) as exc_info:
            runner._rule_package_dir(rule_id)

        error = exc_info.value
        assert error.operation == "load-rule-package", error.operation
        assert error.resource == rule_id, error.resource
        parameters.assert_not_called()
        conftest.assert_not_called()

    def test_valid_identifier_resolves_the_packaged_rule(self) -> None:
        """The shipped package name still resolves to its policy directory."""
        rule_dir = runner._rule_package_dir("rust-makefile-baseline")
        assert (rule_dir / "policy").is_dir(), rule_dir


class TestConftestExitCodes:
    """Only Conftest's policy verdict codes may be decoded as findings."""

    @pytest.mark.parametrize(
        "returncode",
        [pytest.param(2, id="usage-error"), pytest.param(3, id="internal-error")],
    )
    def test_operational_exit_code_is_not_read_as_findings(
        self,
        mocker: pytest_mock.MockFixture,
        returncode: int,
    ) -> None:
        """Well-formed JSON from a failed run is still an operational error.

        Conftest can print a usable-looking document while reporting that it
        never evaluated the policy; decoding it would turn an operational
        failure into a clean verdict.
        """
        completed = subprocess.CompletedProcess(
            args=["conftest"],
            returncode=returncode,
            stdout=json.dumps([{"failures": []}]),
            stderr="error parsing policy",
        )
        mocker.patch.object(runner, "_run_conftest", return_value=completed)
        mocker.patch.object(runner, "_rule_parameters", return_value={})

        with pytest.raises(
            OperationalRuleError, match="without evaluating"
        ) as exc_info:
            runner._invoke_conftest("rust-makefile-baseline", typ.cast("typ.Any", {}))

        error = exc_info.value
        assert error.operation == "invoke-conftest", error.operation
        assert error.tool == "conftest", error.tool
        assert error.resource == "rust-makefile-baseline", error.resource
        assert str(returncode) in str(error), str(error)
        assert "error parsing policy" in str(error), str(error)

    @pytest.mark.parametrize(
        "stdout",
        [
            pytest.param("{}", id="object"),
            pytest.param('"a string"', id="string"),
            pytest.param("7", id="number"),
            pytest.param("null", id="null"),
        ],
    )
    def test_valid_json_that_is_not_an_array_is_rejected(
        self,
        mocker: pytest_mock.MockFixture,
        stdout: str,
    ) -> None:
        """A decodable document of the wrong shape is still unusable.

        A result document is an array, one entry per evaluated input. Anything
        else would previously be returned as-is on the strength of the type
        annotation and fail later, far from the cause.
        """
        completed = subprocess.CompletedProcess(
            args=["conftest"],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        mocker.patch.object(runner, "_run_conftest", return_value=completed)
        mocker.patch.object(runner, "_rule_parameters", return_value={})

        with pytest.raises(OperationalRuleError, match="no usable output") as exc_info:
            runner._invoke_conftest("rust-makefile-baseline", typ.cast("typ.Any", {}))

        _assert_operational_context(
            exc_info.value,
            operation="invoke-conftest",
            tool="conftest",
            resource="rust-makefile-baseline",
        )

    @pytest.mark.parametrize(
        "returncode",
        [pytest.param(0, id="clean"), pytest.param(1, id="policy-failures")],
    )
    def test_policy_exit_codes_are_decoded(
        self,
        mocker: pytest_mock.MockFixture,
        returncode: int,
    ) -> None:
        """Exit 0 and 1 both describe an evaluated policy and are decoded."""
        completed = subprocess.CompletedProcess(
            args=["conftest"],
            returncode=returncode,
            stdout=json.dumps([{"failures": []}]),
            stderr="",
        )
        mocker.patch.object(runner, "_run_conftest", return_value=completed)
        mocker.patch.object(runner, "_rule_parameters", return_value={})

        results = runner._invoke_conftest(
            "rust-makefile-baseline", typ.cast("typ.Any", {})
        )

        assert results == [{"failures": []}], results


class TestRulePackagesDirIsLazy:
    """The canon rule tree is resolved on use, not on import."""

    def test_importing_the_module_does_not_resolve_the_tree(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """A fresh import performs no resource lookup for the rule tree.

        Resolving at import made merely importing the CLI depend on the policy
        tree being present and readable, so a packaging fault surfaced as an
        import error rather than an operational one.

        `importlib.resources.files` is patched rather than
        `_resolve_rule_packages_dir`: reloading re-defines the module's own
        functions, so a patch on the module would be discarded before the
        module body ran and the test could not fail.
        """
        files = mocker.patch("importlib.resources.files", autospec=True)
        runner._rule_packages_dir.cache_clear()

        importlib.reload(runner)

        assert not hasattr(runner, "RULE_PACKAGES_DIR"), (
            "an eagerly resolved module constant resolves the tree at import"
        )
        files.assert_not_called()

    def test_the_resolver_runs_when_a_package_is_looked_up(
        self,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """`_rule_package_dir` resolves the tree, and caches the result."""
        real_root = runner._resolve_rule_packages_dir()
        resolve = mocker.patch.object(
            runner,
            "_resolve_rule_packages_dir",
            autospec=True,
            return_value=real_root,
        )
        runner._rule_packages_dir.cache_clear()

        runner._rule_package_dir("rust-makefile-baseline")
        runner._rule_package_dir("rust-makefile-baseline")

        assert resolve.call_count == 1, (
            f"the tree should resolve once and cache, got {resolve.call_count} calls"
        )
        runner._rule_packages_dir.cache_clear()
