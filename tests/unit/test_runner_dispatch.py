"""Unit tests for policy-input dispatch in the rule runner.

A rule manifest names the envelope its sensor evaluates under `sensor.input`.
The runner must assemble that document — and only that document — for the
checkout, default to the Rust kind for a manifest that predates the field,
and refuse a kind this build cannot build rather than guessing.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import manifest, runner
from tests.unit.rule_test_support import MINIMAL_REPORT

if typ.TYPE_CHECKING:
    import pytest_mock

    from tests.conftest import CmdMox


def _write_package(
    root: pathlib.Path, rule_id: str, manifest_text: str | None
) -> pathlib.Path:
    """Create a rule package skeleton with an optional manifest.

    Returns
    -------
    pathlib.Path
        The package directory.
    """
    package = root / rule_id
    (package / "policy").mkdir(parents=True)
    if manifest_text is not None:
        (package / "rule.yaml").write_text(manifest_text, encoding="utf-8")
    return package


class TestEnvelopeBuilder:
    """`_envelope_builder` resolves the manifest's declared input kind."""

    def test_missing_manifest_defaults_to_the_rust_envelope(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A package without `rule.yaml` keeps the historic Rust input."""
        package = _write_package(tmp_path, "legacy-rule", None)
        assert runner._envelope_builder(package) is runner.build_envelope

    def test_manifest_without_input_defaults_to_the_rust_envelope(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A manifest predating `sensor.input` keeps the historic Rust input."""
        package = _write_package(tmp_path, "legacy-rule", "sensor:\n  type: conftest\n")
        assert runner._envelope_builder(package) is runner.build_envelope

    def test_declared_markdown_input_selects_the_markdown_builder(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The Markdown kind maps to the Markdown envelope builder."""
        package = _write_package(
            tmp_path,
            "prose-rule",
            "sensor:\n  input: policy-input/markdown-formatting-baseline\n",
        )
        assert runner._envelope_builder(package) is runner.build_markdown_envelope

    @pytest.mark.parametrize(
        "declared",
        [
            pytest.param("policy-input/unknown", id="unknown-kind"),
            pytest.param("[not, a, string]", id="wrong-type"),
        ],
    )
    def test_unbuildable_input_kind_is_refused(
        self, tmp_path: pathlib.Path, declared: str
    ) -> None:
        """A kind this build cannot assemble is an operational error."""
        package = _write_package(
            tmp_path, "odd-rule", f"sensor:\n  input: {declared}\n"
        )
        with pytest.raises(OperationalRuleError, match="declares the policy input"):
            runner._envelope_builder(package)

    @pytest.mark.parametrize(
        "manifest_text",
        [
            pytest.param("sensor:\n", id="null"),
            pytest.param("sensor: conftest\n", id="scalar"),
            pytest.param("sensor:\n  - type: conftest\n", id="list"),
        ],
    )
    def test_non_mapping_sensor_is_refused(
        self, tmp_path: pathlib.Path, manifest_text: str
    ) -> None:
        """A `sensor` that is not a mapping is an error, not the Rust default.

        Falling back would hand the Rust envelope to whichever policy the
        package ships. A Markdown policy reading that document finds no
        `applicability.markdown_files`, skips its own checks, and reports a
        compliant verdict it never established.
        """
        package = _write_package(tmp_path, "odd-rule", manifest_text)
        with pytest.raises(OperationalRuleError, match="declares `sensor` as"):
            runner._envelope_builder(package)

    def test_an_unreadable_manifest_is_operational(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A manifest that exists but cannot be read must not read as absent.

        `Path.is_file` answers ``False`` for an unreadable path as readily as
        for a missing one, and suppresses the error outright from Python
        3.14. The package would then lose its declared parameters and its
        policy input silently, and the runner would hand the Rust envelope to
        whichever policy it ships.
        """
        package = _write_package(tmp_path, "locked-rule", "sensor:\n  type: conftest\n")
        package.chmod(0o000)
        try:
            with pytest.raises(OperationalRuleError, match="cannot read rule manifest"):
                runner._envelope_builder(package)
        finally:
            package.chmod(0o755)

    def test_an_absent_manifest_is_still_absent(self, tmp_path: pathlib.Path) -> None:
        """The narrow half: a package without a manifest keeps the default."""
        package = _write_package(tmp_path, "bare-rule", None)
        assert manifest.load(package) == {}

    def test_shipped_packages_declare_their_inputs(self) -> None:
        """Both shipped manifests resolve to the builder for their own kind."""
        rust = runner._rule_package_dir("rust-makefile-baseline")
        markdown = runner._rule_package_dir("markdown-formatting-baseline")
        assert runner._envelope_builder(rust) is runner.build_envelope
        assert runner._envelope_builder(markdown) is runner.build_markdown_envelope


class TestRunRuleDispatch:
    """`run_rule` sends the declared envelope kind to Conftest."""

    def test_markdown_rule_receives_a_markdown_envelope(
        self,
        tmp_path: pathlib.Path,
        cmd_mox: CmdMox,
        mocker: pytest_mock.MockFixture,
    ) -> None:
        """The envelope written for Conftest carries the Markdown kind and facts."""
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "README.md").write_text("# Hi\n", encoding="utf-8")
        (checkout / "Makefile").write_text("fmt:\n\tmdtablefix --in-place\n")
        cmd_mox.mock("makeutil").returns(stdout=json.dumps(MINIMAL_REPORT))
        seen: dict[str, object] = {}

        def capture(argv: list[str], rule_id: str) -> subprocess.CompletedProcess[str]:
            seen["envelope"] = json.loads(pathlib.Path(argv[-1]).read_text())
            seen["namespace"] = argv[argv.index("--namespace") + 1]
            return subprocess.CompletedProcess(
                argv, 0, json.dumps([{"failures": []}]), ""
            )

        mocker.patch.object(runner, "_run_conftest", side_effect=capture)
        cmd_mox.replay()

        result = runner.run_rule("markdown-formatting-baseline", checkout)

        cmd_mox.verify()
        assert result.verdict == "compliant", result
        envelope = typ.cast("dict[str, object]", seen["envelope"])
        assert envelope["kind"] == "policy-input/markdown-formatting-baseline", envelope
        assert envelope["makefile"] == MINIMAL_REPORT, envelope
        assert "cargo" not in envelope, envelope
        assert seen["namespace"] == "canon.lint_rules.markdown_formatting_baseline"
