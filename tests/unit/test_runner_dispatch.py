"""`run_rule` evaluates a declared-only package over its own envelope.

`markdown-formatting-baseline` is not registered by identifier; it reaches
its builder through the `sensor.input` kind its manifest declares. The
selection rules themselves are specified in `test_envelope_selection.py`;
this drives the whole path an operator runs, down to the document written
for Conftest.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import typing as typ

from concordat.rules import runner
from tests.unit.rule_test_support import MINIMAL_REPORT

if typ.TYPE_CHECKING:
    import pytest_mock

    from tests.conftest import CmdMox


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
