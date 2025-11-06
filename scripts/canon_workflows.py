"""Helper to exercise canonical workflows locally via `act`."""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

from cyclopts import App

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / "platform-standards" / "canon" / ".github" / "workflows"
EVENT_DIR = WORKFLOW_DIR / "events"
ACT_ARCH = "linux/amd64"


@dataclasses.dataclass(frozen=True)
class WorkflowMeta:
    """Describe the location of a canonical workflow and its sample event."""

    name: str
    workflow_file: Path
    event_file: Path


class WorkflowError(RuntimeError):
    """Raised when canonical workflow invocation fails."""


class WorkflowFileError(WorkflowError):
    """Raised when required workflow files are missing."""

    def __init__(self, path: Path) -> None:
        """Store the missing path for later inspection."""
        super().__init__(f"Expected file missing: {path}")
        self.path = path


class WorkflowLookupError(WorkflowError):
    """Raised when the caller references an unknown workflow key."""

    def __init__(self, name: str) -> None:
        """Capture the requested workflow for debugging."""
        known = ", ".join(sorted(WORKFLOWS))
        super().__init__(f"Unknown workflow {name!r}; options: {known}")
        self.name = name


class ActNotInstalledError(WorkflowError):
    """Raised when the `act` executable is missing."""

    def __init__(self) -> None:
        """Provide a prescriptive installation hint."""
        super().__init__("act executable not found on PATH; install nektos/act first")


WORKFLOWS: dict[str, WorkflowMeta] = {
    "ci": WorkflowMeta(
        name="canon-ci",
        workflow_file=WORKFLOW_DIR / "ci.yml",
        event_file=EVENT_DIR / "ci.workflow_dispatch.json",
    ),
    "release": WorkflowMeta(
        name="canon-release",
        workflow_file=WORKFLOW_DIR / "release.yml",
        event_file=EVENT_DIR / "release.workflow_dispatch.json",
    ),
}

app = App()


def _assert_exists(path: Path) -> None:
    """Verify that a file exists prior to running act."""
    if not path.exists():
        raise WorkflowFileError(path)


def _act_available() -> str:
    """Return the resolved `act` binary or raise a helpful error."""
    binary = shutil.which("act")
    if not binary:
        raise ActNotInstalledError
    return binary


def _build_args(meta: WorkflowMeta) -> list[str]:
    """Construct the `act` command-line for the workflow metadata."""
    return [
        "act",
        "workflow_dispatch",
        "--workflows",
        str(meta.workflow_file),
        "--eventpath",
        str(meta.event_file),
        "--container-architecture",
        ACT_ARCH,
    ]


@app.command(name="list")
def list_workflows() -> None:
    """Print the workflows registered in the manifest."""
    rows: list[tuple[str, WorkflowMeta]] = sorted(WORKFLOWS.items())
    for key, meta in rows:
        rel_path = meta.workflow_file.relative_to(PROJECT_ROOT)
        print(f"{key}\t{meta.name}\t{rel_path}")


@app.command()
def show_event(name: str) -> None:
    """Display the workflow_dispatch payload used for `act`."""
    meta = WORKFLOWS.get(name)
    if not meta:
        raise WorkflowLookupError(name)
    _assert_exists(meta.event_file)
    print(json.dumps(json.loads(meta.event_file.read_text(encoding="utf-8")), indent=2))


@app.command()
def run(name: str, *, dry_run: bool = False) -> None:
    """Execute `act workflow_dispatch` for the named canonical workflow."""
    meta = WORKFLOWS.get(name)
    if not meta:
        raise WorkflowLookupError(name)
    _assert_exists(meta.workflow_file)
    _assert_exists(meta.event_file)

    args = _build_args(meta)
    if dry_run:
        print("Dry run:", " ".join(args))
        return

    _act_available()
    subprocess.run(args, check=True)  # noqa: S603


def main() -> None:  # pragma: no cover - exercised via CLI
    """Entrypoint for `python -m scripts.canon_workflows`."""
    app()


if __name__ == "__main__":
    main()
