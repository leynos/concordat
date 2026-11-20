"""Cross-process coordination tests for Parabellum sweeps."""

from __future__ import annotations

import contextlib
import json
import pathlib
import subprocess
import sys
import typing as typ


def _ledger_lines(path: pathlib.Path) -> list[dict[str, typ.Any]]:
    """Read the sweep records from a JSONL ledger."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_concurrent_sweeps_share_ledger_without_duplicate_audits(
    tmp_path: pathlib.Path,
) -> None:
    """Separate sweep processes recheck the ledger after the first completes."""
    estate_path = tmp_path / "estate.yaml"
    ledger_path = tmp_path / "ledger.jsonl"
    start_path = tmp_path / "start"
    calls_path = tmp_path / "calls.txt"
    estate_path.write_text(
        "schema_version: 1\nowner: leynos\nrepositories:\n  - name: wireframe\n",
        encoding="utf-8",
    )
    worker = """
import pathlib, sys, time
from scripts import parabellum_sweep as sweep

estate_path, ledger_path, start_path, calls_path = map(pathlib.Path, sys.argv[1:])
while not start_path.exists():
    time.sleep(0.005)

def fake_clone_and_audit(owner, name, **_kwargs):
    with calls_path.open("a", encoding="utf-8") as calls_file:
        calls_file.write(f"{owner}/{name}\\n")
    time.sleep(0.2)
    result = sweep.RuleRunResult(
        rule_package=sweep.RULE_PACKAGE,
        verdict="compliant",
        findings=(),
    )
    return "a" * 40, result

sweep.resolve_head = lambda _owner, _name: "a" * 40
sweep.clone_and_audit = fake_clone_and_audit
sweep.run_sweep(estate_path=estate_path, ledger_path=ledger_path)
"""
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-c",
        worker,
        str(estate_path),
        str(ledger_path),
        str(start_path),
        str(calls_path),
    ]
    with contextlib.ExitStack() as stack:
        workers = [
            stack.enter_context(
                subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed Python argv
                    command,
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    text=True,
                )
            )
            for _ in range(2)
        ]
        start_path.touch()
        try:
            output = [process.communicate(timeout=5) for process in workers]
        except subprocess.TimeoutExpired:
            for process in workers:
                process.kill()
            raise

    assert [process.returncode for process in workers] == [0, 0], output
    assert calls_path.read_text(encoding="utf-8").splitlines() == ["leynos/wireframe"]
    assert len(_ledger_lines(ledger_path)) == 1
