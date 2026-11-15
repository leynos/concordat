"""Build the `policy-input/rust-test-environment` envelope.

The estate's rule for Rust tests is that no test changes the process-wide
environment: it is an injected object, or the test runs a subprocess. Clippy
finds the calls (`std::env::set_var`, `remove_var`, `set_current_dir`, and the
wrapper crates that call them), after macro expansion, so this envelope does
not look for calls. It records what decides whether Clippy's verdict stands:

- per workspace member, the Clippy configuration Clippy itself would load (the
  nearest `.clippy.toml` or `clippy.toml` at or above the member's directory,
  inside the checkout), the `disallowed-methods` paths it lists, and the wrapper
  crates the member depends on, with the paths each one would need;
- every attribute in a member's Rust files that suppresses the lint other than
  a site-level `#[expect(.., reason = "..")]` (`test_environment_sources`);
- configuration that lowers the lint outside source: `[lints.clippy]` tables
  in `Cargo.toml` and `-A` flags in `.cargo/config.toml`.

A file the audit cannot read, or a setting that makes Clippy's configuration
unknowable (`CLIPPY_CONF_DIR`), is recorded as unreadable, never guessed.

The file readers are `markdown_envelope`'s, so every envelope shares one
containment guard: no policy input is read from outside the checkout.
"""

from __future__ import annotations

import dataclasses as dc
import glob
import tomllib
import typing as typ

from .markdown_envelope import (
    Repository,
    is_file,
    read_text,
    resolved_root,
    within_checkout,
)
from .test_environment_sources import scan_suppressions

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/rust-test-environment"
OPERATION_READ_ENVIRONMENT_CONFIG: typ.Final = "read-test-environment-config"
CARGO_MANIFEST: typ.Final = "Cargo.toml"
CLIPPY_CONFIG_NAMES: typ.Final = (".clippy.toml", "clippy.toml")
CARGO_CONFIG_NAMES: typ.Final = (".cargo/config.toml", ".cargo/config")
DEPENDENCY_TABLES: typ.Final = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)
LINT_LEVEL_ALLOW: typ.Final = "allow"

DEFAULT_REQUIRED_PATHS: typ.Final = (
    "std::env::set_var",
    "std::env::remove_var",
    "std::env::set_current_dir",
)
DEFAULT_SILENCING_LINTS: typ.Final = (
    "clippy::disallowed_methods",
    "clippy::style",
    "clippy::all",
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


class ClippyConfigFact(typ.TypedDict):
    """The Clippy configuration that applies to one member."""

    path: str | None
    disallowed: list[str]
    readable: bool


class WrapperRequirement(typ.TypedDict):
    """A wrapper crate a member depends on, and the paths it needs disallowed."""

    crate: str
    paths: list[str]


class MemberFact(typ.TypedDict):
    """One workspace member."""

    path: str
    clippy: ClippyConfigFact
    wrappers: list[WrapperRequirement]


class SuppressionFact(typ.TypedDict):
    """An attribute that suppresses the lint other than at one site."""

    path: str
    line: int
    kind: str
    lint: str


class ConfigSuppressionFact(typ.TypedDict):
    """A setting outside source that lowers the lint."""

    path: str
    detail: str


class TestEnvironmentApplicability(typ.TypedDict):
    """Evidence that the rule applies."""

    cargo_manifest: bool


class TestEnvironmentEnvelope(typ.TypedDict):
    """The `policy-input/rust-test-environment` document."""

    schema_version: int
    kind: str
    repository: Repository
    applicability: TestEnvironmentApplicability
    required_paths: list[str]
    members: list[MemberFact]
    suppressions: list[SuppressionFact]
    config_suppressions: list[ConfigSuppressionFact]
    unreadable: list[str]


@dc.dataclass(slots=True)
class _Reader:
    """Reads TOML inside the checkout, recording what cannot be read."""

    checkout: pathlib.Path
    root: pathlib.Path
    unreadable: list[str] = dc.field(default_factory=list)

    def relative(self, path: pathlib.Path) -> str:
        """Return a path relative to the checkout, in POSIX form."""
        return path.relative_to(self.checkout).as_posix()

    def toml(self, path: pathlib.Path) -> dict[str, object] | None:
        """Return a decoded TOML file, or None when absent or unreadable.

        An unreadable or invalid file is appended to `unreadable`; an absent
        one is not, because most repositories hold only some of these files.

        Returns
        -------
        dict[str, object] | None
            The decoded document, or None.
        """
        operation = OPERATION_READ_ENVIRONMENT_CONFIG
        if not (
            within_checkout(self.root, path, operation) and is_file(path, operation)
        ):
            return None
        try:
            return tomllib.loads(read_text(path, operation))
        except UnicodeDecodeError as error:
            self.unreadable.append(f"{self.relative(path)}: not UTF-8 text: {error}")
        except tomllib.TOMLDecodeError as error:
            self.unreadable.append(f"{self.relative(path)}: invalid TOML: {error}")
        return None


def _table(value: object) -> dict[str, object]:
    """Return *value* when it is a table, else an empty one."""
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _normal(name: str) -> str:
    """Return a crate name in the underscore form the parameters use."""
    return name.replace("-", "_")


def _member_directories(
    reader: _Reader, manifest: dict[str, object]
) -> list[pathlib.Path]:
    """Return the directories of the workspace members that hold a manifest.

    The root is a member when it has a `[package]`. `members` and `exclude`
    entries are globs relative to the root, as Cargo reads them.

    Returns
    -------
    list[pathlib.Path]
        The member directories, sorted and without duplicates.
    """
    workspace = _table(manifest.get("workspace"))
    found: list[pathlib.Path] = []
    if "package" in manifest:
        found.append(reader.checkout)
    excluded = {
        path
        for pattern in _strings(workspace.get("exclude"))
        for path in _expand(reader, pattern)
    }
    for pattern in _strings(workspace.get("members")):
        found += [
            path
            for path in _expand(reader, pattern)
            if path.is_dir()
            and path not in excluded
            and (path / CARGO_MANIFEST).is_file()
        ]
    return sorted(set(found))


def _expand(reader: _Reader, pattern: str) -> list[pathlib.Path]:
    """Return the paths a workspace glob matches that stay inside the checkout."""
    matches = (
        reader.checkout / name
        for name in sorted(glob.glob(pattern, root_dir=reader.checkout, recursive=True))  # noqa: PTH207 - Path.glob rejects "."
    )
    return [
        path
        for path in matches
        if within_checkout(reader.root, path, OPERATION_READ_ENVIRONMENT_CONFIG)
    ]


def _strings(value: object) -> list[str]:
    """Return the strings of a list value, or an empty list."""
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def _disallowed_paths(config: dict[str, object]) -> list[str]:
    """Return the `path` of each `disallowed-methods` entry, string or table."""
    entries = config.get("disallowed-methods")
    if not isinstance(entries, list):
        return []
    paths = [
        entry if isinstance(entry, str) else _table(entry).get("path")
        for entry in entries
    ]
    return sorted({path for path in paths if isinstance(path, str)})


def _clippy_fact(reader: _Reader, member: pathlib.Path) -> ClippyConfigFact:
    """Return the Clippy configuration Clippy would load for a member.

    Clippy looks in the crate's directory, then each parent, and takes the
    first `.clippy.toml` or `clippy.toml` it finds (the dotted name first).

    Returns
    -------
    ClippyConfigFact
        The file's path and `disallowed-methods` paths, or no path when none
        applies.
    """
    for directory in (member, *member.parents):
        for name in CLIPPY_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                config = reader.toml(candidate)
                return {
                    "path": reader.relative(candidate),
                    "disallowed": _disallowed_paths(config or {}),
                    "readable": config is not None,
                }
        if directory == reader.checkout:
            break
    return {"path": None, "disallowed": [], "readable": True}


def _dependency_features(
    member_manifest: dict[str, object], workspace_dependencies: dict[str, object]
) -> dict[str, set[str]]:
    """Return each depended-on crate (underscore form) with its enabled features.

    Dependencies are read from every dependency table, including
    `[target.*.dependencies]`; a renamed dependency is recorded under its real
    package name, and `workspace = true` merges the workspace entry's features.

    Returns
    -------
    dict[str, set[str]]
        The enabled features of each depended-on crate.
    """
    tables = [_table(member_manifest.get(name)) for name in DEPENDENCY_TABLES]
    for target in _table(member_manifest.get("target")).values():
        tables += [_table(_table(target).get(name)) for name in DEPENDENCY_TABLES]
    found: dict[str, set[str]] = {}
    for table in tables:
        for key, spec in table.items():
            entry = _table(spec)
            inherited = (
                _table(workspace_dependencies.get(key))
                if entry.get("workspace")
                else {}
            )
            package = entry.get("package") or inherited.get("package") or key
            features = set(_strings(entry.get("features"))) | set(
                _strings(inherited.get("features"))
            )
            found.setdefault(_normal(str(package)), set()).update(features)
    return found


def _wrapper_requirements(
    dependencies: dict[str, set[str]], wrappers: cabc.Mapping[str, object]
) -> list[WrapperRequirement]:
    """Return the wrapper crates a member uses, with the paths each needs."""
    required: list[WrapperRequirement] = []
    for crate, raw in sorted(wrappers.items()):
        spec = _table(raw)
        features = dependencies.get(crate)
        if features is None:
            continue
        gate = spec.get("required_feature")
        if isinstance(gate, str) and gate not in features:
            continue
        paths = set(_strings(spec.get("paths")))
        for feature, extra in _table(spec.get("feature_paths")).items():
            if feature in features:
                paths |= set(_strings(extra))
        required.append({"crate": crate, "paths": sorted(paths)})
    return required


def _lint_levels(manifest: dict[str, object]) -> list[tuple[str, str]]:
    """Return `(lint, level)` for each `[lints.clippy]` entry the manifest sets."""
    tables = [
        _table(_table(manifest.get("lints")).get("clippy")),
        _table(_table(_table(manifest.get("workspace")).get("lints")).get("clippy")),
    ]
    return [
        (lint, str(_table(spec).get("level") if isinstance(spec, dict) else spec))
        for table in tables
        for lint, spec in table.items()
    ]


def _manifest_suppressions(
    path: str, manifest: dict[str, object], silencing: frozenset[str]
) -> list[ConfigSuppressionFact]:
    """Return the `[lints.clippy]` entries that allow the lint or a group."""
    return [
        {"path": path, "detail": f'{lint} = "allow" in a [lints.clippy] table'}
        for lint, level in _lint_levels(manifest)
        if f"clippy::{lint}" in silencing and level == LINT_LEVEL_ALLOW
    ]


def _flag_words(value: object) -> list[str]:
    """Return the words of a `rustflags` string or list, split on spaces."""
    if isinstance(value, str):
        return value.split()
    return [word for item in _strings(value) for word in item.split()]


def _allowed_lints(words: list[str]) -> set[str]:
    """Return the lints a flag list allows: `-A x`, `-Ax`, `--allow x`, `--allow=x`."""
    allowed: set[str] = set()
    for here, following in zip(words, [*words[1:], ""], strict=False):
        if here in {"-A", "--allow"}:
            allowed.add(following)
        elif here.startswith("--allow="):
            allowed.add(here.removeprefix("--allow="))
        elif here.startswith("-A") and len(here) > len("-A"):
            allowed.add(here.removeprefix("-A"))
    return allowed


def _cargo_config_facts(
    path: str, config: dict[str, object], silencing: frozenset[str]
) -> tuple[list[ConfigSuppressionFact], list[str]]:
    """Return suppressions in a `.cargo/config.toml`, and reasons it is unknowable."""
    flags = [_flag_words(_table(config.get("build")).get("rustflags"))]
    flags += [
        _flag_words(_table(target).get("rustflags"))
        for target in _table(config.get("target")).values()
    ]
    environment = _table(config.get("env"))
    for name in ("RUSTFLAGS", "CLIPPY_ARGS"):
        value = environment.get(name)
        flags.append(
            _flag_words(
                _table(value).get("value") if isinstance(value, dict) else value
            )
        )
    found: list[ConfigSuppressionFact] = [
        {"path": path, "detail": f"a flag allows {lint}"}
        for words in flags
        for lint in sorted(_allowed_lints(words) & silencing)
    ]
    unreadable = (
        [
            (
                f"{path}: [env] CLIPPY_CONF_DIR is set, so the Clippy "
                "configuration in force is unknown"
            )
        ]
        if "CLIPPY_CONF_DIR" in environment
        else []
    )
    return found, unreadable


def _config_suppressions(
    reader: _Reader,
    manifests: dict[str, dict[str, object]],
    silencing: frozenset[str],
) -> list[ConfigSuppressionFact]:
    """Return every configuration-level suppression in the checkout."""
    found: list[ConfigSuppressionFact] = []
    for path, manifest in sorted(manifests.items()):
        found += _manifest_suppressions(path, manifest, silencing)
    for name in CARGO_CONFIG_NAMES:
        config = reader.toml(reader.checkout / name)
        if config is None:
            continue
        flags, unknown = _cargo_config_facts(name, config, silencing)
        found += flags
        reader.unreadable += unknown
    return found


def _parameter_list(
    parameters: cabc.Mapping[str, object] | None, key: str, default: typ.Sequence[str]
) -> list[str]:
    """Return a manifest string-list parameter, or its default."""
    declared = _strings((parameters or {}).get(key))
    return declared or list(default)


def _wrappers(
    parameters: cabc.Mapping[str, object] | None,
) -> cabc.Mapping[str, object]:
    """Return the manifest's wrapper table, or the default."""
    declared = (parameters or {}).get("wrapper_paths")
    return _table(declared) or DEFAULT_WRAPPERS


def build_test_environment_envelope(
    checkout: pathlib.Path,
    parameters: cabc.Mapping[str, object] | None = None,
) -> TestEnvironmentEnvelope:
    """Assemble the test-environment policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    parameters:
        The manifest parameters: `required_paths`, `silencing_lints` and
        `wrapper_paths`.

    An `OperationalRuleError` propagates if a path resolves outside the
    checkout.

    Returns
    -------
    TestEnvironmentEnvelope
        The policy input document. A repository with no root `Cargo.toml` is
        recorded as not applicable.
    """
    root = resolved_root(checkout)
    required = _parameter_list(parameters, "required_paths", DEFAULT_REQUIRED_PATHS)
    silencing = frozenset(
        _parameter_list(parameters, "silencing_lints", DEFAULT_SILENCING_LINTS)
    )
    reader = _Reader(checkout, root)
    root_manifest = reader.toml(checkout / CARGO_MANIFEST)
    applicable = (checkout / CARGO_MANIFEST).is_file()
    envelope: TestEnvironmentEnvelope = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {"cargo_manifest": applicable},
        "required_paths": required,
        "members": [],
        "suppressions": [],
        "config_suppressions": [],
        "unreadable": reader.unreadable,
    }
    if not applicable or root_manifest is None:
        return envelope
    directories = _member_directories(reader, root_manifest)
    manifests: dict[str, dict[str, object]] = {CARGO_MANIFEST: root_manifest}
    workspace_dependencies = _table(
        _table(root_manifest.get("workspace")).get("dependencies")
    )
    wrappers = _wrappers(parameters)
    for directory in directories:
        manifest = (
            root_manifest
            if directory == checkout
            else reader.toml(directory / CARGO_MANIFEST) or {}
        )
        relative = reader.relative(directory / CARGO_MANIFEST)
        manifests[relative] = manifest
        dependencies = _dependency_features(manifest, workspace_dependencies)
        envelope["members"].append({
            "path": reader.relative(directory),
            "clippy": _clippy_fact(reader, directory),
            "wrappers": _wrapper_requirements(dependencies, wrappers),
        })
    envelope["config_suppressions"] = _config_suppressions(reader, manifests, silencing)
    scan = scan_suppressions(checkout, directories, silencing)
    envelope["suppressions"] = [
        {"path": s.path, "line": s.line, "kind": s.kind, "lint": s.lint}
        for s in scan.suppressions
    ]
    envelope["unreadable"] = sorted({*reader.unreadable, *scan.unreadable})
    return envelope
