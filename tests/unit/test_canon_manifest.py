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


def test_every_lint_rule_package_is_registered() -> None:
    """Ensure each rule package ships its policy and manifest through the canon.

    `compare_manifest_to_published` and `sync_artifacts` iterate only the
    manifest, so a package missing from it is never published to consumers,
    however green its own suite is.
    """
    data = yaml.load(MANIFEST.read_text(encoding="utf-8"))
    registered = {artifact["path"] for artifact in data["artifacts"]}
    rules = ROOT / "platform-standards" / "canon" / "lint-rules"
    expected = {
        path.relative_to(ROOT).as_posix()
        for package in sorted(rules.iterdir())
        if package.is_dir()
        for path in (
            package / "rule.yaml",
            *sorted((package / "policy").glob("*.rego")),
        )
        if not path.name.endswith("_test.rego")
    }

    assert expected - registered == set()
