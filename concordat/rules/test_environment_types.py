"""Shared types, constants and the TOML reader for `rust-test-environment`.

The envelope builder is split by what it reads: workspace members and Clippy
configuration (`test_environment_members`), dependencies and the wrapper crates
they bring (`test_environment_dependencies`), configuration that lowers the lint
(`test_environment_config`), and Rust sources (`test_environment_sources`).
They share the shapes recorded here and one reader, so every file is read
through the same containment guard and every refusal is recorded once.
"""

from __future__ import annotations

import dataclasses as dc
import tomllib
import typing as typ

from .markdown_envelope import (
    Repository,
    is_file,
    read_text,
    within_checkout,
)

if typ.TYPE_CHECKING:
    import pathlib

__all__ = [
    "CARGO_MANIFEST",
    "ClippyConfigFact",
    "ConfigSuppressionFact",
    "MemberFact",
    "Reader",
    "SuppressionFact",
    "TestEnvironmentApplicability",
    "TestEnvironmentEnvelope",
    "WrapperRequirement",
    "strings",
    "table",
]

OPERATION_READ_ENVIRONMENT_CONFIG: typ.Final = "read-test-environment-config"
CARGO_MANIFEST: typ.Final = "Cargo.toml"


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


def table(value: object) -> dict[str, object]:
    """Return *value* when it is a table, else an empty one."""
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else {}


def strings(value: object) -> list[str]:
    """Return the strings of a list value, or an empty list."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


@dc.dataclass(slots=True)
class Reader:
    """Reads TOML inside the checkout, recording what cannot be read."""

    checkout: pathlib.Path
    root: pathlib.Path
    unreadable: list[str] = dc.field(default_factory=list)
    _cache: dict[pathlib.Path, dict[str, object] | None] = dc.field(
        default_factory=dict
    )

    def relative(self, path: pathlib.Path) -> str:
        """Return a path relative to the checkout, in POSIX form."""
        return path.relative_to(self.checkout).as_posix()

    def toml(self, path: pathlib.Path) -> dict[str, object] | None:
        """Return a decoded TOML file, or None when absent or unreadable.

        An unreadable or invalid file is appended to `unreadable` once, however
        often it is asked for; an absent one is not, because most repositories
        hold only some of these files.

        Returns
        -------
        dict[str, object] | None
            The decoded document, or None.
        """
        if path not in self._cache:
            self._cache[path] = self._decode(path)
        return self._cache[path]

    def _decode(self, path: pathlib.Path) -> dict[str, object] | None:
        """Read and decode one file, recording a refusal."""
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
