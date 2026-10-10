"""Specify how the revision-list commands refuse a malformed rule manifest."""

from __future__ import annotations

import typing as typ

import pytest

from tests.unit.whitaker_revisions_support import (
    set_default,
    without_key,
)

pytest_plugins = ("tests.unit.whitaker_revisions_fixtures",)


if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib


@pytest.mark.usefixtures("rootless_package")
@pytest.mark.parametrize("command", ["list", "check", "sync"])
def test_a_manifest_with_no_approved_roots_exits_2_for_every_command(
    cli: cabc.Callable[[str], int],
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    """No approved root means nothing to derive from, so the commands refuse."""
    assert cli(command) == 2
    assert "declares no install_whitaker_roots" in capsys.readouterr().err


REQUIRED_KEYS: typ.Final = {
    "list": ("install_whitaker_roots", "action_directory"),
    "check": (
        "install_whitaker_roots",
        "action_directory",
        "compliant_install_whitaker_refs",
    ),
    "sync": ("install_whitaker_roots", "action_directory"),
}


@pytest.mark.parametrize(
    "case",
    [
        pytest.param((command, key), id=f"{command}-without-{key}")
        for command, keys in REQUIRED_KEYS.items()
        for key in keys
    ],
)
def test_a_manifest_missing_a_required_parameter_exits_2_naming_it(
    rule_package: tuple[pathlib.Path, str, str],
    cli: cabc.Callable[[str], int],
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, str],
) -> None:
    """Each command refuses a manifest without a parameter it indexes.

    The diagnostic names the parameter and the manifest, and the command exits
    2 rather than raising a bare `KeyError`.
    """
    command, key = case
    manifest = rule_package[0] / "rule.yaml"
    without_key(manifest, key)

    assert cli(command) == 2
    error = capsys.readouterr().err
    assert key in error
    assert str(manifest) in error


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            ("action_directory", "    action_directory: ''\n"), id="empty-dir"
        ),
        pytest.param(("action_directory", "    action_directory: 7\n"), id="int-dir"),
        pytest.param(
            ("install_whitaker_roots", "    install_whitaker_roots: not-a-list\n"),
            id="roots-not-a-list",
        ),
        pytest.param(
            ("install_whitaker_roots", "    install_whitaker_roots:\n      - 12\n"),
            id="root-not-a-string",
        ),
    ],
)
def test_a_malformed_required_parameter_exits_2_naming_it(
    rule_package: tuple[pathlib.Path, str, str],
    cli: cabc.Callable[[str], int],
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, str],
) -> None:
    """A required parameter of the wrong shape is refused, not misused."""
    key, replacement = case
    set_default(rule_package[0] / "rule.yaml", key, replacement)

    assert cli("list") == 2
    assert key in capsys.readouterr().err
