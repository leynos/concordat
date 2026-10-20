"""Read repository-owned Cargo defaults without executing Cargo or rustc.

The policy needs the profile after Cargo configuration precedence, not a grep
for ``debug = 0``. Package overrides and replacing rustflags remain separate
facts so a small base profile cannot hide a larger effective development build.
"""

from __future__ import annotations

import datetime as dt
import itertools
import typing as typ

from .cargo_config import (
    normalise_flags,
    rustflags_sources,
)
from .dev_build_context import resolve_dev_context, toml_table
from .rust_surfaces import CargoSurface, resolve_rust_surfaces
from .toolchain import classify_channel

if typ.TYPE_CHECKING:
    import pathlib

ENVELOPE_KIND: typ.Final = "policy-input/rust-dev-build-footprint"
# The 1.88.0 compiler source contains this option; 1.87.0 does not. This is
# a conservative support floor, not a claim about the first introducing nightly.
METADATA_SUPPORT_FLOOR: typ.Final = dt.date(2025, 6, 26)
type SettingState = typ.Literal["disabled", "enabled", "unset", "invalid"]
_DEBUG_LEVELS: typ.Final[dict[str, SettingState]] = {
    "none": "disabled",
    "limited": "enabled",
    "full": "enabled",
    "line-tables-only": "enabled",
    "line-directives-only": "enabled",
}
_NUMERIC_DEBUG_LEVELS: typ.Final[dict[int, SettingState]] = {
    0: "disabled",
    1: "enabled",
    2: "enabled",
}
_FLAG_LEVELS: typ.Final[dict[str, dict[str, SettingState]]] = {
    "-Cdebuginfo": _DEBUG_LEVELS | {"0": "disabled", "1": "enabled", "2": "enabled"},
    "-Zembed-metadata": {
        "no": "disabled",
        "false": "disabled",
        "yes": "enabled",
        "true": "enabled",
    },
}


class ProfileFact(typ.TypedDict):
    """A base profile or override, with Cargo's valid settings normalized."""

    source: str
    incremental: SettingState
    debug: SettingState


class FlagFact(typ.TypedDict):
    """Effective storage-related options within one replacing flag source."""

    source: str
    incremental: SettingState
    debug: SettingState
    metadata: SettingState
    metadata_required: bool


class SurfaceFacts(typ.TypedDict):
    """One governed default build and anything preventing its measurement."""

    path: str
    profiles: list[ProfileFact]
    flags: list[FlagFact]
    metadata_support: str
    errors: list[str]


class DevBuildEnvelope(typ.TypedDict):
    """The offline input consumed by the development-footprint policy."""

    schema_version: int
    kind: str
    surfaces: list[SurfaceFacts]


def _state(value: object, setting: str) -> SettingState:
    """Classify only Cargo's documented profile-value types and spellings."""
    if type(value) is bool:
        return "enabled" if value else "disabled"
    if setting != "debug":
        return "invalid"
    if type(value) is int:
        return _NUMERIC_DEBUG_LEVELS.get(value, "invalid")
    if isinstance(value, str):
        return _DEBUG_LEVELS.get(value, "invalid")
    return "invalid"


def _profile(
    source: str, table: dict[str, object], *, base: bool = False
) -> ProfileFact:
    """Preserve the difference between Cargo defaults and inherited overrides."""
    defaults = {"incremental": True, "debug": True} if base else {}
    effective = defaults | table
    return {
        "source": source,
        "incremental": _state(effective["incremental"], "incremental")
        if "incremental" in effective
        else "unset",
        "debug": _state(effective["debug"], "debug")
        if "debug" in effective
        else "unset",
    }


def _profile_tables(
    dev: dict[str, object], errors: list[str]
) -> list[tuple[str, dict[str, object]]]:
    """Collect base, package and build-script tables without conflating names."""
    tables = [("profile.dev", dev)]
    packages = dev.get("package", {})
    if not isinstance(packages, dict):
        errors.append("profile.dev.package must be a table")
    overrides = [
        (f"profile.dev.package.{name}", table)
        for name, table in toml_table(packages).items()
    ]
    if "build-override" in dev:
        overrides.append(("profile.dev.build-override", dev["build-override"]))
    for source, table in overrides:
        if not isinstance(table, dict):
            errors.append(f"{source} must be a table")
        else:
            tables.append((source, toml_table(table)))
    return tables


def _profiles(
    dev: dict[str, object], config: dict[str, object], errors: list[str]
) -> list[ProfileFact]:
    """Retain overrides, accounting for Cargo's global incremental setting."""
    facts = [
        _profile(source, table, base=source == "profile.dev")
        for source, table in _profile_tables(dev, errors)
    ]
    build = toml_table(config.get("build"))
    if "incremental" in build:
        for fact in facts:
            fact["incremental"] = _state(build["incremental"], "incremental")
    return facts


def _flag_state(flags: list[str], option: str) -> SettingState:
    """Use rustc's last assignment, including one which undoes an earlier flag."""
    values = [
        flag[len(option) + 1 :] for flag in flags if flag.startswith(option + "=")
    ]
    if option in flags:
        return "invalid"
    if not values:
        return "unset"
    value = values[-1]
    if option == "-Cincremental":
        return "enabled" if value else "invalid"
    return _FLAG_LEVELS[option].get(value, "invalid")


def _flag_fact(name: str, value: object, *, metadata_required: bool = True) -> FlagFact:
    """Normalize compiler settings while preserving additive flag sources."""
    flags = [
        "-Cdebuginfo=2" if flag == "-g" else flag for flag in normalise_flags(value)
    ]
    return {
        "source": name,
        "incremental": _flag_state(flags, "-Cincremental"),
        "debug": _flag_state(flags, "-Cdebuginfo"),
        "metadata": _flag_state(flags, "-Zembed-metadata"),
        "metadata_required": metadata_required,
    }


def _flags(
    config: dict[str, object], dev: dict[str, object], errors: list[str]
) -> list[FlagFact]:
    """Read replacing build/target flags and additive profile flags."""
    sources = [
        (source["name"], source["flags"]) for source in rustflags_sources(config)
    ]
    if not any(name == "build" for name, _ in sources):
        sources.insert(0, ("build", []))
    facts = list(itertools.starmap(_flag_fact, sources))
    return [*facts, *_profile_flags(dev, errors)]


def _profile_flags(dev: dict[str, object], errors: list[str]) -> list[FlagFact]:
    """Read additive profile options without imposing replacing-source metadata."""
    facts: list[FlagFact] = []
    for source, table in _profile_tables(dev, errors):
        if "rustflags" not in table:
            continue
        value = table["rustflags"]
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            errors.append(f"{source}.rustflags must be an array of strings")
        else:
            facts.append(_flag_fact(source, value, metadata_required=False))
    return facts


def _support(channel: str | None) -> str:
    """Use a documented capability floor instead of probing the host compiler."""
    if channel is None:
        return "unknown"
    kind = classify_channel(channel)
    if kind in {"stable", "beta", "version"}:
        return "unsupported"
    if kind == "nightly-dated":
        try:
            date = dt.date.fromisoformat(channel.removeprefix("nightly-"))
        except ValueError:
            return "unknown"
        if date >= METADATA_SUPPORT_FLOOR:
            return "supported"
    return "unknown"


def _surface(checkout: pathlib.Path, surface: CargoSurface) -> SurfaceFacts:
    """Normalize storage facts after resolving repository configuration."""
    context = resolve_dev_context(checkout, surface)
    return {
        "path": surface["path"],
        "profiles": _profiles(context.dev, context.config, context.errors),
        "flags": _flags(context.config, context.dev, context.errors),
        "metadata_support": _support(context.channel),
        "errors": context.errors,
    }


def build_dev_build_envelope(
    checkout: pathlib.Path, _parameters: object = None
) -> DevBuildEnvelope:
    """Collect development-build facts for every declared Rust surface.

    Returns
    -------
    DevBuildEnvelope
        Repository-only facts, independent of host environment and compilers.
    """
    resolution = resolve_rust_surfaces(checkout)
    return {
        "schema_version": 1,
        "kind": ENVELOPE_KIND,
        "surfaces": [_surface(checkout, surface) for surface in resolution.surfaces],
    }
