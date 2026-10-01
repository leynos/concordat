"""End-to-end test of the Auditor's live API path for CV-006.

The snapshot test replays a recorded context and never touches the client.
This one serves the GitHub REST responses from a local HTTP server and runs
`python -m concordat.auditor` against it with `--api-url`, so the whole live
path is exercised: the client's endpoints and pagination, `fetch`, the
check, and the SARIF it writes.
"""

from __future__ import annotations

import base64
import http.server
import json
import subprocess
import sys
import threading
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

_PUBLISHER = """\
on:
  push:
    branches: [main]
jobs:
  coverage-upload:
    environment: codescene
    steps:
      - uses: leynos/shared-actions/.github/actions/upload-codescene-coverage@abc
"""

_REPO = "/repos/example/demo"


def _routes(*, repository_secrets: list[str]) -> dict[str, object]:
    """Return the API responses for a repository mid-way through a move."""
    workflow = base64.b64encode(_PUBLISHER.encode()).decode()
    return {
        _REPO: {
            "owner": {"login": "example"},
            "name": "demo",
            "default_branch": "main",
            "allow_squash_merge": True,
            "delete_branch_on_merge": True,
        },
        f"{_REPO}/teams": [{"slug": "platform", "permission": "maintain"}],
        f"{_REPO}/collaborators": [],
        f"{_REPO}/labels": [],
        f"{_REPO}/contents/.github/workflows": [
            {
                "type": "file",
                "name": "coverage-main.yml",
                "path": ".github/workflows/coverage-main.yml",
            }
        ],
        f"{_REPO}/contents/.github/workflows/coverage-main.yml": {"content": workflow},
        f"{_REPO}/actions/secrets": {
            "secrets": [{"name": name} for name in repository_secrets]
        },
        f"{_REPO}/environments/codescene": {
            "name": "codescene",
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            },
        },
        f"{_REPO}/environments/codescene/deployment-branch-policies": {
            "branch_policies": [{"name": "main", "type": "branch"}]
        },
        f"{_REPO}/environments/codescene/secrets": {
            "secrets": [{"name": "CS_ACCESS_TOKEN"}]
        },
    }


def _handler(routes: dict[str, object]) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a handler serving *routes* by path, ignoring the query."""

    class Handler(http.server.BaseHTTPRequestHandler):
        """Serve canned JSON, or a 404 for any path not in the routes."""

        def do_GET(self) -> None:
            """Answer one GET from the routes."""
            path = self.path.split("?", 1)[0]
            body = routes.get(path)
            status = 404 if body is None else 200
            payload = json.dumps(
                {"message": "Not Found"} if body is None else body
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # ruff: ignore[builtin-argument-shadowing] - base signature
            """Keep the test output quiet."""

    return Handler


@pytest.fixture
def api(request: pytest.FixtureRequest) -> cabc.Iterator[str]:
    """Serve the parametrised routes on a local port and yield the API URL."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(request.param))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _audit(api_url: str, tmp_path: Path) -> list[str]:
    """Run the Auditor CLI against *api_url* and return CV-006's statuses."""
    sarif_path = tmp_path / "audit.sarif"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [
            sys.executable,
            "-m",
            "concordat.auditor",
            "--repository",
            "example/demo",
            "--token",
            "placeholder",
            "--api-url",
            api_url,
            "--sarif-path",
            str(sarif_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    results = json.loads(sarif_path.read_text())["runs"][0]["results"]
    return [
        result["properties"]["status"]
        for result in results
        if result["ruleId"] == "CV-006"
    ]


@pytest.mark.parametrize(
    "api", [_routes(repository_secrets=["CS_ACCESS_TOKEN"])], indirect=True
)
def test_the_live_path_reports_a_secret_not_yet_moved(api: str, tmp_path: Path) -> None:
    """The environment is ready but the repository still holds the token."""
    assert _audit(api, tmp_path) == ["secret-not-moved"]


@pytest.mark.parametrize("api", [_routes(repository_secrets=["OTHER"])], indirect=True)
def test_the_live_path_clears_a_completed_move(api: str, tmp_path: Path) -> None:
    """The token lives in the environment alone, so CV-006 reports nothing."""
    assert _audit(api, tmp_path) == []
