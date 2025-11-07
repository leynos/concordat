"""Black-box workflow test executed via `act` when enabled."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

EVENT = Path("tests/fixtures/workflows/auditor-workflow-dispatch.json")
WORKFLOW = Path(".github/workflows/auditor.yml")


@pytest.mark.integration
def test_auditor_workflow_produces_sarif(tmp_path: Path) -> None:
    """Smoke test that runs the workflow via act when explicitly enabled."""
    if os.getenv("CONCORDAT_RUN_ACT_TESTS") != "1":
        pytest.skip("set CONCORDAT_RUN_ACT_TESTS=1 to run act-based workflow tests.")
    if shutil.which("act") is None:
        pytest.skip("act CLI is not installed.")

    artifact_dir = tmp_path / "artifacts"
    command = [
        "act",
        "workflow_dispatch",
        "-W",
        str(WORKFLOW),
        "-j",
        "auditor",
        "-e",
        str(EVENT),
        "-P",
        "ubuntu-latest=catthehacker/ubuntu:act-latest",
        "--artifact-server-path",
        str(artifact_dir),
        "-b",
    ]
    env = os.environ.copy()
    env.setdefault("GITHUB_TOKEN", "local-dev-token")
    completed = subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    sarif_files = list(artifact_dir.rglob("*.sarif"))
    assert sarif_files, f"Expected SARIF artifact, logs:\n{completed.stdout}"
    data = json.loads(sarif_files[0].read_text())
    assert data["version"] == "2.1.0"
