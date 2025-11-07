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
