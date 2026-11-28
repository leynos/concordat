"""Find the Cargo surfaces a repository's lint gate has to cover.

A *surface* is what one lint command covers when it runs against a manifest: a
workspace root with its members, or a package that belongs to no workspace
(femtologging's `rust_extension/`, beside a Python project with no root
Cargo.toml). `rust-lint-tables` resolves which workspace governs each package;
this module groups the packages that way and records, per surface, what the
lint commands must cover: the packages, every declared feature (and whether a
`default` is declared), and whether any package is a library.

The same member rules decide membership as in `gate-workspace-coverage` and
`rust-lint-tables`, so the three cannot disagree about what a workspace holds.
"""

from __future__ import annotations

import typing as typ

from .lint_coverage_selection import default_selection, resolver_of
from .lint_tables_manifests import PackageFacts, read_packages
from .test_environment_types import table

if typ.TYPE_CHECKING:
    from .test_environment_types import Reader

__all__ = ["Surface", "read_surfaces", "surface_of"]


class Surface(typ.TypedDict):
    """One workspace root or standalone package, and what a lint gate must cover.

    `manifest` is the workspace root's manifest, or the package's own when it is
    in no workspace. `features` holds every declared feature name except
    `default`; `has_default` says `default` is declared; `has_library` says some
    package has a library target. `default_packages` names the packages that
    declare `default`, whose default feature sets a gate must check.
    `by_manifest` names the package each manifest declares, since a command run
    at a member's manifest selects that member. `resolver` is the feature
    resolver (`1`, `2` or `3`), since under `1` a bare `--features fast` applies
    to the package the command runs in only.
    `default_selects` lists the packages a command with no `-p` or `--workspace`
    selects at the surface's own manifest, and `is_default_known` is false when
    `default-members` holds a pattern the audit does not expand.
    """

    manifest: str
    packages: list[str]
    manifests: list[str]
    features: list[str]
    has_default: bool
    default_packages: list[str]
    has_library: bool
    by_manifest: dict[str, str]
    resolver: str
    default_selects: list[str]
    is_default_known: bool


def _surface_key(package: PackageFacts) -> str:
    """Return the manifest of the surface a package belongs to."""
    return package["workspace"] or package["manifest"]


def _declared_features(reader: Reader, package: PackageFacts) -> set[str]:
    """Return the feature names a package's `[features]` table declares."""
    manifest = reader.toml(reader.checkout / package["manifest"]) or {}
    return set(table(manifest.get("features")))


def _has_library(reader: Reader, package: PackageFacts) -> bool:
    """Return whether a package has a library target: `[lib]` or `src/lib.rs`."""
    manifest = reader.toml(reader.checkout / package["manifest"]) or {}
    root = reader.checkout / package["directory"]
    return "lib" in manifest or (root / "src" / "lib.rs").is_file()


def _surface_features(reader: Reader, packages: list[PackageFacts]) -> set[str]:
    """Return a surface's declared features, qualified when it has several packages.

    In a surface of one package a feature is its bare name. In a workspace of
    several, `fast` in `a` and `fast` in `b` are different features that one
    `-F a/fast` does not both cover, so each is named `package/feature`;
    `default` is kept bare because the surface only records that one exists.

    Returns
    -------
    set[str]
        The declared feature names of the surface.
    """
    if len(packages) == 1:
        return _declared_features(reader, packages[0])
    found: set[str] = set()
    for package in packages:
        declared = _declared_features(reader, package)
        found |= {"default"} & declared
        found |= {f"{package['name']}/{name}" for name in declared - {"default"}}
    return found


def _surface(reader: Reader, key: str, packages: list[PackageFacts]) -> Surface:
    """Return the surface a set of packages makes under *key*.

    Returns
    -------
    Surface
        What a lint gate must cover there.
    """
    features = _surface_features(reader, packages)
    selected, is_known = default_selection(reader, key, packages)
    return {
        "by_manifest": {p["manifest"]: p["name"] for p in packages},
        "resolver": resolver_of(reader, key),
        "default_selects": selected,
        "is_default_known": is_known,
        "manifest": key,
        "packages": sorted(p["name"] for p in packages),
        "manifests": sorted(p["manifest"] for p in packages),
        "features": sorted(features - {"default"}),
        "has_default": "default" in features,
        "default_packages": sorted(
            p["name"] for p in packages if "default" in _declared_features(reader, p)
        ),
        "has_library": any(_has_library(reader, p) for p in packages),
    }


def read_surfaces(reader: Reader) -> list[Surface]:
    """Return the checkout's Cargo surfaces, sorted by manifest path.

    Parameters
    ----------
    reader:
        Reads TOML inside the checkout and records what it could not read.

    Returns
    -------
    list[Surface]
        One surface per workspace root that has a package, and one per package
        outside every workspace.
    """
    grouped: dict[str, list[PackageFacts]] = {}
    for package in read_packages(reader):
        grouped.setdefault(_surface_key(package), []).append(package)
    return [
        _surface(reader, key, packages) for key, packages in sorted(grouped.items())
    ]


def surface_of(manifest: str, surfaces: list[Surface]) -> str | None:
    """Return the surface a command targeting *manifest* covers, if any.

    A command that targets a workspace root's manifest or any member's covers
    the surface; Cargo then selects packages by its own flags, which the policy
    reads from the run.

    Returns
    -------
    str | None
        The surface's manifest path, or None when no surface holds *manifest*.
    """
    for surface in surfaces:
        if manifest == surface["manifest"] or manifest in surface["manifests"]:
            return surface["manifest"]
    return None
