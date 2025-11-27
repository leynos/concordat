"""File persistence helpers for backend and manifest artifacts."""
# ruff: noqa: TRY003

from __future__ import annotations

import dataclasses
import typing as typ

from .models import PersistenceError, PersistenceFiles, PersistenceResult, _yaml

if typ.TYPE_CHECKING:
    from pathlib import Path


@dataclasses.dataclass(frozen=True)
class FileIO:
    """Reader/writer pair for persistence file operations."""

    reader: typ.Callable[[Path], object]
    writer: typ.Callable[[Path, object], None]

    @staticmethod
    def default() -> FileIO:
        """Return default text reader/writer."""
        return FileIO(
            reader=lambda target: target.read_text(encoding="utf-8"),
            writer=lambda target, payload: target.write_text(
                typ.cast("str", payload), encoding="utf-8"
            ),
        )


def _write_if_changed(
    path: Path,
    contents: object,
    *,
    force: bool,
    io: FileIO | None = None,
) -> bool:
    """Write contents if changed; enforce overwrite policy when different."""
    file_io = io or FileIO.default()
    read = file_io.reader
    write = file_io.writer

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        current = read(path)
        should_write = _enforce_existing_policy(
            path,
            is_same=current == contents,
            force=force,
        )
        if not should_write:
            return False
    write(path, contents)
    return True


def _write_manifest_if_changed(
    path: Path,
    contents: dict[str, typ.Any],
    *,
    force: bool,
) -> bool:
    """Serialise manifest contents to YAML when changed."""

    def _dump_yaml(target: Path, payload: object) -> None:
        manifest = typ.cast("dict[str, typ.Any]", payload)
        with target.open("w", encoding="utf-8") as handle:
            _yaml.dump(manifest, handle)

    manifest_io = FileIO(
        reader=lambda target: dict(
            _yaml.load(target.read_text(encoding="utf-8")) or {}
        ),
        writer=_dump_yaml,
    )

    return _write_if_changed(
        path,
        contents,
        force=force,
        io=manifest_io,
    )


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
    if updated := _write_files(files, force=force):
        return None
    if not updated:
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
