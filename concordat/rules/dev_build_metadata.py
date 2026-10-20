"""Resolve the native Cargo control that coordinates full metadata emission.

A raw rustc flag can leave build-script dependencies with metadata stubs.
Only Cargo's own unstable setting coordinates the separate full rmeta output.
Capability dates deliberately leave the rename transition unproven.
"""

from __future__ import annotations

import datetime as dt
import typing as typ

from .toolchain import classify_channel

OLD_SETTING: typ.Final = "no-embed-metadata"
NEW_SETTING: typ.Final = "embed-metadata"
_OLD_FLOOR: typ.Final = dt.date(2025, 6, 26)
# Cargo's rename reached nightly distributions after its upstream merge.
# https://static.rust-lang.org/dist/2026-07-18/channel-rust-nightly.toml
# https://static.rust-lang.org/dist/2026-07-19/channel-rust-nightly.toml
_OLD_CEILING: typ.Final = dt.date(2026, 7, 18)
# The boolean Cargo configuration also depends on the distribution roll:
# July 29 and 30 still ship the earlier Cargo; July 31 includes PR 17266.
# https://static.rust-lang.org/dist/2026-07-29/channel-rust-nightly.toml
# https://static.rust-lang.org/dist/2026-07-30/channel-rust-nightly.toml
# https://static.rust-lang.org/dist/2026-07-31/channel-rust-nightly.toml
_NEW_FLOOR: typ.Final = dt.date(2026, 7, 31)


class CargoMetadataFact(typ.TypedDict):
    """The expected native setting and its effective embedding state."""

    setting: str
    state: str


def _setting(channel: str | None) -> str:
    """Select only source-verified Cargo configuration capability windows."""
    if channel is None:
        return "unknown"
    kind = classify_channel(channel)
    if kind in {"stable", "beta", "version"}:
        return "unsupported"
    if kind != "nightly-dated":
        return "unknown"
    try:
        date = dt.date.fromisoformat(channel.removeprefix("nightly-"))
    except ValueError:
        return "unknown"
    if _OLD_FLOOR <= date <= _OLD_CEILING:
        return OLD_SETTING
    return NEW_SETTING if date >= _NEW_FLOOR else "unknown"


def _state(config: dict[str, object], setting: str) -> str:
    """Normalize exact booleans according to the selected Cargo setting."""
    if setting not in {OLD_SETTING, NEW_SETTING}:
        return "unset"
    unstable = config.get("unstable", {})
    if not isinstance(unstable, dict):
        return "invalid"
    settings = typ.cast("dict[str, object]", unstable)
    if setting not in settings:
        return "unset"
    value = settings[setting]
    if type(value) is not bool:
        return "invalid"
    disabled = setting == OLD_SETTING
    return "disabled" if value is disabled else "enabled"


def cargo_metadata_facts(
    channel: str | None, config: dict[str, object]
) -> tuple[str, CargoMetadataFact]:
    """Read metadata coordination from merged repository Cargo configuration.

    Returns
    -------
    tuple[str, CargoMetadataFact]
        Supported, unsupported, or unknown capability and its native control.
    """
    setting = _setting(channel)
    support = "supported" if setting in {OLD_SETTING, NEW_SETTING} else setting
    fact: CargoMetadataFact = {
        "setting": setting if support == "supported" else "",
        "state": _state(config, setting),
    }
    return support, fact
