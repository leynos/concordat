"""Owner-scoped estate configuration persistence and migration.

This module owns the YAML configuration storage, active-owner path
resolution, and the one-time migration of a legacy flat configuration into
the owner-namespaced layout. It has no dependency on the git/provisioning
code in :mod:`concordat.estate`, so that façade can re-export these helpers
without a circular import.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from cyclopts import config as cyclopts_config
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import xdg
from .estate_errors import (
    DuplicateEstateAliasError,
    EstateError,
    EstateNotConfiguredError,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

DEFAULT_BRANCH = "main"
DEFAULT_INVENTORY_PATH = "tofu/inventory/repositories.yaml"
CONFIG_FILENAME = "config.yaml"
ESTATE_SECTION = "estate"
ESTATE_COLLECTION_KEY = "estates"
ACTIVE_ESTATE_KEY = "active_estate"

_yaml = YAML(typ="safe")
_yaml.default_flow_style = False
_yaml.explicit_start = False
_yaml.explicit_end = False
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.sort_base_mapping_type_on_output = False


class _YamlConfig(cyclopts_config.ConfigFromFile):
    """Cyclopts config provider backed by ruamel.yaml."""

    def _load_config(self, path: Path) -> dict[str, typ.Any]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                contents = _yaml.load(handle) or {}
        except (OSError, UnicodeDecodeError, YAMLError) as error:
            message = f"cannot read estate configuration {path}: {error}"
            raise EstateError(message) from error
        return dict(contents) if isinstance(contents, dict) else {}


@dataclasses.dataclass(frozen=True, slots=True)
class EstateRecord:
    """Configuration for a managed estate repository."""

    alias: str
    repo_url: str
    branch: str = DEFAULT_BRANCH
    inventory_path: str = DEFAULT_INVENTORY_PATH
    github_owner: str | None = None


def default_config_path() -> Path:
    """Return the path to the estates configuration file.

    This is a read-only query: it resolves the path from the current active
    owner and never mutates state. Estates live under the active owner's
    namespace (``$XDG_CONFIG_HOME/concordat/owners/<owner>/config.yaml``); with
    no active owner the flat path is returned. Any legacy-flat migration is a
    separate, explicit bootstrap step (see :func:`migrate_legacy_config`), run
    once at the CLI entry point rather than implicitly from this getter.

    Returns
    -------
    Path
        Active-owner configuration path, or the legacy flat path when no owner
        is active.
    """
    if owner := xdg.get_active_owner():
        return xdg.owner_config_path(owner)
    return xdg.config_root() / CONFIG_FILENAME


def _load_legacy_migration() -> (
    tuple[Path, dict[str, typ.Any], dict[str, typ.Any], str] | None
):
    """Return legacy migration inputs, or ``None`` when none applies."""
    if xdg.get_active_owner() is not None:
        return None
    legacy = xdg.config_root() / CONFIG_FILENAME
    if not legacy.is_file():
        return None
    try:
        data = _yaml.load(legacy.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        message = f"cannot read legacy configuration {legacy}: {error}"
        raise EstateError(message) from error
    if not isinstance(data, dict):
        return None
    estate_section = data.get(ESTATE_SECTION)
    if not isinstance(estate_section, dict):
        return None
    owner = _derive_owner_from_estates(estate_section)
    if owner is None:
        return None
    return legacy, data, estate_section, owner


def _write_owner_estate_config(owner: str, estate_section: dict[str, typ.Any]) -> None:
    """Write the estate section into the owner-namespaced config file."""
    owner_path = xdg.owner_config_path(owner)
    owner_path.parent.mkdir(parents=True, exist_ok=True)
    with owner_path.open("w", encoding="utf-8") as handle:
        _yaml.dump({ESTATE_SECTION: estate_section}, handle)


def _remove_legacy_estate_section(legacy: Path, data: dict[str, typ.Any]) -> None:
    """Drop the estate section from the legacy file, rewriting or removing it."""
    remaining = {key: value for key, value in data.items() if key != ESTATE_SECTION}
    if remaining:
        with legacy.open("w", encoding="utf-8") as handle:
            _yaml.dump(remaining, handle)
    else:
        legacy.unlink()


def _estate_section(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
    """Return the mutable estate section."""
    match data.get(ESTATE_SECTION):
        case dict() as section:
            pass
        case _:
            # `setdefault` only inserts when the key is absent, so a persisted
            # `estate:` holding a string or list would be returned as-is and
            # then mutated, raising `TypeError` or `AttributeError`. The read
            # paths already treat a non-mapping section as empty; the write
            # paths agree.
            section = {}
            data[ESTATE_SECTION] = section
    return section


def _estate_collection(section: dict[str, typ.Any]) -> dict[str, typ.Any]:
    """Return the mutable estate collection within *section*."""
    match section.get(ESTATE_COLLECTION_KEY):
        case dict() as estates:
            pass
        case _:
            # The same `setdefault` hazard one level down: a persisted
            # `estates:` holding a list passes the duplicate-alias membership
            # test and then raises on item assignment, and a scalar raises on
            # the membership test itself.
            estates = {}
            section[ESTATE_COLLECTION_KEY] = estates
    return estates


def _current_legacy_data(
    legacy: Path,
    fallback: dict[str, typ.Any],
) -> dict[str, typ.Any]:
    """Return the legacy file's current contents, or *fallback* if absent."""
    if not legacy.is_file():
        return fallback
    try:
        reloaded = _yaml.load(legacy.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        message = f"cannot read legacy configuration {legacy}: {error}"
        raise EstateError(message) from error
    return reloaded if isinstance(reloaded, dict) else fallback


def migrate_legacy_config() -> None:
    """Move a legacy flat estates config into the owner-namespaced layout.

    This is the explicit, side-effecting migration step: it writes the
    owner-scoped config, rewrites or removes the legacy flat file, and sets the
    active owner. It is idempotent (a no-op once migrated or when no legacy
    config exists) and is invoked once at the CLI bootstrap, keeping
    :func:`default_config_path` a pure read-only query.

    The step order is load-bearing. The active owner is set as soon as the
    owner-scoped file is complete, and legacy cleanup happens last, because
    the active owner is what points ``default_config_path`` at the migrated
    data. Removing the legacy section first opens a window where a failure
    would leave the estates in neither place the CLI looks: the flat file no
    longer holds them and no active owner selects the owner-scoped file. That
    is unrecoverable, since :func:`_load_legacy_migration` then finds no
    estate section to retry from.

    Cleanup is therefore the only step allowed to fail. If it does, the estate
    data is duplicated rather than lost — the migrated copy is already live —
    and a later run leaves the stale legacy section in place. Duplicated but
    reachable beats complete but invisible.
    """
    migration = _load_legacy_migration()
    if migration is None:
        return
    legacy, data, estate_section, owner = migration
    _write_owner_estate_config(owner, estate_section)
    xdg.set_active_owner(owner)
    _remove_legacy_estate_section(legacy, _current_legacy_data(legacy, data))


def _derive_owner_from_estates(estate_section: dict[str, typ.Any]) -> str | None:
    """Return the sole GitHub owner recorded in a legacy estate section."""
    estates = estate_section.get(ESTATE_COLLECTION_KEY)
    if not isinstance(estates, dict):
        return None
    owners = {
        str(entry["github_owner"])
        for entry in estates.values()
        if isinstance(entry, dict) and entry.get("github_owner")
    }
    if not owners:
        return None
    if len(owners) > 1:
        joined = ", ".join(sorted(owners))
        message = (
            f"legacy configuration mixes estates from multiple github owners "
            f"({joined}); split them into per-owner configurations under "
            "the relevant `concordat owner use <owner>` namespaces before "
            "migrating"
        )
        raise EstateError(message)
    return next(iter(owners))


def list_estates(config_path: Path | None = None) -> list[EstateRecord]:
    """Return every configured estate sorted by alias."""
    records = _load_estates(config_path)
    return sorted(records.values(), key=lambda record: record.alias)


def get_estate(
    alias: str,
    *,
    config_path: Path | None = None,
) -> EstateRecord | None:
    """Look up a specific estate by alias."""
    if not alias:
        return None
    return _load_estates(config_path).get(alias)


def get_active_estate(config_path: Path | None = None) -> EstateRecord | None:
    """Return the currently active estate."""
    estates = _load_estates(config_path)
    metadata = _load_metadata(config_path)
    active_alias = metadata.get(ACTIVE_ESTATE_KEY)
    if not active_alias:
        return None
    return estates.get(active_alias)


def set_active_estate(
    alias: str,
    *,
    config_path: Path | None = None,
) -> EstateRecord:
    """Mark the provided alias as the active estate."""
    estates = _load_estates(config_path)
    record = estates.get(alias)
    if not record:
        raise EstateNotConfiguredError(alias)
    data = _load_config(config_path)
    estate_section = _estate_section(data)
    estate_section[ACTIVE_ESTATE_KEY] = alias
    _write_config(data, config_path)
    return record


def register_estate(
    record: EstateRecord,
    *,
    config_path: Path | None = None,
    set_active_if_missing: bool = True,
) -> None:
    """Persist a new estate entry and optionally set it active."""
    data = _load_config(config_path)
    estate_section = _estate_section(data)
    estates = _estate_collection(estate_section)
    if record.alias in estates:
        raise DuplicateEstateAliasError(record.alias)
    entry = {
        "repo_url": record.repo_url,
        "branch": record.branch,
        "inventory_path": record.inventory_path,
    }
    if record.github_owner:
        entry["github_owner"] = record.github_owner
    estates[record.alias] = entry
    if set_active_if_missing and not estate_section.get(ACTIVE_ESTATE_KEY):
        estate_section[ACTIVE_ESTATE_KEY] = record.alias
    _write_config(data, config_path)


def _load_config(config_path: Path | None) -> dict[str, typ.Any]:
    path = config_path or default_config_path()
    provider = _YamlConfig(path=str(path), must_exist=False)
    raw = provider.config or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _write_config(data: dict[str, typ.Any], config_path: Path | None) -> None:
    path = config_path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(data, handle)


def _estate_record_from_payload(
    alias: str,
    payload: object,
) -> EstateRecord | None:
    """Decode one persisted estate payload, or reject an unsupported one."""
    match payload:
        case str():
            return EstateRecord(alias=alias, repo_url=payload)
        case {"repo_url": str() as repo_url, **rest}:
            # The pattern proves only that `repo_url` is a string, so the owner
            # is still whatever YAML decoded. Narrow it at runtime rather than
            # asserting a type: a persisted `github_owner: 7` would otherwise
            # reach `.strip()` and crash the CLI instead of being ignored.
            owner = rest.get("github_owner")
            return EstateRecord(
                alias=alias,
                repo_url=repo_url,
                branch=str(rest.get("branch", DEFAULT_BRANCH)),
                inventory_path=str(rest.get("inventory_path", DEFAULT_INVENTORY_PATH)),
                github_owner=_normalise_owner(
                    owner if isinstance(owner, str) else None
                ),
            )
        case _:
            return None


def _load_estates(config_path: Path | None) -> dict[str, EstateRecord]:
    """Load valid estate records indexed by alias."""
    data = _load_config(config_path)
    estate_section = data.get(ESTATE_SECTION, {})
    raw_estates = estate_section.get(ESTATE_COLLECTION_KEY, {})
    if not isinstance(raw_estates, dict):
        return {}

    records: dict[str, EstateRecord] = {}
    for alias, payload in raw_estates.items():
        record = _estate_record_from_payload(alias, payload)
        if record is not None:
            records[alias] = record
    return records


def _load_metadata(config_path: Path | None) -> dict[str, typ.Any]:
    data = _load_config(config_path)
    section = data.get(ESTATE_SECTION, {})
    return section if isinstance(section, dict) else {}


def _normalise_owner(owner: str | None) -> str | None:
    if owner is None:
        return None
    trimmed = owner.strip()
    return trimmed or None
