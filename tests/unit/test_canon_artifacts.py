"""Unit tests for canonical artifact comparison and sync."""

from __future__ import annotations

import dataclasses
import hashlib
import re
import typing as typ
from pathlib import Path

import pytest

from concordat.canon_artifacts import (
    ArtifactStatus,
    CanonArtifactsError,
    CanonManifest,
    SyncConfig,
    compare_manifest_to_published,
    load_manifest,
    render_status_table,
    sha256_digest,
    sync_artifacts,
)


class ManifestFixture(typ.NamedTuple):
    """Fixture return type for manifest setup."""

    root: Path
    manifest: CanonManifest
    template_file: Path
    published_root: Path
    artifact_id: str
    template_sha: str


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


def _setup_multi_artifact_scenario(
    tmp_path: Path,
    artifacts: list[tuple[str, str, str, str | None]],
) -> tuple[CanonManifest, Path]:
    root = tmp_path / "concordat"
    published_root = tmp_path / "platform-standards-published"
    manifest_path = root / "platform-standards" / "canon" / "manifest.yaml"
    root.mkdir(parents=True, exist_ok=True)
    published_root.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, str]] = []
    for artifact_id, relative_path, template_content, published_content in artifacts:
        _write_template_file(root, relative_path, template_content)
        sha = hashlib.sha256(template_content.encode("utf-8")).hexdigest()
        entries.append(
            {
                "id": artifact_id,
                "type": "lint-config",
                "path": relative_path,
                "description": artifact_id,
                "sha256": sha,
            }
        )

        if published_content is None:
            continue

        published_relpath = Path(relative_path)
        if published_relpath.parts[:1] == ("platform-standards",):
            published_relpath = published_relpath.relative_to("platform-standards")
        published_file = published_root / published_relpath
        published_file.parent.mkdir(parents=True, exist_ok=True)
        published_file.write_text(published_content, encoding="utf-8")

    _write_manifest_entries(manifest_path=manifest_path, artifacts=entries)
    return load_manifest(manifest_path), published_root


@pytest.fixture
def single_artifact_manifest(tmp_path: Path) -> typ.Callable[..., ManifestFixture]:
    """Create a single-artifact manifest scenario."""
    counter = 0

    def factory(
        *,
        artifact_id: str = "python-ruff-config",
        template_content: str = "rule = 1\n",
        template_relpath: str = "platform-standards/canon/lint/python/ruff.toml",
        use_correct_sha: bool = True,
    ) -> ManifestFixture:
        nonlocal counter
        counter += 1

        root = tmp_path / f"concordat-{counter}"
        template_file = _write_template_file(root, template_relpath, template_content)
        template_sha = hashlib.sha256(template_file.read_bytes()).hexdigest()

        manifest_path = root / "platform-standards" / "canon" / "manifest.yaml"
        manifest_sha = template_sha if use_correct_sha else "0" * 64
        _write_manifest_via_yaml(
            manifest_path=manifest_path,
            artifact_id=artifact_id,
            artifact_path=template_relpath,
            sha256=manifest_sha,
        )
        manifest = load_manifest(manifest_path)

        published_root = tmp_path / f"platform-standards-published-{counter}"
        published_root.mkdir(parents=True, exist_ok=True)

        return ManifestFixture(
            root=root,
            manifest=manifest,
            template_file=template_file,
            published_root=published_root,
            artifact_id=artifact_id,
            template_sha=template_sha,
        )

    return factory


def _create_published_file(
    published_root: Path, artifact_relpath: str, content: str
) -> Path:
    relpath = Path(artifact_relpath)
    if relpath.parts[:1] == ("platform-standards",):
        relpath = Path(*relpath.parts[1:])
    published_file = published_root / relpath
    published_file.parent.mkdir(parents=True, exist_ok=True)
    published_file.write_text(content, encoding="utf-8")
    return published_file


@pytest.mark.parametrize(
    ("published_content", "expected_status"),
    [
        ("rule = 1\n", ArtifactStatus.OK),
        ("rule = 2\n", ArtifactStatus.OUTDATED),
        (None, ArtifactStatus.MISSING),
    ],
    ids=["ok", "outdated", "missing"],
)
def test_compare_status_detection(
    single_artifact_manifest: typ.Callable[..., ManifestFixture],
    published_content: str | None,
    expected_status: ArtifactStatus,
) -> None:
    """Comparison correctly detects artifact status (OK, OUTDATED, MISSING)."""
    setup = single_artifact_manifest()

    if published_content is not None:
        _create_published_file(
            setup.published_root,
            setup.template_file.relative_to(setup.root).as_posix(),
            published_content,
        )

    comparisons = compare_manifest_to_published(
        setup.manifest, published_root=setup.published_root
    )
    assert len(comparisons) == 1
    assert comparisons[0].status == expected_status


@dataclasses.dataclass(frozen=True, slots=True)
class SyncScenario:
    """Test scenario for sync behavior tests."""

    template_content: str
    published_content: str
    use_correct_sha: bool
    dry_run: bool
    expected_status: ArtifactStatus
    expected_copied: bool | None
    expected_actions_count: int
    expected_final_content: str


@pytest.mark.parametrize(
    "scenario",
    [
        SyncScenario(
            template_content="rule = 1\n",
            published_content="rule = 2\n",
            use_correct_sha=True,
            dry_run=False,
            expected_status=ArtifactStatus.OUTDATED,
            expected_copied=True,
            expected_actions_count=1,
            expected_final_content="rule = 1\n",
        ),
        SyncScenario(
            template_content="rule = 2\n",
            published_content="rule = 1\n",
            use_correct_sha=True,
            dry_run=True,
            expected_status=ArtifactStatus.OUTDATED,
            expected_copied=False,
            expected_actions_count=1,
            expected_final_content="rule = 1\n",
        ),
        SyncScenario(
            template_content="rule = 1\n",
            published_content="rule = 2\n",
            use_correct_sha=False,
            dry_run=False,
            expected_status=ArtifactStatus.TEMPLATE_MANIFEST_MISMATCH,
            expected_copied=None,
            expected_actions_count=0,
            expected_final_content="rule = 2\n",
        ),
    ],
    ids=["updates_published", "dry_run_does_not_modify", "manifest_mismatch_skipped"],
)
def test_sync_behavior_scenarios(
    single_artifact_manifest: typ.Callable[..., ManifestFixture],
    scenario: SyncScenario,
) -> None:
    """Sync handles various scenarios: normal updates, dry-run, and mismatch."""
    setup = single_artifact_manifest(
        template_content=scenario.template_content,
        use_correct_sha=scenario.use_correct_sha,
    )
    published_file = _create_published_file(
        setup.published_root,
        setup.template_file.relative_to(setup.root).as_posix(),
        scenario.published_content,
    )

    comparisons = list(
        compare_manifest_to_published(
            setup.manifest, published_root=setup.published_root
        )
    )
    assert comparisons[0].status == scenario.expected_status

    actions = sync_artifacts(
        comparisons,
        SyncConfig(
            template_root=setup.manifest.template_root,
            published_root=setup.published_root,
            ids={setup.artifact_id},
            dry_run=scenario.dry_run,
        ),
    )

    # Check actions count
    assert len(actions) == scenario.expected_actions_count

    # Check copied flag if actions were returned
    if scenario.expected_actions_count > 0 and scenario.expected_copied is not None:
        assert actions[0].copied is scenario.expected_copied

    # Verify final file state
    assert published_file.read_text(encoding="utf-8") == scenario.expected_final_content


def test_sync_missing_artifact_creates_destination(
    single_artifact_manifest: typ.Callable[..., ManifestFixture],
) -> None:
    """sync_artifacts recreates a missing artifact and its path."""
    setup = single_artifact_manifest()
    comparisons = list(
        compare_manifest_to_published(
            setup.manifest, published_root=setup.published_root
        )
    )
    assert comparisons[0].status == ArtifactStatus.MISSING

    actions = sync_artifacts(
        comparisons,
        SyncConfig(
            template_root=setup.manifest.template_root,
            published_root=setup.published_root,
            ids={setup.artifact_id},
        ),
    )
    assert actions
    published_file = setup.published_root / "canon" / "lint" / "python" / "ruff.toml"
    assert published_file.is_file()
    assert published_file.read_text(encoding="utf-8") == "rule = 1\n"


def test_sync_include_unchanged_controls_ok_inclusion(tmp_path: Path) -> None:
    """include_unchanged controls whether OK artifacts are included in sync."""
    manifest, published_root = _setup_multi_artifact_scenario(
        tmp_path,
        [
            (
                "outdated",
                "platform-standards/canon/lint/python/outdated.toml",
                "rule = 2\n",
                "rule = 1\n",
            ),
            (
                "ok",
                "platform-standards/canon/lint/python/ok.toml",
                "rule = 3\n",
                "rule = 3\n",
            ),
        ],
    )

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

    with pytest.raises(
        CanonArtifactsError,
        match=re.escape(f"Manifest content must be a mapping: {manifest_path}"),
    ):
        load_manifest(manifest_path)


def test_load_manifest_rejects_missing_file(tmp_path: Path) -> None:
    """load_manifest rejects missing manifest files."""
    manifest_path = tmp_path / "missing.yaml"

    with pytest.raises(
        CanonArtifactsError,
        match=re.escape(f"Manifest not found: {manifest_path}"),
    ):
        load_manifest(manifest_path)


def test_load_manifest_rejects_wrong_schema_version(tmp_path: Path) -> None:
    """load_manifest rejects unexpected schema versions."""
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("schema_version: 2\nartifacts: []\n", encoding="utf-8")

    with pytest.raises(
        CanonArtifactsError,
        match=re.escape(
            f"Unsupported manifest schema_version=2 (expected 1): {manifest_path}"
        ),
    ):
        load_manifest(manifest_path)


def test_load_manifest_rejects_empty_artifacts_list(tmp_path: Path) -> None:
    """load_manifest rejects empty artifact lists."""
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text("schema_version: 1\nartifacts: []\n", encoding="utf-8")

    with pytest.raises(
        CanonArtifactsError,
        match=re.escape(
            f"Manifest artifacts must be a non-empty list: {manifest_path}"
        ),
    ):
        load_manifest(manifest_path)


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

    with pytest.raises(
        CanonArtifactsError,
        match=re.escape(f"Manifest artifact entries must be mappings: {manifest_path}"),
    ):
        load_manifest(manifest_path)


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

    with pytest.raises(
        CanonArtifactsError,
        match=re.escape(f"Manifest artifact missing key 'sha256': {manifest_path}"),
    ):
        load_manifest(manifest_path)


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


def test_render_status_table_contains_expected_cells(
    single_artifact_manifest: typ.Callable[..., ManifestFixture],
) -> None:
    """render_status_table includes a header and comparison row values."""
    setup = single_artifact_manifest()
    comparisons = list(
        compare_manifest_to_published(
            setup.manifest, published_root=setup.published_root
        )
    )
    table = render_status_table(comparisons)
    assert "id" in table.splitlines()[0]
    assert "python-ruff-config" in table
    assert "missing" in table
