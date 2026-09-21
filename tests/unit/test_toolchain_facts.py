"""Unit tests for the pinned Rust toolchain reading.

Two clauses turn on this string, and both are direction-sensitive: a stable
pin must make the parallel-frontend clause inapplicable rather than
noncompliant, and a channel this reader cannot place must be reported as
unknown rather than guessed at.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.toolchain import (
    CHANNEL_BETA,
    CHANNEL_NIGHTLY,
    CHANNEL_NIGHTLY_DATED,
    CHANNEL_STABLE,
    CHANNEL_UNKNOWN,
    CHANNEL_VERSION,
    classify_channel,
    inspect_toolchain,
)

if typ.TYPE_CHECKING:
    import pathlib


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        pytest.param("nightly-2026-08-23", CHANNEL_NIGHTLY_DATED, id="dated-nightly"),
        pytest.param("nightly", CHANNEL_NIGHTLY, id="bare-nightly"),
        pytest.param("beta", CHANNEL_BETA, id="beta"),
        pytest.param("beta-2026-01-02", CHANNEL_BETA, id="dated-beta"),
        pytest.param("stable", CHANNEL_STABLE, id="stable"),
        pytest.param("1.93.1", CHANNEL_VERSION, id="version"),
        pytest.param("nightly-2026-08", CHANNEL_UNKNOWN, id="truncated-date"),
        pytest.param("my-custom-toolchain", CHANNEL_UNKNOWN, id="custom"),
    ],
)
def test_channel_spellings_are_classified(channel: str, expected: str) -> None:
    """Each rustup spelling the estate uses maps to one classification."""
    assert classify_channel(channel) == expected


def test_an_unpinned_checkout_has_no_toolchain_facts(
    tmp_path: pathlib.Path,
) -> None:
    """An absent file is not a parse failure and must not read as one."""
    assert inspect_toolchain(tmp_path) is None


def test_a_comment_naming_the_channel_is_not_the_channel(
    tmp_path: pathlib.Path,
) -> None:
    """The reader parses for this reason: netsuke's file opens with such a comment."""
    (tmp_path / "rust-toolchain.toml").write_text(
        "# See the ADR before changing the channel to anything but nightly.\n"
        '[toolchain]\nchannel = "1.93.1"\n',
        encoding="utf-8",
    )
    facts = inspect_toolchain(tmp_path)
    assert facts is not None
    assert facts["channel"] == "1.93.1"
    assert facts["channel_kind"] == CHANNEL_VERSION


def test_malformed_toml_is_reported_rather_than_raised(
    tmp_path: pathlib.Path,
) -> None:
    """A pin that cannot be read fails closed with the reason attached."""
    (tmp_path / "rust-toolchain.toml").write_text("[toolchain\n", encoding="utf-8")
    facts = inspect_toolchain(tmp_path)
    assert facts is not None
    assert facts["parse_error"] is not None
    assert facts["channel"] is None


def test_a_file_without_a_channel_key_reports_none(
    tmp_path: pathlib.Path,
) -> None:
    """A components-only file pins no channel, which is not a parse failure."""
    (tmp_path / "rust-toolchain.toml").write_text(
        '[toolchain]\ncomponents = ["clippy"]\n', encoding="utf-8"
    )
    facts = inspect_toolchain(tmp_path)
    assert facts is not None
    assert facts["channel"] is None
    assert facts["parse_error"] is None
    assert facts["channel_kind"] == CHANNEL_UNKNOWN
