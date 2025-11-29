"""File persistence helpers for backend and manifest artifacts."""
# ruff: noqa: TRY003

from __future__ import annotations

import typing as typ

from .models import PersistenceError, PersistenceFiles, PersistenceResult, _yaml

if typ.TYPE_CHECKING:
    from pathlib import Path


def _write_if_changed(
    path: Path,
    contents: object,
    *,
    force: bool,
) -> bool:
    """Write contents if changed; enforce overwrite policy when different."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        current = path.read_text(encoding="utf-8")
        should_write = _enforce_existing_policy(
            path,
            is_same=current == contents,
            force=force,
        )
        if not should_write:
            return False
    path.write_text(typ.cast("str", contents), encoding="utf-8")
    return True


def _write_manifest_if_changed(
    path: Path,
    contents: dict[str, typ.Any],
    *,
    force: bool,
) -> bool:
    """Serialise manifest contents to YAML when changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        current = dict(_yaml.load(path.read_text(encoding="utf-8")) or {})
        should_write = _enforce_existing_policy(
            path,
            is_same=current == contents,
            force=force,
        )
        if not should_write:
            return False
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(contents, handle)
    return True


def _write_files(
    files: PersistenceFiles,
    *,
    force: bool,
) -> bool:
    """Write backend and manifest files if changed."""
    backend_changed = _write_if_changed(
        files.backend_path,
        files.backend_contents,
        force=force,
    )
    manifest_changed = _write_manifest_if_changed(
        files.manifest_path,
        files.manifest_contents,
        force=force,
    )
    return backend_changed or manifest_changed


def _write_files_and_check_for_changes(
    files: PersistenceFiles,
    *,
    force: bool,
) -> PersistenceResult | None:
    """Write backend and manifest files; return early result if unchanged."""
    if not _write_files(files, force=force):
        return PersistenceResult(
            backend_path=files.backend_path,
            manifest_path=files.manifest_path,
            branch=None,
            pr_url=None,
            updated=False,
            message="backend already configured",
        )
    return None


def _enforce_existing_policy(
    path: Path,
    *,
    is_same: bool,
    force: bool,
) -> bool:
    """Return True if caller should write, False if identical, else raise.

    When ``is_same`` is False and ``force`` is False, raises a PersistenceError
    to protect existing files from accidental overwrite.
    """
    if is_same:
        return False
    if not force:
        raise PersistenceError(f"{path} already exists; rerun with --force to replace.")
    return True
