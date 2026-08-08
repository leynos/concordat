"""Unit tests for the git-side estate helpers in `concordat.estate_git`.

Template bootstrapping and inventory decoding are exercised directly here; the
public clone and bootstrap paths are covered end to end by the `init_estate`
suite. The bootstrap seam is still reached through `estate_repository`, which
is the lookup site the rest of the suite patches.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat import estate, estate_git, estate_repository

if typ.TYPE_CHECKING:
    import pathlib


def test_bootstrap_template_requires_an_existing_template_root(
    tmp_path: pathlib.Path,
) -> None:
    """An absent template root is rejected before any Git work begins."""
    missing = tmp_path / "absent-template"
    bootstrap = estate_repository.TemplateBootstrap(
        branch="main",
        template_root=missing,
        inventory_path="tofu/inventory/repositories.yaml",
        callbacks=None,
    )

    with pytest.raises(estate.TemplateMissingError) as exc_info:
        estate_repository._bootstrap_template(
            "git@github.com:example/core.git", bootstrap
        )

    assert str(missing) in str(exc_info.value), str(exc_info.value)


class TestInventoryUrls:
    """Decoding of an estate's inventory into enrolled repository URLs.

    The public clone path is covered end to end by the `init_estate` suite;
    these exercise the filtering, normalisation, and ordering directly.
    """

    @staticmethod
    def _inventory(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
        path = tmp_path / "repositories.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_invalid_entries_are_ignored(self, tmp_path: pathlib.Path) -> None:
        """Only mapping entries carrying a non-blank string name are kept."""
        inventory = self._inventory(
            tmp_path,
            "schema_version: 1\n"
            "repositories:\n"
            "  - name: example/one\n"
            "  - not-a-mapping\n"
            "  - name: 7\n"
            "  - name: ''\n"
            "  - name: '   '\n"
            "  - other_key: example/ignored\n",
        )

        urls = estate_git._inventory_urls(inventory)
        assert urls == ["git@github.com:example/one.git"], urls

    def test_duplicate_and_padded_names_collapse(self, tmp_path: pathlib.Path) -> None:
        """Names are stripped before deduplication, yielding one URL."""
        inventory = self._inventory(
            tmp_path,
            "repositories:\n"
            "  - name: example/one\n"
            "  - name: '  example/one  '\n"
            "  - name: example/one\n",
        )

        urls = estate_git._inventory_urls(inventory)
        assert urls == ["git@github.com:example/one.git"], urls

    def test_urls_are_sorted(self, tmp_path: pathlib.Path) -> None:
        """Inventory order does not leak into the returned URLs."""
        inventory = self._inventory(
            tmp_path,
            "repositories:\n"
            "  - name: example/zulu\n"
            "  - name: example/alpha\n"
            "  - name: example/mike\n",
        )

        urls = estate_git._inventory_urls(inventory)
        assert urls == sorted(urls), urls
        assert urls == [
            "git@github.com:example/alpha.git",
            "git@github.com:example/mike.git",
            "git@github.com:example/zulu.git",
        ], urls

    def test_qualified_urls_are_preserved(self, tmp_path: pathlib.Path) -> None:
        """Already-qualified SSH and HTTPS remotes pass through untouched."""
        inventory = self._inventory(
            tmp_path,
            "repositories:\n"
            "  - name: git@github.com:example/ssh.git\n"
            "  - name: https://github.com/example/https.git\n"
            "  - name: example/bare\n",
        )

        urls = estate_git._inventory_urls(inventory)
        assert urls == [
            "git@github.com:example/bare.git",
            "git@github.com:example/ssh.git",
            "https://github.com/example/https.git",
        ], urls

    @pytest.mark.parametrize(
        "repositories",
        [
            pytest.param("5", id="integer-scalar"),
            pytest.param('"example/one"', id="string-scalar"),
            pytest.param("{name: example/one}", id="mapping"),
            pytest.param("null", id="null"),
        ],
    )
    def test_malformed_repositories_read_as_empty(
        self,
        tmp_path: pathlib.Path,
        repositories: str,
    ) -> None:
        """A `repositories` value that is not a list yields no URLs.

        A scalar previously raised on iteration and a bare string was walked
        character by character, so neither reached the mapping pattern; both
        now read as an empty inventory instead.
        """
        inventory = self._inventory(tmp_path, f"repositories: {repositories}\n")

        assert estate_git._inventory_urls(inventory) == [], repositories

    @pytest.mark.parametrize(
        "document",
        [
            pytest.param("5\n", id="scalar-number"),
            pytest.param("just a string\n", id="scalar-string"),
            pytest.param("- alpha\n- beta\n", id="list-document"),
        ],
    )
    def test_a_non_mapping_document_reads_as_empty(
        self,
        tmp_path: pathlib.Path,
        document: str,
    ) -> None:
        """A document root that is not a mapping yields no URLs.

        `or {}` rescues only a falsy decode, so a truthy scalar or list
        reached `.get` and raised `AttributeError` — an inventory that is not
        a mapping simply has no repositories.
        """
        inventory = self._inventory(tmp_path, document)

        assert estate_git._inventory_urls(inventory) == [], document
