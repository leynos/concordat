"""Unit tests for canonical artifact comparison and sync."""

from __future__ import annotations

import hashlib
from pathlib import Path  # noqa: TC003

import pytest

from concordat.canon_artifacts import (
    ArtifactStatus,
    CanonArtifactsError,
    SyncConfig,
    compare_manifest_to_published,
    load_manifest,
    render_status_table,
    sha256_digest,
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


def _write_manifest_entries(
    *,
    manifest_path: Path,
    artifacts: list[dict[str, str]],
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["schema_version: 1", "artifacts:"]
    for artifact in artifacts:
        lines.extend(
            [
                f"  - id: {artifact['id']}",
                f"    type: {artifact['type']}",
                f"    path: {artifact['path']}",
                f"    description: {artifact['description']}",
                f"    sha256: {artifact['sha256']}",
            ]
        )
    lines.append("")
    manifest_path.write_text("\n".join(lines), encoding="utf-8")


def _write_template_file(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


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


def test_sync_missing_artifact_creates_destination(tmp_path: Path) -> None:
    """sync_artifacts recreates a missing artifact and its path."""
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

    comparisons = list(
        compare_manifest_to_published(manifest, published_root=published_root)
    )
    assert comparisons[0].status == ArtifactStatus.MISSING

    actions = sync_artifacts(
        comparisons,
        SyncConfig(
            template_root=manifest.template_root,
            published_root=published_root,
            ids={"python-ruff-config"},
        ),
    )
    assert actions
    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    assert published_file.is_file()
    assert published_file.read_text(encoding="utf-8") == "rule = 1\n"


def test_sync_dry_run_does_not_modify_published(tmp_path: Path) -> None:
    """dry_run=True returns planned actions but does not modify destination files."""
    root = tmp_path / "concordat"
    template_file = (
        root / "platform-standards" / "canon" / "lint" / "python" / "ruff.toml"
    )
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text("rule = 2\n", encoding="utf-8")
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
    original = published_file.read_text(encoding="utf-8")

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
            dry_run=True,
        ),
    )
    assert actions
    assert actions[0].copied is False
    assert published_file.read_text(encoding="utf-8") == original


def test_sync_include_unchanged_controls_ok_inclusion(tmp_path: Path) -> None:
    """include_unchanged controls whether OK artifacts are included in sync."""
    root = tmp_path / "concordat"
    template_outdated = _write_template_file(
        root,
        "platform-standards/canon/lint/python/outdated.toml",
        "rule = 2\n",
    )
    template_ok = _write_template_file(
        root,
        "platform-standards/canon/lint/python/ok.toml",
        "rule = 3\n",
    )
    sha_outdated = hashlib.sha256(template_outdated.read_bytes()).hexdigest()
    sha_ok = hashlib.sha256(template_ok.read_bytes()).hexdigest()

    manifest_path = root / "platform-standards" / "canon" / "manifest.yaml"
    _write_manifest_entries(
        manifest_path=manifest_path,
        artifacts=[
            {
                "id": "outdated",
                "type": "lint-config",
                "path": "platform-standards/canon/lint/python/outdated.toml",
                "description": "outdated",
                "sha256": sha_outdated,
            },
            {
                "id": "ok",
                "type": "lint-config",
                "path": "platform-standards/canon/lint/python/ok.toml",
                "description": "ok",
                "sha256": sha_ok,
            },
        ],
    )
    manifest = load_manifest(manifest_path)
    published_root = tmp_path / "platform-standards-published"
    published_outdated = published_root / "canon" / "lint" / "python" / "outdated.toml"
    published_ok = published_root / "canon" / "lint" / "python" / "ok.toml"
    published_outdated.parent.mkdir(parents=True, exist_ok=True)
    published_outdated.write_text("rule = 1\n", encoding="utf-8")
    published_ok.write_text("rule = 3\n", encoding="utf-8")

    comparisons = list(
        compare_manifest_to_published(manifest, published_root=published_root)
    )
    statuses = {c.id: c.status for c in comparisons}
    assert statuses["outdated"] == ArtifactStatus.OUTDATED
    assert statuses["ok"] == ArtifactStatus.OK

    default_actions = sync_artifacts(
        comparisons,
        SyncConfig(
            template_root=manifest.template_root,
            published_root=published_root,
            dry_run=True,
        ),
    )
    assert {action.status for action in default_actions} == {ArtifactStatus.OUTDATED}

    include_actions = sync_artifacts(
        comparisons,
        SyncConfig(
            template_root=manifest.template_root,
            published_root=published_root,
            dry_run=True,
            include_unchanged=True,
        ),
    )
    assert {action.status for action in include_actions} == {
        ArtifactStatus.OK,
        ArtifactStatus.OUTDATED,
    }


def test_compare_template_manifest_mismatch_is_reported_and_skipped_by_sync(
    tmp_path: Path,
) -> None:
    """TEMPLATE_MANIFEST_MISMATCH is reported and prevents sync."""
    root = tmp_path / "concordat"
    template_file = (
        root / "platform-standards" / "canon" / "lint" / "python" / "ruff.toml"
    )
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text("rule = 1\n", encoding="utf-8")

    manifest_path = root / "platform-standards" / "canon" / "manifest.yaml"
    _write_manifest_via_yaml(
        manifest_path=manifest_path,
        artifact_id="python-ruff-config",
        artifact_path="platform-standards/canon/lint/python/ruff.toml",
        sha256="0" * 64,
    )
    manifest = load_manifest(manifest_path)
    published_root = tmp_path / "platform-standards-published"
    published_file = published_root / "canon" / "lint" / "python" / "ruff.toml"
    published_file.parent.mkdir(parents=True, exist_ok=True)
    published_file.write_text("rule = 2\n", encoding="utf-8")

    comparisons = list(
        compare_manifest_to_published(manifest, published_root=published_root)
    )
    assert comparisons[0].status == ArtifactStatus.TEMPLATE_MANIFEST_MISMATCH

    actions = sync_artifacts(
        comparisons,
        SyncConfig(
            template_root=manifest.template_root,
            published_root=published_root,
            ids={"python-ruff-config"},
        ),
    )
    assert actions == ()
    assert published_file.read_text(encoding="utf-8") == "rule = 2\n"


def test_compare_filters_ids_and_types(tmp_path: Path) -> None:
    """compare_manifest_to_published respects ids and types filters."""
    root = tmp_path / "concordat"
    artifact_a = _write_template_file(
        root,
        "platform-standards/canon/lint/python/a.toml",
        "a = 1\n",
    )
    artifact_b = _write_template_file(
        root,
        "platform-standards/canon/policies/workflows/b.rego",
        "package test\n",
    )
    sha_a = hashlib.sha256(artifact_a.read_bytes()).hexdigest()
    sha_b = hashlib.sha256(artifact_b.read_bytes()).hexdigest()

    manifest_path = root / "platform-standards" / "canon" / "manifest.yaml"
    _write_manifest_entries(
        manifest_path=manifest_path,
        artifacts=[
            {
                "id": "a",
                "type": "lint-config",
                "path": "platform-standards/canon/lint/python/a.toml",
                "description": "a",
                "sha256": sha_a,
            },
            {
                "id": "b",
                "type": "opa-policy",
                "path": "platform-standards/canon/policies/workflows/b.rego",
                "description": "b",
                "sha256": sha_b,
            },
        ],
    )
    manifest = load_manifest(manifest_path)
    published_root = tmp_path / "platform-standards-published"

    by_id = compare_manifest_to_published(
        manifest, published_root=published_root, ids={"a"}
    )
    assert {c.id for c in by_id} == {"a"}

    by_type = compare_manifest_to_published(
        manifest, published_root=published_root, types={"opa-policy"}
    )
    assert {c.id for c in by_type} == {"b"}


def test_load_manifest_rejects_non_mapping(tmp_path: Path) -> None:
    """load_manifest rejects non-mapping YAML roots."""
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("- not-a-map\n", encoding="utf-8")

    with pytest.raises(CanonArtifactsError) as excinfo:
        load_manifest(manifest_path)

    assert str(excinfo.value) == f"Manifest content must be a mapping: {manifest_path}"


def test_load_manifest_rejects_missing_file(tmp_path: Path) -> None:
    """load_manifest rejects missing manifest files."""
    manifest_path = tmp_path / "missing.yaml"

    with pytest.raises(CanonArtifactsError) as excinfo:
        load_manifest(manifest_path)

    assert str(excinfo.value) == f"Manifest not found: {manifest_path}"


def test_load_manifest_rejects_wrong_schema_version(tmp_path: Path) -> None:
    """load_manifest rejects unexpected schema versions."""
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("schema_version: 2\nartifacts: []\n", encoding="utf-8")

    with pytest.raises(CanonArtifactsError) as excinfo:
        load_manifest(manifest_path)

    assert str(excinfo.value) == (
        f"Unsupported manifest schema_version=2 (expected 1): {manifest_path}"
    )


def test_load_manifest_rejects_empty_artifacts_list(tmp_path: Path) -> None:
    """load_manifest rejects empty artifact lists."""
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("schema_version: 1\nartifacts: []\n", encoding="utf-8")

    with pytest.raises(CanonArtifactsError) as excinfo:
        load_manifest(manifest_path)

    assert str(excinfo.value) == (
        f"Manifest artifacts must be a non-empty list: {manifest_path}"
    )


def test_load_manifest_rejects_non_mapping_artifact_entry(tmp_path: Path) -> None:
    """load_manifest rejects artifacts that are not mappings."""
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "artifacts:",
                "  - 1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CanonArtifactsError) as excinfo:
        load_manifest(manifest_path)

    assert str(excinfo.value) == (
        f"Manifest artifact entries must be mappings: {manifest_path}"
    )


def test_load_manifest_rejects_missing_artifact_key(tmp_path: Path) -> None:
    """load_manifest rejects artifact entries missing required keys."""
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "artifacts:",
                "  - id: missing-key",
                "    type: lint-config",
                "    path: platform-standards/canon/lint/python/ruff.toml",
                "    description: test artifact",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CanonArtifactsError) as excinfo:
        load_manifest(manifest_path)

    assert str(excinfo.value) == (
        f"Manifest artifact missing key 'sha256': {manifest_path}"
    )


def test_sha256_digest_file_matches_hashlib(tmp_path: Path) -> None:
    """sha256_digest returns the file sha256 digest."""
    path = tmp_path / "file.txt"
    path.write_text("hello\n", encoding="utf-8")

    assert sha256_digest(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_sha256_digest_directory_is_order_invariant(tmp_path: Path) -> None:
    """sha256_digest is stable regardless of file creation order."""
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    (first_dir / "b.txt").write_text("b", encoding="utf-8")
    (first_dir / "a.txt").write_text("a", encoding="utf-8")
    first = sha256_digest(first_dir)

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    (second_dir / "a.txt").write_text("a", encoding="utf-8")
    (second_dir / "b.txt").write_text("b", encoding="utf-8")
    second = sha256_digest(second_dir)

    assert first == second


def test_render_status_table_contains_expected_cells(tmp_path: Path) -> None:
    """render_status_table includes a header and comparison row values."""
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
    comparisons = list(
        compare_manifest_to_published(manifest, published_root=published_root)
    )
    table = render_status_table(comparisons)
    assert "id" in table.splitlines()[0]
    assert "python-ruff-config" in table
    assert "missing" in table
