"""Build the `policy-input/rust-test-environment` envelope.

The estate's rule for Rust tests is that no test changes the process-wide
environment: it is an injected object, or the test runs a subprocess. Clippy
finds the calls (`std::env::set_var`, `remove_var`, `set_current_dir`, and the
wrapper crates that call them), after macro expansion, so this envelope does
not look for calls. It records what decides whether Clippy's verdict stands:

- per workspace member, the Clippy configuration Clippy itself would load, the
  `disallowed-methods` paths it lists, and the wrapper crates the member
  depends on, with the paths each one would need (`test_environment_members`,
  `test_environment_dependencies`);
- every attribute in a member's Rust files that suppresses the lint other than
  a site-level `#[expect(.., reason = "..")]` (`test_environment_sources`);
- configuration that lowers the lint outside source: lints tables in
  `Cargo.toml` and `-A` flags in `.cargo/config.toml`
  (`test_environment_config`).

A file the audit cannot read, or a setting that makes Clippy's configuration
unknowable (`CLIPPY_CONF_DIR`), is recorded as unreadable, never guessed.

The file readers are `markdown_envelope`'s, so every envelope shares one
containment guard: no policy input is read from outside the checkout.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from .markdown_envelope import resolved_root
from .test_environment_config import config_suppressions
from .test_environment_dependencies import (
    DEFAULT_WRAPPERS,
    enabled_features,
    wrapper_requirements,
)
from .test_environment_members import clippy_fact, member_directories
from .test_environment_sources import scan_suppressions
from .test_environment_types import (
    CARGO_MANIFEST,
    MemberFact,
    Reader,
    TestEnvironmentEnvelope,
    strings,
    table,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

__all__ = [
    "DEFAULT_REQUIRED_PATHS",
    "DEFAULT_SILENCING_LINTS",
    "ENVELOPE_KIND",
    "TestEnvironmentEnvelope",
    "build_test_environment_envelope",
]

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/rust-test-environment"
DEFAULT_REQUIRED_PATHS: typ.Final = (
    "std::env::set_var",
    "std::env::remove_var",
    "std::env::set_current_dir",
)
DEFAULT_SILENCING_LINTS: typ.Final = (
    "clippy::disallowed_methods",
    "clippy::style",
    "clippy::all",
    "warnings",
)


def _parameter_list(
    parameters: cabc.Mapping[str, object] | None, key: str, default: typ.Sequence[str]
) -> list[str]:
    """Return a manifest string-list parameter, or its default."""
    return strings((parameters or {}).get(key)) or list(default)


def _wrappers(
    parameters: cabc.Mapping[str, object] | None,
) -> cabc.Mapping[str, object]:
    """Return the manifest's wrapper table, or the default."""
    return table((parameters or {}).get("wrapper_paths")) or DEFAULT_WRAPPERS


@dataclasses.dataclass(frozen=True, slots=True)
class _Wrapping:
    """What decides which wrapper crates a member needs disallowed."""

    workspace_dependencies: dict[str, object]
    wrappers: cabc.Mapping[str, object]


def _member_fact(
    reader: Reader,
    directory: pathlib.Path,
    manifest: dict[str, object],
    wrapping: _Wrapping,
) -> MemberFact:
    """Return what the audit records about one workspace member."""
    enabled = enabled_features(manifest, wrapping.workspace_dependencies)
    return {
        "path": reader.relative(directory),
        "clippy": clippy_fact(reader, directory),
        "wrappers": wrapper_requirements(enabled, wrapping.wrappers),
    }


def _empty_envelope(
    checkout: pathlib.Path, required: list[str], reader: Reader
) -> TestEnvironmentEnvelope:
    """Return an envelope with the facts still to be filled in."""
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {"cargo_manifest": (checkout / CARGO_MANIFEST).is_file()},
        "required_paths": required,
        "members": [],
        "suppressions": [],
        "config_suppressions": [],
        "unreadable": reader.unreadable,
    }


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
    required = _parameter_list(parameters, "required_paths", DEFAULT_REQUIRED_PATHS)
    silencing = frozenset(
        _parameter_list(parameters, "silencing_lints", DEFAULT_SILENCING_LINTS)
    )
    reader = Reader(checkout, resolved_root(checkout))
    envelope = _empty_envelope(checkout, required, reader)
    root_manifest = reader.toml(checkout / CARGO_MANIFEST)
    if root_manifest is None:
        return envelope
    directories = member_directories(reader, root_manifest)
    manifests = {
        reader.relative(directory / CARGO_MANIFEST): (
            reader.toml(directory / CARGO_MANIFEST) or {}
        )
        for directory in directories
    }
    wrapping = _Wrapping(
        table(table(root_manifest.get("workspace")).get("dependencies")),
        _wrappers(parameters),
    )
    envelope["members"] = [
        _member_fact(
            reader,
            directory,
            manifests[reader.relative(directory / CARGO_MANIFEST)],
            wrapping,
        )
        for directory in directories
    ]
    envelope["config_suppressions"] = config_suppressions(
        reader, {CARGO_MANIFEST: root_manifest, **manifests}, silencing
    )
    scan = scan_suppressions(checkout, directories, silencing)
    envelope["suppressions"] = [
        {"path": s.path, "line": s.line, "kind": s.kind, "lint": s.lint}
        for s in scan.suppressions
    ]
    envelope["unreadable"] = sorted({*reader.unreadable, *scan.unreadable})
    return envelope
