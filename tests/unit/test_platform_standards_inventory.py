"""Tests for the platform-standards inventory helpers."""

from __future__ import annotations

import typing as typ

import pygit2
import pytest
from ruamel.yaml import YAML

from concordat import platform_standards

if typ.TYPE_CHECKING:
    from pathlib import Path


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


@pytest.mark.parametrize(
    ("mutation_result", "expected_changed", "expected_calls"),
    [
        pytest.param(False, False, ["mutate"], id="unchanged"),
        pytest.param(True, True, ["mutate", "commit", "validate"], id="changed"),
    ],
)
def test_apply_inventory_change_commits_and_validates_only_when_mutated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mutation_result: bool,
    expected_changed: bool,
    expected_calls: list[str],
) -> None:
    """Only commit and validate after an inventory mutation."""
    calls: list[str] = []
    config = platform_standards.PlatformStandardsConfig(
        repo_url="https://example.com/platform-standards.git"
    )

    def mutate_inventory(inventory: Path, repo_slug: str) -> bool:
        calls.append("mutate")
        assert inventory == tmp_path / config.inventory_path
        assert repo_slug == "example/repo"
        return mutation_result

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

    assert changed is expected_changed
    assert calls == expected_calls


def test_apply_inventory_change_commits_the_mutated_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A changed inventory is committed before its validation boundary runs."""
    repository = pygit2.init_repository(str(tmp_path))
    repository.config["user.name"] = "Test User"
    repository.config["user.email"] = "test@example.com"
    config = platform_standards.PlatformStandardsConfig(
        repo_url="https://example.com/platform-standards.git"
    )
    inventory_path = tmp_path / config.inventory_path
    inventory_path.parent.mkdir(parents=True)
    inventory_path.write_text("schema_version: 1\nrepositories: []\n", encoding="utf-8")
    repository.index.add(config.inventory_path)
    repository.index.write()
    signature = repository.default_signature
    base_commit_id = repository.create_commit(
        "HEAD",
        signature,
        signature,
        "seed inventory",
        repository.index.write_tree(),
        [],
    )
    base_commit = repository[base_commit_id].peel(pygit2.Commit)
    validated_heads: list[pygit2.Oid] = []

    def validate_tofu_changes(workdir: Path) -> None:
        assert workdir == tmp_path
        validated_heads.append(repository.head.peel(pygit2.Commit).id)

    monkeypatch.setattr(
        platform_standards, "_validate_tofu_changes", validate_tofu_changes
    )

    changed = platform_standards._apply_inventory_change(
        repository,
        tmp_path,
        config,
        "example/repo",
        base_commit,
        verb="enrol",
        mutate_inventory=platform_standards._update_inventory,
    )

    commit = repository.head.peel(pygit2.Commit)
    assert changed is True
    assert commit.parent_ids == [base_commit.id]
    assert commit.message == "chore: enrol example/repo via concordat"
    assert "example/repo" in inventory_path.read_text(encoding="utf-8")
    assert validated_heads == [commit.id]


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
