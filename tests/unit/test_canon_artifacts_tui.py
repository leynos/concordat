"""Behavioural tests for the canonical-artifacts Textual application."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import DataTable

from concordat.canon_artifacts import (
    ArtifactComparison,
    ArtifactStatus,
    CanonArtifact,
    CanonManifest,
)
from scripts import canon_artifacts_tui


def _comparison(identifier: str) -> ArtifactComparison:
    """Build a comparison displayed by the Textual application."""
    artifact = CanonArtifact(
        id=identifier,
        type="lint-config",
        path=Path("canon/lint/python/ruff.toml"),
        description="Ruff configuration",
        sha256="a" * 64,
    )
    return ArtifactComparison(
        artifact=artifact,
        template_sha256="b" * 64,
        published_sha256=None,
        status=ArtifactStatus.MISSING,
        published_path=Path("canon/lint/python/ruff.toml"),
    )


@pytest.mark.asyncio
async def test_refresh_action_recomputes_mounted_application_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The public refresh binding replaces the mounted table's comparisons."""
    initial = [_comparison("initial")]
    refreshed = [_comparison("first"), _comparison("second")]
    comparison_sets = iter((initial, refreshed))
    monkeypatch.setattr(
        canon_artifacts_tui,
        "compare_manifest_to_published",
        lambda *_, **__: next(comparison_sets),
    )
    app = canon_artifacts_tui.CanonArtifactsApp(
        manifest=CanonManifest(
            schema_version=1,
            artifacts=(),
            manifest_path=tmp_path / "canon" / "manifest.yaml",
        ),
        published_root=tmp_path,
        ids=None,
        types=None,
    )

    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        assert table.row_count == 1, "refresh test requires one initial table row"

        await pilot.press("r")

        assert table.row_count == 2, "refresh should replace the table with two rows"
        assert [table.get_row_at(index)[0] for index in range(table.row_count)] == [
            "first",
            "second",
        ], "refresh should display the recomputed comparison IDs"
