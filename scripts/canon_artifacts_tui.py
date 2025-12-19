"""Textual TUI for managing canonical artifact sync."""

from __future__ import annotations

import typing as typ
from pathlib import Path  # noqa: TC003

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header

from concordat.canon_artifacts import (
    ArtifactComparison,
    ArtifactStatus,
    CanonManifest,
    compare_manifest_to_published,
    sync_artifacts,
)


class CanonArtifactsApp(App[None]):
    """Interactive view for canonical artifacts and one-shot syncing."""

    BINDINGS: typ.ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("s", "sync_selected", "Sync selected"),
        Binding("a", "sync_all_outdated", "Sync all outdated"),
    ]

    def __init__(
        self,
        *,
        manifest: CanonManifest,
        published_root: Path,
        ids: set[str] | None,
        types: set[str] | None,
    ) -> None:
        """Capture manifest/published roots and optional filters."""
        super().__init__()
        self._manifest = manifest
        self._published_root = published_root
        self._ids = ids
        self._types = types
        self._comparisons: list[ArtifactComparison] = []
        self._table = DataTable(zebra_stripes=True)

    def compose(self) -> ComposeResult:
        """Build the UI layout."""
        yield Header()
        yield self._table
        yield Footer()

    def on_mount(self) -> None:
        """Populate the table on startup."""
        self._table.add_columns("id", "type", "status", "template", "published", "path")
        self._refresh()

    def action_refresh(self) -> None:
        """Reload the manifest comparison and update the table."""
        self._refresh()

    def action_sync_selected(self) -> None:
        """Sync the currently highlighted artifact when it is missing/outdated."""
        row = self._table.cursor_row
        if row < 0 or row >= len(self._comparisons):
            return
        comparison = self._comparisons[row]
        if comparison.status in {
            ArtifactStatus.OK,
            ArtifactStatus.TEMPLATE_MANIFEST_MISMATCH,
        }:
            return
        sync_artifacts(
            [comparison],
            template_root=self._manifest.template_root,
            published_root=self._published_root,
            ids={comparison.id},
        )
        self._refresh()

    def action_sync_all_outdated(self) -> None:
        """Sync every missing/outdated artifact currently shown."""
        outdated_ids = {
            c.id
            for c in self._comparisons
            if c.status in {ArtifactStatus.MISSING, ArtifactStatus.OUTDATED}
        }
        if not outdated_ids:
            return
        sync_artifacts(
            self._comparisons,
            template_root=self._manifest.template_root,
            published_root=self._published_root,
            ids=outdated_ids,
        )
        self._refresh()

    def _refresh(self) -> None:
        """Recompute comparisons and rewrite the table rows."""
        comparisons = list(
            compare_manifest_to_published(
                self._manifest,
                published_root=self._published_root,
                ids=self._ids,
                types=self._types,
            )
        )
        comparisons.sort(key=lambda c: (c.status != ArtifactStatus.OK, c.type, c.id))
        self._comparisons = comparisons
        self._table.clear()
        for comparison in comparisons:
            self._table.add_row(
                comparison.id,
                comparison.type,
                str(comparison.status),
                comparison.template_sha256[:12],
                (comparison.published_sha256 or "-")[:12],
                comparison.artifact.published_relpath().as_posix(),
            )
