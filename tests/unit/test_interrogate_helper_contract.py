"""Exercise the pure helper adapter's argument and uncertainty boundaries.

The existing envelope regressions keep the trusted canonical oracle and shell
integration coverage. These cases specify the extracted adapter's two query
results without reading a checkout or constructing shell facts.
"""

from __future__ import annotations

import pytest

from concordat.rules.interrogate_helper_contract import (
    is_unresolved_tool_prefix,
    python_arguments,
)
from concordat.rules.makefile_expansion import UNRESOLVED_MARK


@pytest.mark.parametrize(
    ("operands", "expected"),
    [
        pytest.param(["-m"], ["-m"], id="direct-module"),
        pytest.param([], None, id="missing-python-operand"),
        pytest.param(["-I", "-m"], None, id="unsupported-interpreter-option"),
        pytest.param(["run"], None, id="script-is-not-uv-subcommand"),
        pytest.param(
            ["./scripts/uv_gate.py", "run", "--"],
            ["run", "--"],
            id="safe-relative-helper",
        ),
        pytest.param(
            ["scripts/../scripts/uv_gate.py", "run", "--"],
            None,
            id="parent-traversal",
        ),
        pytest.param(["/scripts/uv_gate.py", "run", "--"], None, id="absolute-helper"),
        pytest.param(["scripts/uv_gate.py", "run"], None, id="run-needs-separator"),
        pytest.param(
            ["scripts/uv_gate.py", "run", "--", "--"],
            None,
            id="run-refuses-repeated-separator",
        ),
        pytest.param(
            ["scripts/uv_gate.py", "run", "--", "argument"],
            None,
            id="run-prefix-ends-at-command",
        ),
        pytest.param(
            ["scripts/uv_gate.py", "run", "--offline", "--"],
            None,
            id="run-policy-override",
        ),
        pytest.param(
            ["scripts/uv_gate.py", "tool", "--from", "interrogate==1.7.0", "--"],
            ["tool", "--from", "interrogate==1.7.0", "--"],
            id="pinned-tool",
        ),
        pytest.param(
            ["scripts/uv_gate.py", "tool", "--from", "interrogate", "--"],
            None,
            id="tool-needs-exact-pin",
        ),
        pytest.param(
            ["scripts/uv_gate.py", "tool", "--from", UNRESOLVED_MARK],
            ["tool", "--from", UNRESOLVED_MARK],
            id="unknown-tool-spec-remains-visible",
        ),
        pytest.param(
            ["scripts/uv_gate.py", "tool", "--from", UNRESOLVED_MARK, "-qU"],
            None,
            id="known-refusal-precedes-unknown-spec",
        ),
    ],
)
def test_python_arguments_keep_the_existing_launcher_boundary(
    operands: list[str], expected: list[str] | None
) -> None:
    """Separate Python/helper argument recognition from shell interpretation."""
    assert python_arguments(operands, UNRESOLVED_MARK) == expected


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        pytest.param(
            ["python3", "scripts/uv_gate.py", "tool", "--from", UNRESOLVED_MARK],
            True,
            id="unknown-helper-tool-spec",
        ),
        pytest.param(
            [
                "/usr/bin/python",
                "./scripts/uv_gate.py",
                "tool",
                "--from",
                UNRESOLVED_MARK,
            ],
            True,
            id="qualified-python-and-safe-helper",
        ),
        pytest.param([], False, id="empty-command"),
        pytest.param(
            ["python3", "scripts/uv_gate.py", "run", "--"],
            False,
            id="run-is-not-tool-spec-recovery",
        ),
        pytest.param(
            ["python3", "scripts/uv_gate.py", "tool", "--from", "interrogate==1.7.0"],
            False,
            id="known-spec-is-not-recovery",
        ),
        pytest.param(
            [
                "python3",
                "scripts/uv_gate.py",
                "tool",
                "--from",
                UNRESOLVED_MARK,
                "--offline",
            ],
            False,
            id="refused-helper-does-not-recover",
        ),
        pytest.param(
            ["uv", "scripts/uv_gate.py", "tool", "--from", UNRESOLVED_MARK],
            False,
            id="non-python-does-not-claim-helper",
        ),
        pytest.param(
            ["python3/", "scripts/uv_gate.py", "tool", "--from", UNRESOLVED_MARK],
            False,
            id="non-executable-interpreter-word",
        ),
        pytest.param(
            ["python3", "other/uv_gate.py", "tool", "--from", UNRESOLVED_MARK],
            False,
            id="foreign-helper-does-not-recover",
        ),
    ],
)
def test_unresolved_tool_prefix_retains_only_existing_helper_uncertainty(
    prefix: list[str], *, expected: bool
) -> None:
    """Leave placeholder construction and recipe provenance with the reader."""
    assert is_unresolved_tool_prefix(prefix, UNRESOLVED_MARK) is expected
