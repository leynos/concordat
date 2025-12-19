"""Unit tests for canonical artifact comparison and sync."""

from __future__ import annotations

import hashlib
from pathlib import Path  # noqa: TC003

from concordat.canon_artifacts import (
    ArtifactStatus,
    SyncConfig,
    compare_manifest_to_published,
    load_manifest,
    sync_artifacts,
)


def _write_manifest_via_yaml(
    *,
    manifest_path: Path,
    artifact_id: str,
    artifact_path: str,
    sha256: str,
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "artifacts:",
                f"  - id: {artifact_id}",
                "    type: lint-config",
                f"    path: {artifact_path}",
                "    description: test artifact",
                f"    sha256: {sha256}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_compare_ok(tmp_path: Path) -> None:
    """Published files matching the template are marked ok."""
    root = tmp_path / "concordat"
    template_file = (
        root / "platform-standards" / "canon" / "lint" / "python" / "ruff.toml"
    )
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text("rule = 1\n", encoding="utf-8")
    template_sha = hashlib.sha256(template_file.read_bytes()).hexdigest()

    manifest_path = root / "platform-standards" / "canon" / "manifest.yaml"
    _write_manifest_via_yaml(
        manifest_path=manifest_path,
        artifact_id="python-ruff-config",
        artifact_path="platform-standards/canon/lint/python/ruff.toml",
        sha256=template_sha,
    )
    manifest = load_manifest(manifest_path)

    published_root = tmp_path / "platform-standards-published"
    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    published_file.parent.mkdir(parents=True, exist_ok=True)
    published_file.write_text("rule = 1\n", encoding="utf-8")

    comparisons = compare_manifest_to_published(manifest, published_root=published_root)
    assert len(comparisons) == 1
    assert comparisons[0].status == ArtifactStatus.OK


def test_compare_outdated(tmp_path: Path) -> None:
    """Published files differing from the template are marked outdated."""
    root = tmp_path / "concordat"
    template_file = (
        root / "platform-standards" / "canon" / "lint" / "python" / "ruff.toml"
    )
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text("rule = 1\n", encoding="utf-8")
    template_sha = hashlib.sha256(template_file.read_bytes()).hexdigest()

    manifest_path = root / "platform-standards" / "canon" / "manifest.yaml"
    _write_manifest_via_yaml(
        manifest_path=manifest_path,
        artifact_id="python-ruff-config",
        artifact_path="platform-standards/canon/lint/python/ruff.toml",
        sha256=template_sha,
    )
    manifest = load_manifest(manifest_path)

    published_root = tmp_path / "platform-standards-published"
    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    published_file.parent.mkdir(parents=True, exist_ok=True)
    published_file.write_text("rule = 2\n", encoding="utf-8")

    comparisons = compare_manifest_to_published(manifest, published_root=published_root)
    assert comparisons[0].status == ArtifactStatus.OUTDATED


def test_compare_missing(tmp_path: Path) -> None:
    """Missing published files are surfaced as missing."""
    root = tmp_path / "concordat"
    template_file = (
        root / "platform-standards" / "canon" / "lint" / "python" / "ruff.toml"
    )
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text("rule = 1\n", encoding="utf-8")
    template_sha = hashlib.sha256(template_file.read_bytes()).hexdigest()

    manifest_path = root / "platform-standards" / "canon" / "manifest.yaml"
    _write_manifest_via_yaml(
        manifest_path=manifest_path,
        artifact_id="python-ruff-config",
        artifact_path="platform-standards/canon/lint/python/ruff.toml",
        sha256=template_sha,
    )
    manifest = load_manifest(manifest_path)

    published_root = tmp_path / "platform-standards-published"

    comparisons = compare_manifest_to_published(manifest, published_root=published_root)
    assert comparisons[0].status == ArtifactStatus.MISSING


def test_sync_updates_published(tmp_path: Path) -> None:
    """Sync copies the template content into the published checkout."""
    root = tmp_path / "concordat"
    template_file = (
        root / "platform-standards" / "canon" / "lint" / "python" / "ruff.toml"
    )
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text("rule = 1\n", encoding="utf-8")
    template_sha = hashlib.sha256(template_file.read_bytes()).hexdigest()

    manifest_path = root / "platform-standards" / "canon" / "manifest.yaml"
    _write_manifest_via_yaml(
        manifest_path=manifest_path,
        artifact_id="python-ruff-config",
        artifact_path="platform-standards/canon/lint/python/ruff.toml",
        sha256=template_sha,
    )
    manifest = load_manifest(manifest_path)

    published_root = tmp_path / "platform-standards-published"
    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    published_file.parent.mkdir(parents=True, exist_ok=True)
    published_file.write_text("rule = 2\n", encoding="utf-8")

    comparisons = list(
        compare_manifest_to_published(manifest, published_root=published_root)
    )
    assert comparisons[0].status == ArtifactStatus.OUTDATED

    actions = sync_artifacts(
        comparisons,
        SyncConfig(
            template_root=manifest.template_root,
            published_root=published_root,
            ids={"python-ruff-config"},
        ),
    )
    assert actions[0].copied is True
    assert published_file.read_text(encoding="utf-8") == "rule = 1\n"
