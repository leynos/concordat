"""Validate the canonical artifact manifest integrity."""

from __future__ import annotations

import hashlib
from pathlib import Path

import ruamel.yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "platform-standards" / "canon" / "manifest.yaml"

yaml = ruamel.yaml.YAML(typ="safe")


def test_manifest_entries_have_valid_paths_and_hashes() -> None:
    """Ensure every manifest entry exists and matches its checksum."""
    data = yaml.load(MANIFEST.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    artifacts = data.get("artifacts", [])
    assert artifacts, "manifest artifacts list must not be empty"

    for artifact in artifacts:
        rel_path = artifact["path"]
        artifact_path = ROOT / rel_path
        assert artifact_path.exists(), f"missing artifact {rel_path}"
        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        assert digest == artifact["sha256"], f"checksum mismatch for {rel_path}"
        assert artifact.get("type"), f"artifact type missing for {rel_path}"
        assert artifact.get("description"), (
            f"artifact description missing for {rel_path}"
        )
