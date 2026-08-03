"""Unit tests for parsing and validating the Parabellum estate manifest."""

from __future__ import annotations

import typing as typ

import pytest

from scripts import parabellum_sweep as sweep

if typ.TYPE_CHECKING:
    import pathlib


ESTATE_YAML = """\
---
schema_version: 1
owner: leynos
repositories:
  - name: wireframe
  - name: gauss
    excluded: test-framework migration in flight
  - name: statelet
"""


@pytest.fixture
def estate_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write a small estate inventory and return its path."""
    path = tmp_path / "estate.yaml"
    path.write_text(ESTATE_YAML)
    return path


class TestLoadEstate:
    """Parsing of the estate inventory."""

    def test_parses_names_and_exclusions(self, estate_path: pathlib.Path) -> None:
        """Names and exclusion reasons round-trip from YAML."""
        estate = sweep.load_estate(estate_path)
        assert estate.owner == "leynos", "estate owner should parse from the manifest"
        names = [entry.name for entry in estate.repositories]
        assert names == ["wireframe", "gauss", "statelet"], (
            "repository names should round-trip from YAML in order"
        )
        assert estate.repositories[1].excluded == (
            "test-framework migration in flight"
        ), "gauss should carry its exclusion reason"
        assert estate.repositories[0].excluded is None, (
            "wireframe should have no exclusion reason"
        )


class TestManifestIdentifiers:
    """Validation of manifest owners and repository names at the boundary."""

    @staticmethod
    def _manifest(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
        path = tmp_path / "estate.yaml"
        path.write_text(body)
        return path

    @pytest.mark.parametrize(
        "owner",
        [
            pytest.param("../evil", id="parent-traversal"),
            pytest.param("a/b", id="path-separator"),
            pytest.param("..", id="dot-dot"),
            pytest.param("-leading", id="leading-hyphen"),
            pytest.param("trailing-", id="trailing-hyphen"),
            pytest.param("''", id="empty"),
        ],
    )
    def test_invalid_owner_is_rejected(
        self,
        tmp_path: pathlib.Path,
        owner: str,
    ) -> None:
        """A manifest owner that is not a GitHub login is refused."""
        manifest = self._manifest(
            tmp_path,
            f"schema_version: 1\nowner: {owner}\nrepositories:\n  - name: wireframe\n",
        )

        with pytest.raises(sweep.OperationalRuleError, match="invalid owner") as info:
            sweep.load_estate(manifest)

        assert info.value.operation == "load-estate-manifest", info.value.operation
        assert info.value.resource == manifest, info.value.resource

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("../escape", id="parent-traversal"),
            pytest.param("nested/name", id="path-separator"),
            pytest.param('"..\\\\windows"', id="backslash"),
            pytest.param("'.'", id="dot"),
            pytest.param("'..'", id="dot-dot"),
            pytest.param("/absolute", id="absolute-path"),
            pytest.param("''", id="empty"),
        ],
    )
    def test_invalid_repository_name_is_rejected(
        self,
        tmp_path: pathlib.Path,
        name: str,
    ) -> None:
        """A repository name that is not a single safe component is refused."""
        manifest = self._manifest(
            tmp_path,
            f"schema_version: 1\nowner: leynos\nrepositories:\n  - name: {name}\n",
        )

        with pytest.raises(
            sweep.OperationalRuleError, match="invalid repository name"
        ) as info:
            sweep.load_estate(manifest)

        assert info.value.operation == "load-estate-manifest", info.value.operation

    def test_valid_manifest_is_accepted(self, tmp_path: pathlib.Path) -> None:
        """Ordinary owners and names, including dots and underscores, parse."""
        manifest = self._manifest(
            tmp_path,
            "schema_version: 1\nowner: leynos\nrepositories:\n"
            "  - name: wireframe\n  - name: some.repo_name-2\n",
        )

        estate = sweep.load_estate(manifest)

        assert estate.owner == "leynos", estate.owner
        assert [e.name for e in estate.repositories] == [
            "wireframe",
            "some.repo_name-2",
        ], estate.repositories
