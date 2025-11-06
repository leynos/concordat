"""Exercise the canonical workflow CLI helper."""

from __future__ import annotations

import typing as typ

import pytest

from scripts import canon_workflows

if typ.TYPE_CHECKING:
    from tests.conftest import CmdMox


def test_run_invokes_act_with_expected_arguments(
    cmd_mox: CmdMox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Command runner invokes act with the expected workflow payload."""
    meta = canon_workflows.WORKFLOWS["ci"]
    expected_args = canon_workflows._build_args(meta)
    monkeypatch.setattr(canon_workflows, "_act_available", lambda: "act")
    cmd_mox.mock("act").with_args(*expected_args[1:]).returns(exit_code=0)
    cmd_mox.replay()

    canon_workflows.run("ci")

    cmd_mox.verify()


def test_run_unknown_workflow() -> None:
    """Unknown workflow names raise an explicit error."""
    with pytest.raises(canon_workflows.WorkflowLookupError):
        canon_workflows.run("unknown")


def test_dry_run_prints(capsys: pytest.CaptureFixture[str]) -> None:
    """Dry-run mode prints the constructed act command."""
    canon_workflows.run("release", dry_run=True)
    output = capsys.readouterr().out
    assert "Dry run" in output


def test_show_event_outputs_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Show event pretty-prints the sample workflow_dispatch payload."""
    canon_workflows.show_event("ci")
    output = capsys.readouterr().out
    assert "python-version" in output


def test_list_prints_expected_rows(capsys: pytest.CaptureFixture[str]) -> None:
    """List surfaces the canonical workflow metadata."""
    canon_workflows.list_workflows()
    output = capsys.readouterr().out
    assert "canon-ci" in output
