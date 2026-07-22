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
        with path.open("r", encoding="utf-8") as handle:
            contents = _yaml.load(handle) or {}
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

    Estates live under the active owner's namespace
    (``$XDG_CONFIG_HOME/concordat/owners/<owner>/config.yaml``). A legacy
    flat configuration is migrated into that layout the first time the
    owner is derivable; until then the flat path keeps working.
    """
    _migrate_legacy_config()
    if owner := xdg.get_active_owner():
        return xdg.owner_config_path(owner)
    return xdg.config_root() / CONFIG_FILENAME


def _load_legacy_migration() -> (
    tuple[Path, dict[str, typ.Any], dict[str, typ.Any], str] | None
):
    """Return the legacy migration inputs, or ``None`` when none applies.

    The tuple is ``(legacy, full_data, estate_section, owner)``. ``None`` is
    returned when an active owner is already configured, the flat config is
    absent, the parsed YAML or estate section is not a mapping, or no owner can
    be derived from the recorded estates.
    """
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


def _migrate_legacy_config() -> None:
    """Move a legacy flat estates config into the owner-namespaced layout.

    The active owner is only set once both filesystem writes succeed: were it
    set earlier, ``default_config_path()`` would resolve future implicit
    configuration operations to an incomplete owner-scoped config after a
    failed migration.
    """
    migration = _load_legacy_migration()
    if migration is None:
        return
    legacy, data, estate_section, owner = migration
    _write_owner_estate_config(owner, estate_section)
    _remove_legacy_estate_section(legacy, data)
    xdg.set_active_owner(owner)


def _derive_owner_from_estates(estate_section: dict[str, typ.Any]) -> str | None:
    """Return the sole github_owner recorded in a legacy estate section.

    The legacy flat format permitted estates for more than one owner. Moving
    such a section wholesale into a single owner's namespace would silently
    misplace the other owners' estates, so mixed-owner input is rejected
    rather than migrated under the first owner encountered.
    """
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
    estate_section = data.setdefault(ESTATE_SECTION, {})
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
    estate_section = data.setdefault(ESTATE_SECTION, {})
    estates = estate_section.setdefault(ESTATE_COLLECTION_KEY, {})
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


def _load_estates(config_path: Path | None) -> dict[str, EstateRecord]:
    data = _load_config(config_path)
    estate_section = data.get(ESTATE_SECTION, {})
    raw_estates = estate_section.get(ESTATE_COLLECTION_KEY, {})
    result: dict[str, EstateRecord] = {}
    if isinstance(raw_estates, dict):
        for alias, payload in raw_estates.items():
            if isinstance(payload, str):
                record = EstateRecord(alias=alias, repo_url=payload)
            elif isinstance(payload, dict):
                repo_url = payload.get("repo_url")
                if not isinstance(repo_url, str):
                    continue
                branch = payload.get("branch", DEFAULT_BRANCH)
                inventory_path = payload.get(
                    "inventory_path",
                    DEFAULT_INVENTORY_PATH,
                )
                owner = payload.get("github_owner")
                record = EstateRecord(
                    alias=alias,
                    repo_url=repo_url,
                    branch=str(branch),
                    inventory_path=str(inventory_path),
                    github_owner=_normalise_owner(owner),
                )
            else:
                continue
            result[alias] = record
    return result


def _load_metadata(config_path: Path | None) -> dict[str, typ.Any]:
    data = _load_config(config_path)
    section = data.get(ESTATE_SECTION, {})
    return section if isinstance(section, dict) else {}


def _normalise_owner(owner: str | None) -> str | None:
    if owner is None:
        return None
    trimmed = owner.strip()
    return trimmed or None
