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
    assert "%YAML" not in contents
    assert "\n---" not in contents
    assert contents.startswith("schema_version: 1\n")
    assert "\nrepositories:\n" in contents


def test_update_inventory_idempotent(tmp_path: Path) -> None:
    """Second insertion of the same repository becomes a no-op."""
    inventory = tmp_path / "repositories.yaml"
    assert platform_standards._update_inventory(inventory, "example/repo") is True
    assert platform_standards._update_inventory(inventory, "example/repo") is False


def test_remove_inventory_removes_entry(tmp_path: Path) -> None:
    """Remove a repository when it is present."""
    inventory = tmp_path / "repositories.yaml"
    assert platform_standards._update_inventory(inventory, "example/repo") is True
    assert platform_standards._remove_inventory(inventory, "example/repo") is True
    contents = inventory.read_text(encoding="utf-8")
    assert "example/repo" not in contents


def test_remove_inventory_idempotent(tmp_path: Path) -> None:
    """Second removal of the same repository becomes a no-op."""
    inventory = tmp_path / "repositories.yaml"
    assert platform_standards._update_inventory(inventory, "example/repo") is True
    assert platform_standards._remove_inventory(inventory, "example/repo") is True
    assert platform_standards._remove_inventory(inventory, "example/repo") is False


def test_parse_github_slug_preserves_repo_names_ending_in_git_chars() -> None:
    """Slug parsing must only remove the literal `.git` suffix."""
    assert (
        platform_standards.parse_github_slug("git@github.com:leynos/ortho-config")
        == "leynos/ortho-config"
    )
    assert (
        platform_standards.parse_github_slug("https://github.com/leynos/ortho-config")
        == "leynos/ortho-config"
    )

    assert (
        platform_standards.parse_github_slug("git@github.com:leynos/ortho-config.git")
        == "leynos/ortho-config"
    )
    assert (
        platform_standards.parse_github_slug(
            "https://github.com/leynos/ortho-config.git"
        )
        == "leynos/ortho-config"
    )
