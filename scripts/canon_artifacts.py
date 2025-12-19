"""Maintain canonical artifacts in platform-standards checkouts.

This utility compares `platform-standards/canon/manifest.yaml` in the Concordat
repository against a checked-out platform-standards repository and can sync
outdated or missing artifacts.
"""

# ruff: noqa: TRY003

from __future__ import annotations

import dataclasses
from pathlib import Path  # noqa: TC003
from typing import Annotated  # noqa: ICN003

from cyclopts import App, Parameter

from concordat.canon_artifacts import (
    DEFAULT_MANIFEST_RELATIVE,
    ArtifactComparison,
    ArtifactStatus,
    CanonArtifactsError,
    SyncConfig,
    compare_manifest_to_published,
    load_manifest,
    render_status_table,
    resolve_concordat_root,
    sync_artifacts,
)

app = App()


@dataclasses.dataclass(frozen=True, slots=True)
class _Filter:
    ids: set[str] | None
    types: set[str] | None


@dataclasses.dataclass(frozen=True, slots=True)
class StatusConfig:
    """Configuration for the status command."""

    published_root: Path
    template_root: Path | None = None
    ids: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    outdated_only: bool = False
    fail_on_outdated: bool = False
    fail_on_missing: bool = False


def _build_filter(*, ids: tuple[str, ...], types: tuple[str, ...]) -> _Filter:
    ids_filter = set(ids) if ids else None
    types_filter = set(types) if types else None
    return _Filter(ids=ids_filter, types=types_filter)


def _resolve_manifest_path(template_root: Path | None) -> Path:
    root = template_root or resolve_concordat_root()
    return root / DEFAULT_MANIFEST_RELATIVE


@app.command(name="list")
def list_artifacts(
    *,
    template_root: Path | None = None,
    types: tuple[str, ...] = (),
) -> None:
    """List canonical artifacts registered in the manifest."""
    manifest_path = _resolve_manifest_path(template_root)
    manifest = load_manifest(manifest_path)
    types_filter = set(types) if types else None
    for artifact in manifest.artifacts:
        if types_filter is not None and artifact.type not in types_filter:
            continue
        print(
            f"{artifact.id}\t{artifact.type}\t{artifact.published_relpath().as_posix()}"
        )


@app.command()
def status(
    config: Annotated[StatusConfig, Parameter(name="*")],
) -> int:
    """Print a table comparing published artifacts against the template."""
    return _render_status(config)


def _render_status(
    config: StatusConfig,
) -> int:
    """Render status table and return the appropriate exit code."""
    manifest_path = _resolve_manifest_path(config.template_root)
    manifest = load_manifest(manifest_path)
    filters = _build_filter(ids=config.ids, types=config.types)
    comparisons = list(
        compare_manifest_to_published(
            manifest,
            published_root=config.published_root,
            ids=filters.ids,
            types=filters.types,
        )
    )
    if config.outdated_only:
        comparisons = [c for c in comparisons if c.status != ArtifactStatus.OK]

    print(render_status_table(comparisons))
    return _compute_status_exit_code(
        comparisons,
        fail_on_outdated=config.fail_on_outdated,
        fail_on_missing=config.fail_on_missing,
    )


def _compute_status_exit_code(
    comparisons: list[ArtifactComparison],
    *,
    fail_on_outdated: bool,
    fail_on_missing: bool,
) -> int:
    """Compute exit code based on comparison results."""
    if _has_manifest_mismatch(comparisons):
        return 3

    if fail_on_missing and _has_missing_artifacts(comparisons):
        return 2
    if fail_on_outdated and _has_outdated_artifacts(comparisons):
        return 2
    return 0


def _has_manifest_mismatch(comparisons: list[ArtifactComparison]) -> bool:
    """Return True when comparisons include a template manifest mismatch."""
    return any(
        comparison.status == ArtifactStatus.TEMPLATE_MANIFEST_MISMATCH
        for comparison in comparisons
    )


def _has_missing_artifacts(comparisons: list[ArtifactComparison]) -> bool:
    """Return True when comparisons include missing artifacts."""
    return any(
        comparison.status == ArtifactStatus.MISSING for comparison in comparisons
    )


def _has_outdated_artifacts(comparisons: list[ArtifactComparison]) -> bool:
    """Return True when comparisons include outdated artifacts."""
    return any(
        comparison.status == ArtifactStatus.OUTDATED for comparison in comparisons
    )


@app.command()
def sync(
    published_root: Path,
    *artifact_ids: str,
    template_root: Path | None = None,
    types: tuple[str, ...] = (),
    dry_run: bool = False,
    all_outdated: bool = False,
    include_unchanged: bool = False,
) -> int:
    """Copy template artifacts into the published checkout."""
    manifest_path = _resolve_manifest_path(template_root)
    manifest = load_manifest(manifest_path)
    filters = _build_filter(ids=tuple(artifact_ids), types=types)

    comparisons = list(
        compare_manifest_to_published(
            manifest,
            published_root=published_root,
            ids=filters.ids,
            types=filters.types,
        )
    )
    selected_ids = filters.ids
    if all_outdated:
        selected_ids = {
            c.id
            for c in comparisons
            if c.status in {ArtifactStatus.MISSING, ArtifactStatus.OUTDATED}
        }
    if not selected_ids:
        raise CanonArtifactsError(
            "No artifacts selected for sync. Pass explicit IDs or use --all-outdated."
        )

    actions = sync_artifacts(
        comparisons,
        SyncConfig(
            template_root=manifest.template_root,
            published_root=published_root,
            ids=selected_ids,
            dry_run=dry_run,
            include_unchanged=include_unchanged,
        ),
    )
    for action in actions:
        verb = "would copy" if dry_run else "copied"
        print(
            f"{verb}\t{action.status}\t{action.artifact_id}\t"
            f"{action.source.relative_to(manifest.template_root)}\t"
            f"{action.destination.relative_to(published_root)}"
        )
    return 0


@app.command()
def tui(
    published_root: Path,
    *,
    template_root: Path | None = None,
    ids: tuple[str, ...] = (),
    types: tuple[str, ...] = (),
) -> None:
    """Interactively review and sync artifacts via a Textual TUI."""
    try:
        from scripts.canon_artifacts_tui import CanonArtifactsApp
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise CanonArtifactsError(
            "Textual is required for `tui`. Install dev dependencies via "
            "`make build` or `uv sync --group dev`."
        ) from exc

    manifest_path = _resolve_manifest_path(template_root)
    manifest = load_manifest(manifest_path)
    filters = _build_filter(ids=ids, types=types)
    app_ = CanonArtifactsApp(
        manifest=manifest,
        published_root=published_root,
        ids=filters.ids,
        types=filters.types,
    )
    app_.run()


def main() -> int:  # pragma: no cover - exercised via CLI
    """Entrypoint for `python -m scripts.canon_artifacts`."""
    try:
        result = app()
    except CanonArtifactsError as error:
        print(f"canon_artifacts: {error}")
        return 1
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
