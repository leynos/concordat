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


def test_update_inventory_preserves_extra_top_level_keys(tmp_path: Path) -> None:
    """Extra top-level keys in inventory are preserved after update."""
    from ruamel.yaml import YAML

    inventory = tmp_path / "repositories.yaml"
    original_contents = """\
schema_version: 1
metadata:
  owner: team-a
  environment: production
labels:
  - backend
  - critical
repositories:
  - name: existing/repo
"""
    inventory.write_text(original_contents, encoding="utf-8")

    added = platform_standards._update_inventory(inventory, "example/repo")
    assert added is True

    yaml = YAML(typ="safe")
    data = yaml.load(inventory.read_text(encoding="utf-8"))

    assert data["schema_version"] == 1
    assert data["metadata"] == {"owner": "team-a", "environment": "production"}
    assert data["labels"] == ["backend", "critical"]

    repo_names = [r["name"] for r in data["repositories"]]
    assert "existing/repo" in repo_names
    assert "example/repo" in repo_names


def test_update_inventory_sorts_repositories_by_name(tmp_path: Path) -> None:
    """Repositories are deterministically sorted by name after update."""
    from ruamel.yaml import YAML

    inventory = tmp_path / "repositories.yaml"
    original_contents = """\
schema_version: 1
repositories:
  - name: z-repo/last
  - name: m-repo/middle
  - name: a-repo/first
"""
    inventory.write_text(original_contents, encoding="utf-8")

    platform_standards._update_inventory(inventory, "example/repo")

    yaml = YAML(typ="safe")
    data = yaml.load(inventory.read_text(encoding="utf-8"))

    repo_names = [r["name"] for r in data["repositories"]]
    assert repo_names == sorted(repo_names)


def test_remove_inventory_preserves_extra_top_level_keys(tmp_path: Path) -> None:
    """Extra top-level keys in inventory are preserved after removal."""
    from ruamel.yaml import YAML

    inventory = tmp_path / "repositories.yaml"
    original_contents = """\
schema_version: 1
metadata:
  owner: team-a
  environment: production
labels:
  - backend
  - critical
repositories:
  - name: existing/repo
  - name: to-remove/repo
"""
    inventory.write_text(original_contents, encoding="utf-8")

    removed = platform_standards._remove_inventory(inventory, "to-remove/repo")
    assert removed is True

    yaml = YAML(typ="safe")
    data = yaml.load(inventory.read_text(encoding="utf-8"))

    assert data["schema_version"] == 1
    assert data["metadata"] == {"owner": "team-a", "environment": "production"}
    assert data["labels"] == ["backend", "critical"]

    repo_names = [r["name"] for r in data["repositories"]]
    assert "existing/repo" in repo_names
    assert "to-remove/repo" not in repo_names


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
