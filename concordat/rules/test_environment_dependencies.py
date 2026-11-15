"""Read a member's dependencies, and which wrapper crates it brings in.

`temp_env`, `env-lock` and figment's `Jail` change the process environment
through the same functions as `std::env::set_var`, out of Clippy's sight, so a
member that depends on one needs that crate's entry points disallowed too
(TE-002). Whether it does depends on more than the dependency table: a renamed
dependency keeps its package name, `workspace = true` takes the workspace
entry's features, and a feature the member enables by default can forward one
(`default = ["figment/test"]`). All three are resolved here. Features that
another workspace member enables are not: Cargo unifies them, but the rule
audits each member's own manifest.
"""

from __future__ import annotations

import collections
import dataclasses as dc
import typing as typ

from .test_environment_types import strings as _strings
from .test_environment_types import table

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .test_environment_types import WrapperRequirement

__all__ = [
    "DEFAULT_WRAPPERS",
    "Dependency",
    "dependencies_of",
    "enabled_features",
    "wrapper_requirements",
]

DEPENDENCY_TABLES: typ.Final = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)
DEFAULT_WRAPPERS: typ.Final[dict[str, dict[str, object]]] = {
    "temp_env": {
        "paths": [
            "temp_env::with_var",
            "temp_env::with_var_unset",
            "temp_env::with_vars",
            "temp_env::with_vars_unset",
        ],
        "feature_paths": {"async_closure": ["temp_env::async_with_vars"]},
    },
    "env_lock": {
        "paths": ["env_lock::lock_env", "env_lock::lock_current_dir"],
    },
    "figment": {
        "required_feature": "test",
        "paths": ["figment::Jail::expect_with", "figment::Jail::try_with"],
    },
}


@dc.dataclass(frozen=True, slots=True)
class Dependency:
    """One dependency entry of a member."""

    key: str
    package: str
    features: frozenset[str]
    path: str | None
    # The path came from the workspace's entry, so it is relative to the
    # workspace root and not to the member that inherits it.
    inherited_path: bool


def _normal(name: str) -> str:
    """Return a crate name in the underscore form the parameters use."""
    return name.replace("-", "_")


def _dependency_tables(manifest: dict[str, object]) -> list[dict[str, object]]:
    """Return every dependency table, including each `[target.*]` one."""
    scopes = [manifest, *(table(t) for t in table(manifest.get("target")).values())]
    return [table(scope.get(name)) for scope in scopes for name in DEPENDENCY_TABLES]


def _dependency(
    key: str, spec: object, workspace_dependencies: dict[str, object]
) -> Dependency:
    """Return one entry, with what a `workspace = true` entry inherits."""
    entry = table(spec)
    inherited = table(workspace_dependencies.get(key)) if entry.get("workspace") else {}
    package = entry.get("package") or inherited.get("package") or key
    features = {*_strings(entry.get("features")), *_strings(inherited.get("features"))}
    own_path = entry.get("path")
    path = own_path or inherited.get("path")
    return Dependency(
        key,
        _normal(str(package)),
        frozenset(features),
        path if isinstance(path, str) else None,
        inherited_path=not own_path and isinstance(path, str),
    )


def dependencies_of(
    manifest: dict[str, object], workspace_dependencies: dict[str, object]
) -> list[Dependency]:
    """Return the dependency entries of a manifest.

    Returns
    -------
    list[Dependency]
        Each entry of every dependency table, `[target.*]` tables included.
    """
    return [
        _dependency(key, spec, workspace_dependencies)
        for dependency_table in _dependency_tables(manifest)
        for key, spec in dependency_table.items()
    ]


def _follow(
    item: str,
    own_features: dict[str, object],
    pending: list[str],
    forwarded: dict[str, set[str]],
) -> None:
    """Queue a crate feature, or record the dependency feature an item enables."""
    name, slash, feature = item.partition("/")
    if slash:
        forwarded[name.removesuffix("?")].add(feature)
    elif item in own_features:
        pending.append(item)


def _forwarded_features(manifest: dict[str, object]) -> dict[str, set[str]]:
    """Return the dependency features the default feature set enables.

    Follows `default` through the member's `[features]` table, so
    `default = ["extras"]` with `extras = ["figment/test"]` enables `test` on
    figment.

    Returns
    -------
    dict[str, set[str]]
        The features forwarded to each dependency, by dependency key.
    """
    own_features = table(manifest.get("features"))
    forwarded: dict[str, set[str]] = collections.defaultdict(set)
    seen: set[str] = set()
    pending = ["default"]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for item in _strings(own_features.get(name)):
            _follow(item, own_features, pending, forwarded)
    return forwarded


def enabled_features(
    manifest: dict[str, object], workspace_dependencies: dict[str, object]
) -> dict[str, set[str]]:
    """Return each depended-on crate (underscore form) with its enabled features.

    Returns
    -------
    dict[str, set[str]]
        The features each dependency enables, from its own entry, the
        workspace entry it inherits, and the member's default features.
    """
    forwarded = _forwarded_features(manifest)
    found: dict[str, set[str]] = {}
    for dependency in dependencies_of(manifest, workspace_dependencies):
        found.setdefault(dependency.package, set()).update(
            dependency.features, forwarded.get(dependency.key, set())
        )
    return found


def _requirement(
    crate: str, spec: dict[str, object], features: set[str]
) -> WrapperRequirement | None:
    """Return what a wrapper needs disallowed, or None when its gate is shut."""
    gate = spec.get("required_feature")
    if isinstance(gate, str) and gate not in features:
        return None
    paths = set(_strings(spec.get("paths")))
    for feature, extra in table(spec.get("feature_paths")).items():
        if feature in features:
            paths.update(_strings(extra))
    return {"crate": crate, "paths": sorted(paths)}


def wrapper_requirements(
    enabled: dict[str, set[str]], wrappers: cabc.Mapping[str, object]
) -> list[WrapperRequirement]:
    """Return the wrapper crates a member uses, with the paths each needs.

    Returns
    -------
    list[WrapperRequirement]
        One entry per wrapper crate the member depends on, whose required
        feature (if the entry names one) is enabled.
    """
    candidates = (
        (crate, table(raw), enabled.get(crate))
        for crate, raw in sorted(wrappers.items())
    )
    found = (
        _requirement(crate, spec, features)
        for crate, spec, features in candidates
        if features is not None
    )
    return [requirement for requirement in found if requirement is not None]
