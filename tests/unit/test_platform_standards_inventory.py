"""Tests for the platform-standards inventory helpers."""

from __future__ import annotations

import typing as typ

from concordat import platform_standards

if typ.TYPE_CHECKING:
    from pathlib import Path
else:  # pragma: no cover - runtime fallback
    Path = typ.Any


def test_update_inventory_adds_entry(tmp_path: Path) -> None:
    """Add a repository when it is not present."""
    inventory = tmp_path / "repositories.yaml"
    added = platform_standards._update_inventory(inventory, "example/repo")
    assert added is True
    contents = inventory.read_text(encoding="utf-8")
    assert "example/repo" in contents


def test_update_inventory_idempotent(tmp_path: Path) -> None:
    """Second insertion of the same repository becomes a no-op."""
    inventory = tmp_path / "repositories.yaml"
    assert platform_standards._update_inventory(inventory, "example/repo") is True
    assert platform_standards._update_inventory(inventory, "example/repo") is False
