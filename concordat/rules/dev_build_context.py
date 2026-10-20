"""Resolve repository-owned Cargo configuration and toolchain context.

Discovery stays inside the checkout. Configuration precedence, workspace
ownership, and ambient override refusals are resolved before storage facts
are normalized by the development-build envelope.
"""

from __future__ import annotations

import dataclasses
import pathlib
import tomllib
import typing as typ

from .cargo_config import locate_cargo_config, rustflags_cargo_would_refuse
from .toolchain import inspect_toolchain

if typ.TYPE_CHECKING:
    from .rust_surfaces import CargoSurface


@dataclasses.dataclass(slots=True)
class DevBuildContext:
    """Merged development settings and the evidence limiting their authority."""

    dev: dict[str, object]
    config: dict[str, object] = dataclasses.field(default_factory=dict)
    channel: str | None = None
    errors: list[str] = dataclasses.field(default_factory=list)


def toml_table(value: object) -> dict[str, object]:
    """Select a TOML table without accepting scalar configuration as a mapping.

    Returns
    -------
    dict[str, object]
        The original table, or an empty mapping for a non-table value.
    """
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _merge(lower: dict[str, object], higher: dict[str, object]) -> dict[str, object]:
    """Merge Cargo configuration, appending arrays and replacing scalars."""
    merged = dict(lower)
    for key, value in higher.items():
        previous = merged.get(key)
        match previous, value:
            case dict(), dict():
                merged[key] = _merge(toml_table(previous), toml_table(value))
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


def _dev_profile(document: dict[str, object], errors: list[str]) -> dict[str, object]:
    """Select a valid development-profile table, without swallowing bad shapes."""
    profiles = document.get("profile", {})
    dev = toml_table(profiles).get("dev", {})
    if not isinstance(profiles, dict) or not isinstance(dev, dict):
        errors.append("profile and profile.dev must be tables")
    if "inherits" in toml_table(dev):
        errors.append("the built-in dev profile cannot inherit another profile")
    return toml_table(dev)


def _ancestors(checkout: pathlib.Path, directory: pathlib.Path) -> list[pathlib.Path]:
    """Return repository-owned Cargo discovery directories, outermost first."""
    return [
        checkout,
        *(
            checkout / pathlib.Path(*directory.relative_to(checkout).parts[:i])
            for i in range(1, len(directory.relative_to(checkout).parts) + 1)
        ),
    ]


def _check_workspace(
    checkout: pathlib.Path, surface: CargoSurface, errors: list[str]
) -> None:
    """Reject member profiles whose governing workspace was not selected."""
    if toml_table(surface["parsed"].get("package")).get("workspace") is not None:
        errors.append("declare the governing workspace named by package.workspace")
    if "workspace" in surface["parsed"]:
        return
    directory = (checkout / surface["path"]).parent
    for parent in _ancestors(checkout, directory)[:-1]:
        if "workspace" in _document(parent / "Cargo.toml", checkout, errors):
            errors.append("declare the governing workspace, not its member manifest")


def _merge_config(
    parent: pathlib.Path, checkout: pathlib.Path, context: DevBuildContext
) -> None:
    """Apply one discovery directory's Cargo configuration at its precedence."""
    location = locate_cargo_config(parent)
    if location.read_error is not None:
        context.errors.append(location.read_error)
    if location.path is None:
        return
    document = _document(location.path, checkout, context.errors)
    refusal = rustflags_cargo_would_refuse(document)
    if refusal is not None:
        context.errors.append(refusal)
    context.dev = _merge(context.dev, _dev_profile(document, context.errors))
    context.config = _merge(context.config, document)


def _select_toolchain(
    parent: pathlib.Path, checkout: pathlib.Path, context: DevBuildContext
) -> None:
    """Select the nearest pin without consulting a host installation."""
    pin = parent / "rust-toolchain.toml"
    if not (pin.exists() or pin.is_symlink()):
        if (parent / "rust-toolchain").exists():
            context.channel = None
        return
    if not _contained(pin, checkout):
        context.errors.append("rust-toolchain.toml resolves outside the checkout")
        return
    toolchain = inspect_toolchain(parent)
    context.channel = toolchain["channel"] if toolchain is not None else None
    if toolchain is not None and toolchain["parse_error"] is not None:
        context.errors.append(toolchain["parse_error"])


def _is_build_environment_override(key: str) -> bool:
    """Identify environment settings that supersede the static Cargo facts."""
    if key in {
        "RUSTFLAGS",
        "CARGO_ENCODED_RUSTFLAGS",
        "CARGO_INCREMENTAL",
        "CARGO_BUILD_INCREMENTAL",
        "CARGO_BUILD_RUSTFLAGS",
    }:
        return True
    if key.startswith("CARGO_PROFILE_DEV_"):
        return True
    return key.startswith("CARGO_TARGET_") and key.endswith("_RUSTFLAGS")


def resolve_dev_context(
    checkout: pathlib.Path, surface: CargoSurface
) -> DevBuildContext:
    """Collect the repository configuration governing one default dev build.

    Returns
    -------
    DevBuildContext
        Merged settings, selected toolchain channel, and measurement errors.
    """
    errors: list[str] = []
    context = DevBuildContext(
        dev=_dev_profile(surface["parsed"], errors), errors=errors
    )
    _check_workspace(checkout, surface, errors)
    directory = (checkout / surface["path"]).parent
    for parent in _ancestors(checkout, directory):
        _merge_config(parent, checkout, context)
        _select_toolchain(parent, checkout, context)
    if any(map(_is_build_environment_override, toml_table(context.config.get("env")))):
        errors.append("Cargo env overrides prevent a static development-profile proof")
    return context
