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

import posixpath
import typing as typ

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
    package has a library target. `by_manifest` names the package each manifest
    declares, since a command run at a member's manifest selects that member.
    `default_selects` lists the packages a command with no `-p` or `--workspace`
    selects at the surface's own manifest, and `is_default_known` is false when
    `default-members` holds a pattern the audit does not expand.
    """

    manifest: str
    packages: list[str]
    manifests: list[str]
    features: list[str]
    has_default: bool
    has_library: bool
    by_manifest: dict[str, str]
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


_GLOB_CHARACTERS: typ.Final = frozenset("*?[]")


def _default_selection(
    reader: Reader, key: str, packages: list[PackageFacts]
) -> tuple[list[str], bool]:
    """Return the packages a bare command at the surface's manifest selects.

    Cargo selects the `default-members` of a workspace; with none, a virtual
    workspace selects every member and a workspace with a root package selects
    only that package.

    Returns
    -------
    tuple[list[str], bool]
        The selected package names, and whether the selection is known (it is
        not when `default-members` holds a glob).
    """
    root = reader.toml(reader.checkout / key) or {}
    workspace = table(root.get("workspace"))
    names = sorted(p["name"] for p in packages)
    default_members = workspace.get("default-members")
    if isinstance(default_members, list):
        entries = [m for m in default_members if isinstance(m, str)]
        if any(_GLOB_CHARACTERS & set(entry) for entry in entries):
            return names, False
        base = posixpath.dirname(key)
        joined = (posixpath.normpath(posixpath.join(base, entry)) for entry in entries)
        wanted = {"" if path == "." else path for path in joined}
        return sorted(p["name"] for p in packages if p["directory"] in wanted), True
    root_name = table(root.get("package")).get("name")
    if isinstance(root_name, str):
        return [root_name], True
    return names, True


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
    surfaces: list[Surface] = []
    for key, packages in sorted(grouped.items()):
        features = _surface_features(reader, packages)
        selected, is_known = _default_selection(reader, key, packages)
        surfaces.append({
            "by_manifest": {p["manifest"]: p["name"] for p in packages},
            "default_selects": selected,
            "is_default_known": is_known,
            "manifest": key,
            "packages": sorted(p["name"] for p in packages),
            "manifests": sorted(p["manifest"] for p in packages),
            "features": sorted(features - {"default"}),
            "has_default": "default" in features,
            "has_library": any(_has_library(reader, p) for p in packages),
        })
    return surfaces


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
