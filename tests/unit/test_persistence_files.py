"""File writing helpers for persistence artefacts."""

from __future__ import annotations

import typing as typ

import pytest

import concordat.persistence.files as persistence_files
import concordat.persistence.models as persistence_models
import concordat.persistence.render as persistence_render
from concordat import persistence

if typ.TYPE_CHECKING:
    from pathlib import Path

    import tests.unit.conftest as persistence_conftest


def test_write_if_changed_respects_force(tmp_path: Path) -> None:
    """Existing files are not overwritten unless --force is supplied."""
    path = tmp_path / "backend" / "core.tfbackend"
    path.parent.mkdir(parents=True)
    path.write_text("original", encoding="utf-8")

    with pytest.raises(persistence.PersistenceError):
        persistence_files._write_if_changed(path, "updated", force=False)

    assert path.read_text(encoding="utf-8") == "original"

    updated = persistence_files._write_if_changed(path, "updated", force=True)
    assert updated
    assert path.read_text(encoding="utf-8") == "updated"


def test_write_if_changed_noop_when_contents_identical(tmp_path: Path) -> None:
    """Rewriting identical contents is a no-op."""
    path = tmp_path / "backend" / "core.tfbackend"
    path.parent.mkdir(parents=True)
    path.write_text("unchanged", encoding="utf-8")

    result = persistence_files._write_if_changed(path, "unchanged", force=False)
    assert result is False
    assert path.read_text(encoding="utf-8") == "unchanged"


def test_write_manifest_if_changed_noop(tmp_path: Path) -> None:
    """Manifest unchanged returns False without writing."""
    path = tmp_path / "backend" / "persistence.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("a: 1\n", encoding="utf-8")
    changed = persistence_files._write_manifest_if_changed(
        path,
        {"a": 1},
        force=False,
    )
    assert changed is False


def test_write_files_handles_conflicts(
    conflict_test_setup: tuple[Path, Path],
    expectation: persistence_conftest.ConflictExpectation,
) -> None:
    """Writing differing contents handles conflicts per force flag."""
    backend_path, manifest_path = conflict_test_setup

    files = persistence.PersistenceFiles(
        backend_path=backend_path,
        backend_contents="new-backend",
        manifest_path=manifest_path,
        manifest_contents={"bucket": "new"},
    )

    if expectation.expect_error:
        with pytest.raises(persistence.PersistenceError):
            persistence_files._write_files(files, force=expectation.force)
    else:
        changed = persistence_files._write_files(files, force=expectation.force)
        assert changed is True

    assert backend_path.read_text(encoding="utf-8") == expectation.expected_backend
    assert (
        persistence_models._yaml.load(manifest_path.read_text(encoding="utf-8"))
        == expectation.expected_manifest
    )


def test_write_files_and_check_returns_unchanged_result(tmp_path: Path) -> None:
    """When files are identical, early result marks workflow unchanged."""
    backend_path = tmp_path / "backend.tfbackend"
    manifest_path = tmp_path / "backend" / "persistence.yaml"
    backend_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    descriptor = persistence.PersistenceDescriptor(
        schema_version=persistence.PERSISTENCE_SCHEMA_VERSION,
        enabled=True,
        bucket="df12",
        key_prefix="estates/example/main",
        key_suffix="terraform.tfstate",
        region="fr-par",
        endpoint="https://s3.fr-par.scw.cloud",
        backend_config_path="backend.tfbackend",
    )
    backend_contents = persistence_render._render_tfbackend(
        descriptor, "terraform.tfstate"
    )
    backend_path.write_text(backend_contents, encoding="utf-8")
    with manifest_path.open("w", encoding="utf-8") as handle:
        persistence_models._yaml.dump(descriptor.to_dict(), handle)

    files = persistence.PersistenceFiles(
        backend_path=backend_path,
        backend_contents=backend_contents,
        manifest_path=manifest_path,
        manifest_contents=descriptor.to_dict(),
    )

    result = persistence_files._write_files_and_check_for_changes(files, force=False)
    assert result is not None
    assert result.updated is False
    assert result.message == "backend already configured"


def test_write_files_and_check_returns_none_when_files_updated(
    tmp_path: Path,
) -> None:
    """When files change, helper writes and returns None."""
    backend_path = tmp_path / "backend.tfbackend"
    manifest_path = tmp_path / "backend" / "persistence.yaml"
    backend_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    files = persistence.PersistenceFiles(
        backend_path=backend_path,
        backend_contents="new-backend",
        manifest_path=manifest_path,
        manifest_contents={"bucket": "new"},
    )

    result = persistence_files._write_files_and_check_for_changes(files, force=False)

    assert result is None
    assert backend_path.read_text(encoding="utf-8") == "new-backend"
    assert persistence_models._yaml.load(manifest_path.read_text(encoding="utf-8")) == {
        "bucket": "new"
    }
