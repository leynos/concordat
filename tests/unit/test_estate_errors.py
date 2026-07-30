"""Unit tests for the estate error taxonomy and its façade re-exports."""

from __future__ import annotations

import typing as typ

from concordat import estate

if typ.TYPE_CHECKING:
    import pathlib


class TestEstateErrorTaxonomy:
    """The exception taxonomy is re-exported from ``concordat.estate``."""

    def test_errors_are_reexported_from_estate(self) -> None:
        """Representative errors remain importable from ``concordat.estate``."""
        from concordat import estate_errors

        for name in (
            "EstateError",
            "MissingGitHubOwnerError",
            "TemplateMissingError",
            "GitHubOrganizationAuthenticationError",
        ):
            assert getattr(estate, name) is getattr(estate_errors, name), (
                f"estate.{name} should be the same object as estate_errors.{name}"
            )

    def test_base_class_inheritance_is_preserved(self) -> None:
        """Moved errors keep their place under ``EstateError``."""
        assert issubclass(estate.MissingGitHubOwnerError, estate.EstateError), (
            "MissingGitHubOwnerError should subclass EstateError"
        )
        assert issubclass(estate.TemplateMissingError, estate.EstateError), (
            "TemplateMissingError should subclass EstateError"
        )
        # Authentication subclasses still descend from GitHubAuthenticationError.
        assert issubclass(
            estate.GitHubOrganizationAuthenticationError,
            estate.GitHubAuthenticationError,
        ), "GitHubOrganizationAuthenticationError should subclass the auth base"

    def test_error_messages_are_preserved(self, tmp_path: pathlib.Path) -> None:
        """Constructor messages are byte-for-byte unchanged after the move."""
        owner_message = str(estate.MissingGitHubOwnerError())
        assert owner_message == (
            "Unable to determine github_owner for the estate. Provide "
            "--github-owner when the remote URL is not a GitHub repository."
        ), f"MissingGitHubOwnerError message must be preserved, got {owner_message!r}"
        template = tmp_path / "tpl"
        template_message = str(estate.TemplateMissingError(template))
        assert template_message == f"Template directory {template} is missing.", (
            f"TemplateMissingError message must be preserved, got {template_message!r}"
        )
