"""Property tests for QG-001's shell readings, with bash as the oracle.

The policy reads recipe text rather than running it, so each property builds
bounded recipes, asks the real Conftest policy for its verdict, and runs the
same recipe through bash with stub tools to see what actually happens. Bash is
the independent oracle: no copy of the policy's patterns appears here.

Each property checks soundness, the direction that matters for a gate: when
the policy accepts a recipe, bash must agree that the gate cannot be skipped.
The policy may still be stricter than bash, for example by flagging a probe
whose failure block exits non-zero before its last statement. That is a false
positive, which is loud, and the named Rego tests pin those shapes.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import typing as typ
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules import runner

if typ.TYPE_CHECKING:
    from concordat.rules.envelope import PolicyEnvelope
    from concordat.rules.makefile_facts import MakeRule, MakeVariable

_REPOSITORY_ROOT = Path(__file__).parents[2]
_RULE_ID: typ.Final = "rust-makefile-baseline"
_ENVELOPE_FIXTURE: typ.Final = (
    _REPOSITORY_ROOT
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / _RULE_ID
    / "fixtures"
    / "envelopes"
    / "compliant.json"
)
_LOCATION: typ.Final = {"start_line": 1}
_BASH_TIMEOUT_SECONDS: typ.Final = 10

# A tool no runner has, so `command -v` always takes its failure branch.
_MISSING_TOOL: typ.Final = "concordat-absent-tool-3f9c"


def _recipe(text: str) -> dict[str, object]:
    """Build one unconditional recipe fact."""
    return {"text": text, "ignore_errors": False, "location": _LOCATION}


def _rule(target: str, recipes: list[str]) -> dict[str, object]:
    """Build one unconditional rule with the given recipe lines."""
    return {
        "targets": [target],
        "prerequisites": [],
        "conditions": [],
        "recipes": [_recipe(text) for text in recipes],
        "location": _LOCATION,
        "double_colon": False,
    }


def _variable(name: str, raw_value: str) -> dict[str, object]:
    """Build one recursive assignment fact."""
    return {
        "name": name,
        "operator": "=",
        "raw_value": raw_value,
        "exported": False,
        "overridden": False,
        "define_block": False,
        "conditions": [],
        "location": _LOCATION,
    }


def _envelope(
    lint_recipes: list[str],
    variables: list[dict[str, object]],
) -> PolicyEnvelope:
    """Return the compliant fixture with `lint` and the variables replaced."""
    loaded = json.loads(_ENVELOPE_FIXTURE.read_text(encoding="utf-8"))
    envelope = typ.cast("PolicyEnvelope", copy.deepcopy(loaded))
    makefile = envelope["makefile"]
    assert makefile is not None, "the compliant fixture must carry Make facts"
    makefile["rules"] = typ.cast(
        "list[MakeRule]",
        [
            _rule("build", ["cargo build"]),
            _rule("test", ["cargo test"]),
            _rule("lint", lint_recipes),
        ],
    )
    makefile["variables"] = typ.cast(
        "list[MakeVariable]", [_variable("WHITAKER", "whitaker"), *variables]
    )
    return envelope


def _qg_messages(envelope: PolicyEnvelope) -> list[str]:
    """Return the QG-001 messages from a real Conftest evaluation."""
    results = runner._invoke_conftest(_RULE_ID, envelope)
    return [
        finding.message
        for finding in runner._findings_from_results(results)
        if finding.rule_id == "QG-001"
    ]


def _host_path() -> str:
    """Return the harness's own PATH, read once for the child's environment."""
    return os.environ.get("PATH", "")


def _run_bash(script: str, stub_dir: Path) -> int:
    """Run ``script`` in bash with ``stub_dir`` first on PATH; return its status."""
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, generated script
        ["bash", "-c", script],  # noqa: S607 - bash from PATH
        env={**os.environ, "PATH": f"{stub_dir}{os.pathsep}{_host_path()}"},
        capture_output=True,
        text=True,
        timeout=_BASH_TIMEOUT_SECONDS,
        check=False,
    )
    return completed.returncode


def _write_stub(directory: Path, name: str, body: str) -> None:
    """Write an executable bash stub called ``name``."""
    stub = directory / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)


# -- command -v --------------------------------------------------------------

_BLOCK_STATEMENTS = st.sampled_from([
    "true;",
    "printf 'tool missing\\n' >&2;",
    "printf 'will exit 0 later\\n' >&2;",
    "exit 0;",
    "exit 1;",
    "exit 2;",
])
_FAILURE_BRANCHES = st.one_of(
    st.sampled_from(["exit 0", "exit 1", "exit 3", "true"]),
    st.lists(_BLOCK_STATEMENTS, min_size=1, max_size=3).map(
        lambda statements: "{ " + " ".join(statements) + " }"
    ),
)


@settings(max_examples=30, deadline=None)
@given(_FAILURE_BRANCHES)
def test_an_accepted_command_probe_fails_when_the_tool_is_missing(failure: str) -> None:
    """If the policy does not call a probe a soft skip, bash fails without the tool.

    The recipe probes a tool no runner has, so its failure branch always runs;
    an accepted probe must leave the recipe with a non-zero status.
    """
    guard = f"command -v {_MISSING_TOOL} >/dev/null 2>&1 || {failure}"
    messages = _qg_messages(_envelope(["$(WHITAKER) --all", guard], []))
    is_flagged = any("command -v" in message for message in messages)

    with TemporaryDirectory(prefix="concordat-probe-property-") as scratch:
        status = _run_bash(guard, Path(scratch))

    assert is_flagged or status != 0, (
        f"the policy accepted {guard!r}, but bash exits {status} without the tool"
    )


# -- environment prefixes -----------------------------------------------------

_PREFIX_TOKENS = st.sampled_from([
    "RUSTFLAGS=-Dwarnings",
    'RUSTFLAGS="-D warnings; -C x|y"',
    "CARGO_TERM_COLOR='always&never'",
    "X=1||true",
    "X=1;true",
    "X=1&&true",
    "X=`true`",
    "X=1|cat",
    "echo",
    "true",
])


@settings(max_examples=30, deadline=None)
@given(st.lists(_PREFIX_TOKENS, min_size=1, max_size=3))
def test_a_seen_through_prefix_runs_the_gate_and_keeps_its_status(
    tokens: list[str],
) -> None:
    """If the policy sees through a prefix variable, bash runs the gate.

    The gate's exit status must also be the recipe's.

    Make expands `$(GATE_ENV)` textually, so bash runs the value followed by
    the gate. A stub gate records that it ran and exits 3; an accepted prefix
    must leave that status as the recipe's.
    """
    value = " ".join(tokens)
    envelope = _envelope(
        ["$(GATE_ENV) $(WHITAKER) --all"], [_variable("GATE_ENV", value)]
    )
    is_accepted = _qg_messages(envelope) == []

    with TemporaryDirectory(prefix="concordat-prefix-property-") as scratch:
        stub_dir = Path(scratch)
        marker = stub_dir / "gate-ran"
        _write_stub(stub_dir, "gate-stub", f"touch {marker}\nexit 3")
        status = _run_bash(f"{value} gate-stub --all", stub_dir)
        gate_ran = marker.exists()

    assert not is_accepted or (gate_ran and status == 3), (
        f"the policy accepted prefix {value!r}, but bash ran the gate={gate_ran} "
        f"and exited {status}"
    )


# -- which ------------------------------------------------------------------

_WHICH_FRAGMENTS = st.sampled_from([
    f"which {_MISSING_TOOL} >/dev/null || exit 0",
    f"{{ which {_MISSING_TOOL} >/dev/null || exit 0; }}",
    f"if true; then which {_MISSING_TOOL} || exit 0; fi",
    f"if false; then :; else which {_MISSING_TOOL} || exit 0; fi",
    f"for t in a; do which {_MISSING_TOOL} || exit 0; done",
    f"true && which {_MISSING_TOOL} || exit 0",
    f"printf 'note; which {_MISSING_TOOL}\\n' || exit 1",
    f"echo which {_MISSING_TOOL} || exit 1",
    "printf 'which writes it\\n' >&2 || exit 1",
])


@settings(max_examples=30, deadline=None)
@given(st.lists(_WHICH_FRAGMENTS, min_size=1, max_size=3))
def test_a_recipe_without_a_which_finding_never_runs_which(
    fragments: list[str],
) -> None:
    """If the policy reports no `which` guard, bash never runs `which`.

    A stub `which` records each call. Whatever the recipe does, an unflagged
    recipe must not have run it.
    """
    guard = "; ".join(fragments)
    messages = _qg_messages(_envelope(["$(WHITAKER) --all", guard], []))
    is_flagged = any('"which" existence guard' in message for message in messages)

    with TemporaryDirectory(prefix="concordat-which-property-") as scratch:
        stub_dir = Path(scratch)
        marker = stub_dir / "which-ran"
        _write_stub(stub_dir, "which", f"touch {marker}\nexit 1")
        _run_bash(guard, stub_dir)
        which_ran = marker.exists()

    assert is_flagged or not which_ran, (
        f"the policy reported no which guard in {guard!r}, but bash ran which"
    )
