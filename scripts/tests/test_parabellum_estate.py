"""Unit tests for parsing and validating the Parabellum estate manifest."""

from __future__ import annotations

import typing as typ

import pytest

from scripts import parabellum_sweep as sweep

if typ.TYPE_CHECKING:
    import pathlib


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

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param("", id="empty"),
            pytest.param("7\n", id="scalar-number"),
            pytest.param("just a string\n", id="scalar-string"),
            pytest.param("- alpha\n- beta\n", id="list-document"),
            pytest.param(
                "owner: leynos\nrepositories:\n  - just-a-string\n",
                id="non-mapping-entry",
            ),
            # A string entry containing "name" passes a bare `in` check, since
            # that tests a substring rather than a key, and then leaks an
            # `AttributeError` from `.get`. Only the mapping guard stops it.
            pytest.param(
                "owner: leynos\nrepositories:\n  - my-name-here\n",
                id="string-entry-containing-name",
            ),
            pytest.param(
                "owner: leynos\nrepositories: 5\n",
                id="repositories-scalar",
            ),
            pytest.param(
                'owner: leynos\nrepositories: "abc"\n',
                id="repositories-string",
            ),
            pytest.param(
                "owner: leynos\nrepositories: {a: b}\n",
                id="repositories-mapping",
            ),
            pytest.param(
                "owner: leynos\nrepositories:\n  - {excluded: why}\n",
                id="entry-without-name",
            ),
            pytest.param("repositories: []\n", id="missing-owner"),
            pytest.param("owner: leynos\n", id="missing-repositories"),
            pytest.param(
                "owner: leynos\nrepositories:\n  - {name: gauss, excluded: 7}\n",
                id="non-string-exclusion-reason",
            ),
        ],
    )
    def test_malformed_manifest_shapes_are_rejected(
        self,
        tmp_path: pathlib.Path,
        body: str,
    ) -> None:
        """Every malformed shape becomes an operational error, not a TypeError.

        The manifest is operator-supplied, so indexing it without checking its
        shape turned a bad file into a bare `TypeError` from a subscript —
        untagged, and giving the sweep no repository to blame.
        """
        manifest = self._manifest(tmp_path, body)

        with pytest.raises(sweep.OperationalRuleError) as info:
            sweep.load_estate(manifest)

        assert info.value.operation == "load-estate-manifest", (
            "a malformed manifest should be tagged as a manifest-load failure"
        )
        assert info.value.resource == manifest, (
            "the failure should name the manifest it could not decode"
        )

    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            pytest.param(
                "owner: leynos\nrepositories:\n  - wireframe\n",
                "non-mapping repository entry",
                id="non-mapping-entry",
            ),
            pytest.param(
                "owner: leynos\nrepositories:\n  - {excluded: why}\n",
                "missing key 'name'",
                id="entry-without-name",
            ),
            pytest.param(
                "owner: leynos\nrepositories:\n  - {name: gauss, excluded: 7}\n",
                "non-string exclusion reason",
                id="non-string-exclusion-reason",
            ),
        ],
    )
    def test_malformed_entry_names_its_defect(
        self,
        tmp_path: pathlib.Path,
        body: str,
        fragment: str,
    ) -> None:
        """Each per-entry rejection keeps its own diagnostic wording.

        The shape checks above prove an entry is refused; these pin *why*, so
        moving the per-entry validation cannot quietly collapse three distinct
        defects into one message the operator cannot act on.
        """
        manifest = self._manifest(tmp_path, body)

        with pytest.raises(sweep.OperationalRuleError, match=fragment) as info:
            sweep.load_estate(manifest)

        assert info.value.operation == "load-estate-manifest", info.value.operation
        assert info.value.resource == manifest, info.value.resource

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
