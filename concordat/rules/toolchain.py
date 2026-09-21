"""Read the pinned Rust toolchain channel from `rust-toolchain.toml`.

Two clauses of the build-defaults rule turn on this one string. The parallel
frontend flag is a nightly flag, so demanding it of a stable pin would break
the build rather than improve it; and a recorded Cranelift exception is only
current while it names the channel the repository is pinned to, which is what
makes the re-test obligation checkable on each toolchain bump.

The channel is read with a TOML parser, never with a textual search. netsuke's
file opens with a comment containing the word `channel`, and a `grep -m1
channel` on it returns a line of prose.
"""

from __future__ import annotations

import re
import tomllib
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

TOOLCHAIN_FILENAME: typ.Final = "rust-toolchain.toml"

CHANNEL_NIGHTLY_DATED: typ.Final = "nightly-dated"
CHANNEL_NIGHTLY: typ.Final = "nightly"
CHANNEL_BETA: typ.Final = "beta"
CHANNEL_STABLE: typ.Final = "stable"
CHANNEL_VERSION: typ.Final = "version"
CHANNEL_UNKNOWN: typ.Final = "unknown"

_DATED_NIGHTLY: typ.Final = re.compile(r"^nightly-\d{4}-\d{2}-\d{2}$")
_DATED_BETA: typ.Final = re.compile(r"^beta-\d{4}-\d{2}-\d{2}$")
_VERSION: typ.Final = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


class ToolchainFacts(typ.TypedDict):
    """The pinned channel, its classification, and any read failure."""

    path: str
    channel: str | None
    channel_kind: str
    parse_error: str | None


def classify_channel(channel: str) -> str:
    """Return the kind of toolchain channel *channel* names.

    Returns
    -------
    str
        One of the ``CHANNEL_*`` constants; ``unknown`` when the spelling is
        not one this reader can place, so the policy can fail closed.
    """
    if _DATED_NIGHTLY.match(channel):
        return CHANNEL_NIGHTLY_DATED
    if channel == CHANNEL_NIGHTLY:
        return CHANNEL_NIGHTLY
    if channel == CHANNEL_BETA or _DATED_BETA.match(channel):
        return CHANNEL_BETA
    if channel == CHANNEL_STABLE:
        return CHANNEL_STABLE
    if _VERSION.match(channel):
        return CHANNEL_VERSION
    return CHANNEL_UNKNOWN


def inspect_toolchain(checkout: pathlib.Path) -> ToolchainFacts | None:
    """Return the toolchain facts for *checkout*, or ``None`` if unpinned.

    Returns
    -------
    ToolchainFacts | None
        The pinned channel and its classification, or ``None`` when the
        checkout has no `rust-toolchain.toml`.
    """
    path = checkout / TOOLCHAIN_FILENAME
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            document: dict[str, object] = tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return {
            "path": TOOLCHAIN_FILENAME,
            "channel": None,
            "channel_kind": CHANNEL_UNKNOWN,
            "parse_error": str(error),
        }
    toolchain = document.get("toolchain")
    channel = None
    if isinstance(toolchain, dict):
        candidate = typ.cast("dict[str, object]", toolchain).get("channel")
        channel = candidate if isinstance(candidate, str) else None
    return {
        "path": TOOLCHAIN_FILENAME,
        "channel": channel,
        "channel_kind": (
            classify_channel(channel) if channel is not None else CHANNEL_UNKNOWN
        ),
        "parse_error": None,
    }
