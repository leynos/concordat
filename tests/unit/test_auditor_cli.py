"""CLI smoke tests for `python -m concordat.auditor`."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_cli_runs_with_snapshot(tmp_path: Path) -> None:
    """Integration smoke test for the auditor CLI using a snapshot."""
    sarif_path = tmp_path / "audit.sarif"
    snapshot = Path("tests/fixtures/auditor/snapshot.json")
    command = [
        sys.executable,
        "-m",
        "concordat.auditor",
        "--repository",
        "example/demo",
        "--snapshot",
        str(snapshot),
        "--sarif-path",
        str(sarif_path),
    ]
    completed = subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    data = json.loads(sarif_path.read_text())
    assert data["version"] == "2.1.0"
    assert data["runs"][0]["tool"]["driver"]["name"] == "Concordat Auditor"


def test_cli_reports_a_secret_not_yet_moved(tmp_path: Path) -> None:
    """A snapshot's CodeScene settings reach CV-006 and its SARIF result.

    The environment is ready but the token is still a repository secret, the
    state the estate passes through mid-move, so the result names that step.
    """
    snapshot = json.loads(Path("tests/fixtures/auditor/snapshot.json").read_text())
    snapshot["codescene"] = {
        "uploads": True,
        "environment_exists": True,
        "protected_branches": False,
        "custom_branch_policies": True,
        "branch_policies": [{"name": "main", "type": "branch"}],
        "environment_secrets": ["CS_ACCESS_TOKEN"],
        "repository_secrets": ["CS_ACCESS_TOKEN"],
    }
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot))
    sarif_path = tmp_path / "audit.sarif"
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "concordat.auditor",
            "--repository",
            "example/demo",
            "--snapshot",
            str(snapshot_path),
            "--sarif-path",
            str(sarif_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    results = json.loads(sarif_path.read_text())["runs"][0]["results"]
    cv006 = [result for result in results if result["ruleId"] == "CV-006"]
    assert [result["properties"]["status"] for result in cv006] == [
        "secret-not-moved"
    ], cv006
