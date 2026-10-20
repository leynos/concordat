"""Read repository-owned Cargo defaults without executing Cargo or rustc.

The policy needs the profile after Cargo configuration precedence, not a grep
for ``debug = 0``. Package overrides and replacing rustflags remain separate
facts so a small base profile cannot hide a larger effective development build.
"""

from __future__ import annotations

import datetime as dt
import itertools
import pathlib
import tomllib
import typing as typ

from .cargo_config import (
    locate_cargo_config,
    normalise_flags,
    rustflags_cargo_would_refuse,
    rustflags_sources,
)
from .rust_surfaces import CargoSurface, resolve_rust_surfaces
from .toolchain import classify_channel, inspect_toolchain

ENVELOPE_KIND: typ.Final = "policy-input/rust-dev-build-footprint"
# The 1.88.0 compiler source contains this option; 1.87.0 does not. This is
# a conservative support floor, not a claim about the first introducing nightly.
METADATA_SUPPORT_FLOOR: typ.Final = dt.date(2025, 6, 26)
type SettingState = typ.Literal["disabled", "enabled", "unset", "invalid"]


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


def _table(value: object) -> dict[str, object]:
    """Return a TOML table, or an empty mapping for a non-table value."""
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _merge(lower: dict[str, object], higher: dict[str, object]) -> dict[str, object]:
    """Merge Cargo configuration, appending arrays and replacing scalars."""
    merged = dict(lower)
    for key, value in higher.items():
        previous = merged.get(key)
        match previous, value:
            case dict(), dict():
                merged[key] = _merge(_table(previous), _table(value))
            case list(), list():
                merged[key] = [*previous, *value]
            case _:
                merged[key] = value
    return merged


def _contained(path: pathlib.Path, checkout: pathlib.Path) -> bool:
    """Keep ambient host files and symlink escapes outside the audit."""
    return path.resolve().is_relative_to(checkout.resolve())


def _document(
    path: pathlib.Path, checkout: pathlib.Path, errors: list[str]
) -> dict[str, object]:
    """Read one contained TOML document, retaining failures as input facts."""
    try:
        if not _contained(path, checkout):
            errors.append(f"{path.name} resolves outside the checkout")
            return {}
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        if path.is_symlink():
            errors.append(f"{path.name} is a dangling symlink")
    except (OSError, ValueError, RuntimeError) as error:
        errors.append(f"cannot read {path.name}: {error}")
    return {}


def _dev_table(document: dict[str, object], errors: list[str]) -> dict[str, object]:
    """Select a valid development-profile table, without swallowing bad shapes."""
    profiles = document.get("profile", {})
    dev = _table(profiles).get("dev", {})
    if not isinstance(profiles, dict) or not isinstance(dev, dict):
        errors.append("profile and profile.dev must be tables")
    if "inherits" in _table(dev):
        errors.append("the built-in dev profile cannot inherit another profile")
    return _table(dev)


def _state(value: object, setting: str) -> SettingState:
    """Classify only Cargo's documented profile-value types and spellings."""
    if value is False:
        return "disabled"
    if value is True:
        return "enabled"
    if setting == "debug":
        if type(value) is int and value in {0, 1, 2}:
            return "disabled" if value == 0 else "enabled"
        if isinstance(value, str) and value in {
            "none",
            "limited",
            "full",
            "line-tables-only",
            "line-directives-only",
        }:
            return "disabled" if value == "none" else "enabled"
    return "invalid"


def _profile(
    source: str, table: dict[str, object], *, base: bool = False
) -> ProfileFact:
    """Preserve the difference between Cargo defaults and inherited overrides."""
    return {
        "source": source,
        "incremental": _state(table.get("incremental", True), "incremental")
        if base or "incremental" in table
        else "unset",
        "debug": _state(table.get("debug", True), "debug")
        if base or "debug" in table
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
        for name, table in _table(packages).items()
    ]
    if "build-override" in dev:
        overrides.append(("profile.dev.build-override", dev["build-override"]))
    for source, table in overrides:
        if not isinstance(table, dict):
            errors.append(f"{source} must be a table")
        else:
            tables.append((source, _table(table)))
    return tables


def _profiles(
    dev: dict[str, object], config: dict[str, object], errors: list[str]
) -> list[ProfileFact]:
    """Retain overrides, accounting for Cargo's global incremental setting."""
    facts = [
        _profile(source, table, base=source == "profile.dev")
        for source, table in _profile_tables(dev, errors)
    ]
    build = _table(config.get("build"))
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
    disabled = {"0", "none"} if option == "-Cdebuginfo" else {"no", "false"}
    enabled = (
        {"1", "2", "limited", "full", "line-tables-only", "line-directives-only"}
        if option == "-Cdebuginfo"
        else {"yes", "true"}
    )
    if value in disabled:
        return "disabled"
    return "enabled" if value in enabled else "invalid"


def _flag_fact(name: str, value: object, *, metadata_required: bool = True) -> FlagFact:
    """Normalize compiler settings while preserving additive flag sources."""
    flags = normalise_flags(value)
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


def _ancestors(checkout: pathlib.Path, directory: pathlib.Path) -> list[pathlib.Path]:
    """Return repository-owned Cargo discovery directories, outermost first."""
    return [
        checkout,
        *(
            checkout / pathlib.Path(*directory.relative_to(checkout).parts[:i])
            for i in range(1, len(directory.relative_to(checkout).parts) + 1)
        ),
    ]


def _surface(checkout: pathlib.Path, surface: CargoSurface) -> SurfaceFacts:
    """Resolve one declared workspace's repository-owned development defaults."""
    errors: list[str] = []
    directory = (checkout / surface["path"]).parent
    directories = _ancestors(checkout, directory)
    dev = _dev_table(surface["parsed"], errors)
    config: dict[str, object] = {}
    channel: str | None = None
    for parent in directories:
        if parent != directory and "workspace" not in surface["parsed"]:
            ancestor = _document(parent / "Cargo.toml", checkout, errors)
            if "workspace" in ancestor:
                errors.append(
                    "declare the governing workspace, not its member manifest"
                )
        location = locate_cargo_config(parent)
        if location.read_error is not None:
            errors.append(location.read_error)
        if location.path is not None:
            document = _document(location.path, checkout, errors)
            refusal = rustflags_cargo_would_refuse(document)
            if refusal is not None:
                errors.append(refusal)
            dev = _merge(dev, _dev_table(document, errors))
            config = _merge(config, document)
        pin = parent / "rust-toolchain.toml"
        if pin.exists() or pin.is_symlink():
            if _contained(pin, checkout):
                toolchain = inspect_toolchain(parent)
                channel = toolchain["channel"] if toolchain is not None else None
                if toolchain is not None and toolchain["parse_error"] is not None:
                    errors.append(toolchain["parse_error"])
            else:
                errors.append("rust-toolchain.toml resolves outside the checkout")
        elif (parent / "rust-toolchain").exists():
            channel = None
    if _table(surface["parsed"].get("package")).get("workspace") is not None:
        errors.append("declare the governing workspace named by package.workspace")
    environment = _table(config.get("env"))
    if any(
        key
        in {
            "RUSTFLAGS",
            "CARGO_ENCODED_RUSTFLAGS",
            "CARGO_INCREMENTAL",
            "CARGO_BUILD_INCREMENTAL",
            "CARGO_BUILD_RUSTFLAGS",
        }
        or key.startswith("CARGO_PROFILE_DEV_")
        or (key.startswith("CARGO_TARGET_") and key.endswith("_RUSTFLAGS"))
        for key in environment
    ):
        errors.append("Cargo env overrides prevent a static development-profile proof")
    profiles = _profiles(dev, config, errors)
    flags = _flags(config, dev, errors)
    return {
        "path": surface["path"],
        "profiles": profiles,
        "flags": flags,
        "metadata_support": _support(channel),
        "errors": errors,
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
