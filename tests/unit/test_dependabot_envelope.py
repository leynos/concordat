"""Specify the Dependabot configuration facts for the DB-005 policy."""

from __future__ import annotations

import os
import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules.dependabot_envelope import (
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    OPERATION_READ_ACTIONS,
    build_dependabot_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

_CONFIG = """\
version: 2
updates:
  - package-ecosystem: cargo
    directory: /
    schedule:
      interval: daily
    groups:
      rstest-bdd:
        patterns: ['rstest-bdd*']
      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
  - package-ecosystem: uv
    directory: /
    schedule:
      interval: daily
"""


def _write(checkout: pathlib.Path, relative: str, text: str) -> pathlib.Path:
    """Write *text* at *relative* under *checkout* and return the path."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_checkout_without_configuration_has_none(tmp_path: pathlib.Path) -> None:
    """No configuration and no actions is an envelope with nothing to judge."""
    envelope = build_dependabot_envelope(tmp_path)

    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert envelope["kind"] == ENVELOPE_KIND
    assert envelope["config"] is None
    assert envelope["action_directories"] == []


@pytest.mark.parametrize("name", ["dependabot.yml", "dependabot.yaml"])
def test_the_configuration_is_decoded_with_its_group_order(
    tmp_path: pathlib.Path, name: str
) -> None:
    """Each entry's group names keep document order, which Rego cannot see.

    Dependabot assigns a dependency to the first group that matches it, so
    the order is part of the configuration's meaning.
    """
    _write(tmp_path, f".github/{name}", _CONFIG)

    config = build_dependabot_envelope(tmp_path)["config"]

    assert config is not None
    assert config["path"] == f".github/{name}"
    assert config["error"] is None
    assert config["group_order"] == [["rstest-bdd", "minor-and-patch"], None]
    assert isinstance(config["parsed"], dict)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("updates: [", "invalid YAML"),
        ("- a list\n", "configuration is not a mapping"),
        ("updates: .nan\n", "configuration is not JSON-safe"),
    ],
)
def test_an_undecodable_configuration_keeps_its_reason(
    tmp_path: pathlib.Path, text: str, reason: str
) -> None:
    """A content failure is evidence for an indeterminate verdict, not absence."""
    _write(tmp_path, ".github/dependabot.yml", text)

    config = build_dependabot_envelope(tmp_path)["config"]

    assert config is not None
    assert config["parsed"] is None
    assert reason in str(config["error"]), config


def test_a_non_utf8_configuration_keeps_its_reason(tmp_path: pathlib.Path) -> None:
    """Undecodable bytes are one file's content error, not an operational one."""
    path = tmp_path / ".github" / "dependabot.yml"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe")

    config = build_dependabot_envelope(tmp_path)["config"]

    assert config is not None
    assert "not UTF-8" in str(config["error"]), config


def test_both_spellings_are_reported_rather_than_chosen(
    tmp_path: pathlib.Path,
) -> None:
    """GitHub reads one file, and the audit cannot tell which."""
    _write(tmp_path, ".github/dependabot.yml", _CONFIG)
    _write(tmp_path, ".github/dependabot.yaml", _CONFIG)

    config = build_dependabot_envelope(tmp_path)["config"]

    assert config is not None
    assert config["parsed"] is None
    assert "both" in str(config["error"]), config


def test_a_linked_configuration_is_reported(tmp_path: pathlib.Path) -> None:
    """A symbolic link is not this repository's configuration to read."""
    target = _write(tmp_path, "elsewhere.yml", _CONFIG)
    link = tmp_path / ".github" / "dependabot.yml"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)

    config = build_dependabot_envelope(tmp_path)["config"]

    assert config is not None
    assert config["error"] == "configuration file is a symlink", config


def test_action_directories_are_listed_at_any_depth(tmp_path: pathlib.Path) -> None:
    """Every directory holding an action manifest is listed, `/`-rooted.

    A directory without a manifest is a container, not an action, and is
    not listed.
    """
    _write(tmp_path, ".github/actions/setup/action.yml", "name: a\n")
    _write(tmp_path, ".github/actions/rust/build/action.yaml", "name: b\n")
    _write(tmp_path, ".github/actions/README.md", "notes\n")

    envelope = build_dependabot_envelope(tmp_path)

    assert envelope["action_directories"] == [
        "/.github/actions/rust/build",
        "/.github/actions/setup",
    ]


def test_a_linked_action_directory_is_not_followed(tmp_path: pathlib.Path) -> None:
    """A link could leave the checkout, so the walk does not follow it."""
    _write(tmp_path, "shared/action.yml", "name: a\n")
    actions = tmp_path / ".github" / "actions"
    actions.mkdir(parents=True)
    (actions / "shared").symlink_to(tmp_path / "shared")

    assert build_dependabot_envelope(tmp_path)["action_directories"] == []


def test_an_actions_directory_outside_the_checkout_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A linked `.github/actions` would let another tree decide the verdict."""
    outside = tmp_path / "outside"
    _write(outside, "setup/action.yml", "name: a\n")
    checkout = tmp_path / "checkout"
    (checkout / ".github").mkdir(parents=True)
    (checkout / ".github" / "actions").symlink_to(outside)

    with pytest.raises(OperationalRuleError) as caught:
        build_dependabot_envelope(checkout)

    assert caught.value.operation == OPERATION_READ_ACTIONS
    assert "outside the checkout" in str(caught.value)


def test_an_actions_path_that_is_a_file_is_refused(tmp_path: pathlib.Path) -> None:
    """An occupied path is not an absent directory."""
    _write(tmp_path, ".github/actions", "not a directory\n")

    with pytest.raises(OperationalRuleError) as caught:
        build_dependabot_envelope(tmp_path)

    assert caught.value.operation == OPERATION_READ_ACTIONS


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads unreadable directories")
def test_an_unreadable_action_directory_is_operational(
    tmp_path: pathlib.Path,
) -> None:
    """A directory the walk cannot list must not read as one with no actions."""
    locked = tmp_path / ".github" / "actions" / "locked"
    _write(locked, "action.yml", "name: a\n")
    locked.chmod(0)
    try:
        with pytest.raises(OperationalRuleError) as caught:
            build_dependabot_envelope(tmp_path)
    finally:
        locked.chmod(0o755)

    assert caught.value.operation == OPERATION_READ_ACTIONS
