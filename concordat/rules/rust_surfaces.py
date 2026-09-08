"""Resolve the governed Rust Cargo surfaces for a repository checkout.

The resolver is the shared applicability boundary for Rust rule packages.  A
repository may declare its governed Cargo manifests in `.concordat`; that list
is authoritative, including an explicit empty list.  Until a declaration is
present, the historic root-`Cargo.toml` fallback remains available so existing
repositories retain their current audit behaviour.
"""

from __future__ import annotations

import pathlib
import stat
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

type CargoManifest = dict[str, object]


class CargoSurface(typ.TypedDict):
    """One resolved, governed Cargo manifest."""

    path: str
    role: str
    parsed: CargoManifest


class RustSurfaceResolution(typ.NamedTuple):
    """The declared-state marker and its resolved Cargo surfaces."""

    declared: bool
    surfaces: tuple[CargoSurface, ...]


class SurfaceDeclaration(typ.NamedTuple):
    """One manifest entry after schema validation."""

    path: str
    role: str | None


class SurfaceResolutionContext(typ.NamedTuple):
    """Inputs shared while resolving one declared or fallback surface."""

    checkout: pathlib.Path
    manifest_path: pathlib.Path
    is_declared: bool


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
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError) as error:
        message = f"cannot parse {manifest_path}: {error}"
        raise _resolution_error(message, manifest_path) from error
    try:
        loaded: object = YAML(typ="safe").load(text)
    except YAMLError as error:
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


def _declaration_path(
    mapping: dict[str, object],
    manifest_path: pathlib.Path,
) -> str:
    """Return the safe repository-relative Cargo manifest path."""
    path = mapping.get("path")
    if not isinstance(path, str):
        message = f"a language.rust.surfaces entry in {manifest_path} needs a path"
        raise _resolution_error(message, manifest_path)
    if _contains_control_character(path):
        message = "a language.rust.surfaces path must not contain control characters"
        raise _resolution_error(message, manifest_path)
    relative_path = pathlib.PurePosixPath(path)
    if not _is_safe_manifest_path(relative_path):
        message = (
            "a language.rust.surfaces path must be a repository-relative "
            f"{MANIFEST_FILENAME}: {path!r}"
        )
        raise _resolution_error(message, manifest_path)
    # Preserve one spelling for every accepted manifest.  The envelope and
    # policy use this value as an identity, so equivalent lexical paths must
    # not evade duplicate detection or surface qualification.
    return relative_path.as_posix()


def _contains_control_character(value: str) -> bool:
    """Return whether an untrusted declaration contains an ASCII control code."""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _is_safe_manifest_path(path: pathlib.PurePosixPath) -> bool:
    """Return whether *path* names a Cargo manifest within the checkout."""
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.name == MANIFEST_FILENAME
    )


def _declaration_role(
    mapping: dict[str, object],
    manifest_path: pathlib.Path,
) -> str | None:
    """Return a validated optional role override."""
    role = mapping.get("role")
    if role is None:
        return None
    if isinstance(role, str) and role in SURFACE_ROLES:
        return role
    message = f"a language.rust.surfaces role must be `workspace` or `crate`: {role!r}"
    raise _resolution_error(message, manifest_path)


def _surface_declaration(
    entry: object,
    manifest_path: pathlib.Path,
) -> SurfaceDeclaration:
    """Validate one declared surface entry."""
    mapping = _as_mapping(entry, "a language.rust.surfaces entry", manifest_path)
    return SurfaceDeclaration(
        path=_declaration_path(mapping, manifest_path),
        role=_declaration_role(mapping, manifest_path),
    )


def _parse_cargo(
    cargo_path: pathlib.Path,
    context: SurfaceResolutionContext,
) -> CargoManifest:
    """Parse a declared Cargo TOML file with surface-resolution context."""
    try:
        loaded: object = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        raise _cargo_parse_error(
            cargo_path, context, f"cannot parse: {error}"
        ) from error
    if not isinstance(loaded, dict):
        raise _cargo_parse_error(cargo_path, context, "did not parse to a table")
    return typ.cast("CargoManifest", loaded)


def _cargo_parse_error(
    cargo_path: pathlib.Path,
    context: SurfaceResolutionContext,
    detail: str,
) -> OperationalRuleError:
    """Create the stable error shape for Cargo parsing failures."""
    relative_path = cargo_path.relative_to(context.checkout).as_posix()
    if context.is_declared:
        message = f"{relative_path} declared by {context.manifest_path} {detail}"
        return _resolution_error(message, context.manifest_path)
    message = f"{relative_path} in {context.checkout} {detail}"
    return OperationalRuleError(
        message,
        operation=OPERATION_PARSE_CARGO,
        resource=cargo_path,
    )


def _cargo_path(
    context: SurfaceResolutionContext,
    declaration: SurfaceDeclaration,
) -> pathlib.Path:
    """Return the declared path after rejecting a symbolic-link escape."""
    cargo_path = context.checkout.joinpath(
        *pathlib.PurePosixPath(declaration.path).parts
    )
    if cargo_path.resolve().is_relative_to(context.checkout.resolve()):
        return cargo_path
    message = (
        f"a language.rust.surfaces path escapes the checkout: {declaration.path!r}"
    )
    raise _resolution_error(message, context.manifest_path)


def _resolved_surface(
    context: SurfaceResolutionContext,
    declaration: SurfaceDeclaration,
) -> CargoSurface:
    """Parse one declared or fallback Cargo manifest and infer its role."""
    cargo_path = _cargo_path(context, declaration)
    parsed = _parse_cargo(cargo_path, context)
    inferred_role = "workspace" if "workspace" in parsed else "crate"
    return {
        "path": declaration.path,
        "role": declaration.role or inferred_role,
        "parsed": parsed,
    }


def _root_cargo_toml_exists(cargo_path: pathlib.Path) -> bool:
    """Return whether the root Cargo manifest is a regular file.

    Missing files preserve the established no-Rust fallback. Other filesystem
    failures cannot safely be treated as absence.
    """
    try:
        mode = cargo_path.stat().st_mode
    except FileNotFoundError:
        return False
    except OSError as error:
        message = f"cannot inspect {cargo_path}: {error}"
        raise OperationalRuleError(
            message,
            operation=OPERATION_RESOLVE_SURFACES,
            resource=cargo_path,
        ) from error
    return stat.S_ISREG(mode)


def resolve_rust_surfaces(checkout: pathlib.Path) -> RustSurfaceResolution:
    """Resolve the governed Rust surfaces for *checkout*.

    Parameters
    ----------
    checkout:
        Repository root containing an optional `.concordat` declaration and
        Cargo manifests.

    Returns
    -------
    RustSurfaceResolution
        Whether a declaration was present and every governed, parsed Cargo
        surface. An explicit empty declaration returns no surfaces.

    Raises
    ------
    OperationalRuleError
        If a declaration is malformed, unsafe, duplicated, escapes the
        checkout, contains a control character, cannot be read, or names an
        unreadable or invalid Cargo manifest.

    Notes
    -----
    A declared list in `.concordat` is authoritative. In its absence, a root
    `Cargo.toml` is the compatibility fallback; no root file resolves to no
    surfaces so policy can emit the established AP-001 onboarding finding.

    """
    manifest_path = checkout / CONCORDAT_FILENAME
    declared_context = SurfaceResolutionContext(
        checkout=checkout,
        manifest_path=manifest_path,
        is_declared=True,
    )
    manifest = _load_manifest(manifest_path)
    entries = _declared_surface_entries(manifest, manifest_path)
    if entries is not None:
        seen_paths: set[str] = set()
        surfaces: list[CargoSurface] = []
        for entry in entries:
            declaration = _surface_declaration(entry, manifest_path)
            if declaration.path in seen_paths:
                message = f"language.rust.surfaces repeats {declaration.path!r}"
                raise _resolution_error(message, manifest_path)
            seen_paths.add(declaration.path)
            surface = _resolved_surface(declared_context, declaration)
            surfaces.append(surface)
        return RustSurfaceResolution(declared=True, surfaces=tuple(surfaces))

    root_cargo_path = checkout / MANIFEST_FILENAME
    if not _root_cargo_toml_exists(root_cargo_path):
        return RustSurfaceResolution(declared=False, surfaces=())
    fallback_context = SurfaceResolutionContext(
        checkout=checkout,
        manifest_path=manifest_path,
        is_declared=False,
    )
    surface = _resolved_surface(
        fallback_context,
        SurfaceDeclaration(MANIFEST_FILENAME, None),
    )
    return RustSurfaceResolution(declared=False, surfaces=(surface,))
