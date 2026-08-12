"""Tests for the platform-standards inventory helpers."""

from __future__ import annotations

import typing as typ

import pytest
from ruamel.yaml import YAML

from concordat import platform_standards

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pygit2


def _seed_inventory_with_metadata(inventory: Path, repos: list[str]) -> None:
    """Write an inventory file with schema_version, metadata, labels, and repos."""
    repo_entries = "\n".join(f"  - name: {slug}" for slug in repos)
    contents = f"""\
schema_version: 1
metadata:
  owner: team-a
  environment: production
labels:
  - backend
  - critical
repositories:
{repo_entries}
"""
    inventory.write_text(contents, encoding="utf-8")


def _load_inventory(inventory: Path) -> dict[str, typ.Any]:
    """Load inventory YAML file."""
    yaml = YAML(typ="safe")
    return yaml.load(inventory.read_text(encoding="utf-8"))


def _assert_metadata_preserved(data: dict[str, typ.Any]) -> None:
    """Assert that seeded metadata keys are preserved."""
    assert data["schema_version"] == 1
    assert data["metadata"] == {"owner": "team-a", "environment": "production"}
    assert data["labels"] == ["backend", "critical"]


def test_apply_inventory_change_skips_commit_and_validation_when_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A no-op inventory mutation must not commit or validate changes."""
    calls: list[str] = []
    config = platform_standards.PlatformStandardsConfig(
        repo_url="https://example.com/platform-standards.git"
    )

    def mutate_inventory(inventory: Path, repo_slug: str) -> bool:
        calls.append("mutate")
        assert inventory == tmp_path / config.inventory_path
        assert repo_slug == "example/repo"
        return False

    def commit_inventory_changes(*args: object, **kwargs: object) -> None:
        calls.append("commit")

    def validate_tofu_changes(workdir: Path) -> None:
        calls.append("validate")

    monkeypatch.setattr(
        platform_standards,
        "_commit_inventory_changes",
        commit_inventory_changes,
    )
    monkeypatch.setattr(
        platform_standards,
        "_validate_tofu_changes",
        validate_tofu_changes,
    )

    changed = platform_standards._apply_inventory_change(
        typ.cast("pygit2.Repository", object()),
        tmp_path,
        config,
        "example/repo",
        typ.cast("pygit2.Commit", object()),
        verb="enrol",
        mutate_inventory=mutate_inventory,
    )

    assert changed is False
    assert calls == ["mutate"]


def test_apply_inventory_change_commits_before_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An inventory mutation must be committed before it is validated."""
    calls: list[str] = []
    config = platform_standards.PlatformStandardsConfig(
        repo_url="https://example.com/platform-standards.git"
    )

    def mutate_inventory(inventory: Path, repo_slug: str) -> bool:
        calls.append("mutate")
        assert inventory == tmp_path / config.inventory_path
        assert repo_slug == "example/repo"
        return True

    def commit_inventory_changes(*args: object, **kwargs: object) -> None:
        calls.append("commit")

    def validate_tofu_changes(workdir: Path) -> None:
        assert workdir == tmp_path
        calls.append("validate")

    monkeypatch.setattr(
        platform_standards,
        "_commit_inventory_changes",
        commit_inventory_changes,
    )
    monkeypatch.setattr(
        platform_standards,
        "_validate_tofu_changes",
        validate_tofu_changes,
    )

    changed = platform_standards._apply_inventory_change(
        typ.cast("pygit2.Repository", object()),
        tmp_path,
        config,
        "example/repo",
        typ.cast("pygit2.Commit", object()),
        verb="enrol",
        mutate_inventory=mutate_inventory,
    )

    assert changed is True
    assert calls == ["mutate", "commit", "validate"]


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


@pytest.mark.parametrize(
    ("initial_repos", "mutate", "expected_repos"),
    [
        pytest.param(
            ["existing/repo"],
            lambda inv: platform_standards._update_inventory(inv, "example/repo"),
            {"existing/repo", "example/repo"},
            id="update_adds_repo",
        ),
        pytest.param(
            ["existing/repo", "example/repo"],
            lambda inv: platform_standards._remove_inventory(inv, "example/repo"),
            {"existing/repo"},
            id="remove_deletes_repo",
        ),
    ],
)
def test_inventory_preserves_extra_top_level_keys_on_update_and_remove(
    tmp_path: Path,
    initial_repos: list[str],
    mutate: typ.Callable[[Path], bool],
    expected_repos: set[str],
) -> None:
    """Extra top-level keys in inventory are preserved after update or removal."""
    inventory = tmp_path / "repositories.yaml"
    _seed_inventory_with_metadata(inventory, initial_repos)

    result = mutate(inventory)
    assert result is True

    data = _load_inventory(inventory)
    _assert_metadata_preserved(data)

    repo_names = {r["name"] for r in data["repositories"]}
    assert repo_names == expected_repos


def test_update_inventory_sorts_repositories_by_name(tmp_path: Path) -> None:
    """Repositories are deterministically sorted by name after update."""
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

    data = _load_inventory(inventory)

    repo_names = [r["name"] for r in data["repositories"]]
    assert repo_names == sorted(repo_names)


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
