"""Resolve the governed Rust Cargo surfaces for a repository checkout.

The resolver is the shared applicability boundary for Rust rule packages.  A
repository may declare its governed Cargo manifests in `.concordat`; that list
is authoritative, including an explicit empty list.  Until a declaration is
present, the historic root-`Cargo.toml` fallback remains available so existing
repositories retain their current audit behaviour.
"""

from __future__ import annotations

import pathlib
import tomllib
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

CONCORDAT_FILENAME: typ.Final = ".concordat"
MANIFEST_FILENAME: typ.Final = "Cargo.toml"
OPERATION_RESOLVE_SURFACES: typ.Final = "resolve-rust-surfaces"
OPERATION_PARSE_CARGO: typ.Final = "parse-cargo-toml"
SURFACE_ROLES: typ.Final = frozenset({"workspace", "crate"})

CargoManifest = dict[str, object]


class CargoSurface(typ.TypedDict):
    """One resolved, governed Cargo manifest."""

    path: str
    role: str
    parsed: CargoManifest


class RustSurfaceResolution(typ.NamedTuple):
    """The declared-state marker and its resolved Cargo surfaces."""

    declared: bool
    surfaces: tuple[CargoSurface, ...]


def _resolution_error(
    message: str,
    manifest_path: pathlib.Path,
) -> OperationalRuleError:
    """Create a surface-resolution error with a stable operation boundary."""
    return OperationalRuleError(
        message,
        operation=OPERATION_RESOLVE_SURFACES,
        resource=manifest_path,
    )


def _as_mapping(
    value: object, label: str, manifest_path: pathlib.Path
) -> dict[str, object]:
    """Return a YAML mapping or report the malformed declaration boundary."""
    if not isinstance(value, dict):
        message = f"{label} in {manifest_path} must be a mapping"
        raise _resolution_error(message, manifest_path)
    return typ.cast("dict[str, object]", value)


def _load_manifest(manifest_path: pathlib.Path) -> dict[str, object] | None:
    """Load `.concordat`, retaining absence as distinct from an empty document."""
    if not manifest_path.is_file():
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8")
        loaded: object = YAML(typ="safe").load(text)
    except (OSError, UnicodeDecodeError, ValueError, YAMLError) as error:
        message = f"cannot parse {manifest_path}: {error}"
        raise _resolution_error(message, manifest_path) from error
    if loaded is None:
        return {}
    return _as_mapping(loaded, "the .concordat document", manifest_path)


def _declared_surface_entries(
    manifest: dict[str, object] | None,
    manifest_path: pathlib.Path,
) -> list[object] | None:
    """Return the declared list, preserving absence at every nesting level."""
    if manifest is None:
        return None
    language = manifest.get("language")
    if language is None:
        return None
    language_mapping = _as_mapping(language, "language", manifest_path)
    rust = language_mapping.get("rust")
    if rust is None:
        return None
    rust_mapping = _as_mapping(rust, "language.rust", manifest_path)
    if "surfaces" not in rust_mapping:
        return None
    surfaces = rust_mapping["surfaces"]
    if not isinstance(surfaces, list):
        message = f"language.rust.surfaces in {manifest_path} must be a list"
        raise _resolution_error(message, manifest_path)
    return typ.cast("list[object]", surfaces)


def _surface_path(
    entry: object,
    manifest_path: pathlib.Path,
) -> tuple[str, str | None]:
    """Validate one declaration and return its relative path and role override."""
    mapping = _as_mapping(entry, "a language.rust.surfaces entry", manifest_path)
    path = mapping.get("path")
    if not isinstance(path, str):
        message = f"a language.rust.surfaces entry in {manifest_path} needs a path"
        raise _resolution_error(message, manifest_path)
    relative_path = pathlib.PurePosixPath(path)
    is_safe_relative_path = (
        not relative_path.is_absolute()
        and ".." not in relative_path.parts
        and relative_path.name == MANIFEST_FILENAME
    )
    if not is_safe_relative_path:
        message = (
            "a language.rust.surfaces path must be a repository-relative "
            f"{MANIFEST_FILENAME}: {path!r}"
        )
        raise _resolution_error(message, manifest_path)
    role = mapping.get("role")
    if role is not None and (not isinstance(role, str) or role not in SURFACE_ROLES):
        message = (
            f"a language.rust.surfaces role must be `workspace` or `crate`: {role!r}"
        )
        raise _resolution_error(message, manifest_path)
    return path, role


def _parse_cargo(
    cargo_path: pathlib.Path,
    checkout: pathlib.Path,
    manifest_path: pathlib.Path,
    *,
    is_declared: bool,
) -> CargoManifest:
    """Parse a declared Cargo TOML file with surface-resolution context."""
    try:
        loaded: object = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        relative_path = cargo_path.relative_to(checkout).as_posix()
        if not is_declared:
            message = f"cannot parse {relative_path} in {checkout}: {error}"
            raise OperationalRuleError(
                message,
                operation=OPERATION_PARSE_CARGO,
                resource=cargo_path,
            ) from error
        message = f"cannot parse {relative_path} declared by {manifest_path}: {error}"
        raise _resolution_error(message, manifest_path) from error
    if not isinstance(loaded, dict):
        relative_path = cargo_path.relative_to(checkout).as_posix()
        if not is_declared:
            message = f"{relative_path} in {checkout} did not parse to a table"
            raise OperationalRuleError(
                message,
                operation=OPERATION_PARSE_CARGO,
                resource=cargo_path,
            )
        message = (
            f"{relative_path} declared by {manifest_path} did not parse to a table"
        )
        raise _resolution_error(message, manifest_path)
    return typ.cast("CargoManifest", loaded)


def _resolved_surface(
    checkout: pathlib.Path,
    path: str,
    role_override: str | None,
    manifest_path: pathlib.Path,
    *,
    is_declared: bool,
) -> CargoSurface:
    """Parse one declared or fallback Cargo manifest and infer its role."""
    cargo_path = checkout.joinpath(*pathlib.PurePosixPath(path).parts)
    resolved_checkout = checkout.resolve()
    resolved_cargo_path = cargo_path.resolve()
    if not resolved_cargo_path.is_relative_to(resolved_checkout):
        message = f"a language.rust.surfaces path escapes the checkout: {path!r}"
        raise _resolution_error(message, manifest_path)
    parsed = _parse_cargo(
        cargo_path,
        checkout,
        manifest_path,
        is_declared=is_declared,
    )
    inferred_role = "workspace" if "workspace" in parsed else "crate"
    return {"path": path, "role": role_override or inferred_role, "parsed": parsed}


def resolve_rust_surfaces(checkout: pathlib.Path) -> RustSurfaceResolution:
    """Resolve the governed Rust surfaces for *checkout*.

    A declared list in `.concordat` is authoritative.  In its absence, a root
    `Cargo.toml` is the compatibility fallback; no root file resolves to no
    surfaces so policy can emit the established AP-001 onboarding finding.
    """
    manifest_path = checkout / CONCORDAT_FILENAME
    manifest = _load_manifest(manifest_path)
    entries = _declared_surface_entries(manifest, manifest_path)
    if entries is not None:
        seen_paths: set[str] = set()
        surfaces: list[CargoSurface] = []
        for entry in entries:
            path, role = _surface_path(entry, manifest_path)
            if path in seen_paths:
                message = f"language.rust.surfaces repeats {path!r}"
                raise _resolution_error(message, manifest_path)
            seen_paths.add(path)
            surface = _resolved_surface(
                checkout,
                path,
                role,
                manifest_path,
                is_declared=True,
            )
            surfaces.append(surface)
        return RustSurfaceResolution(declared=True, surfaces=tuple(surfaces))

    root_cargo_path = checkout / MANIFEST_FILENAME
    if not root_cargo_path.is_file():
        return RustSurfaceResolution(declared=False, surfaces=())
    surface = _resolved_surface(
        checkout,
        MANIFEST_FILENAME,
        None,
        manifest_path,
        is_declared=False,
    )
    return RustSurfaceResolution(declared=False, surfaces=(surface,))
