"""XDG base-directory layout for all concordat configuration and state.

Everything the CLI persists lives under the XDG base directories,
namespaced by GitHub owner:

- ``$XDG_CONFIG_HOME/concordat/config.yaml`` — the headline config; its
  ``github_owner`` key names the active owner.
- ``$XDG_CONFIG_HOME/concordat/owners/<owner>/config.yaml`` — estates and
  the active estate for one owner.
- ``$XDG_CONFIG_HOME/concordat/owners/<owner>/credentials.yaml`` —
  optional credential fallbacks (see :mod:`concordat.credentials`).
- ``$XDG_CACHE_HOME/concordat/owners/<owner>/estates/<alias>`` — estate
  repository caches.
- ``$XDG_CACHE_HOME/concordat/tofu/plugin-cache`` — the shared OpenTofu
  provider plugin cache (provider binaries are owner-independent).
- ``$XDG_STATE_HOME/concordat/owners/<owner>/runs/`` — throwaway OpenTofu
  working trees (kept only with ``--keep-workdir``).

Remote OpenTofu state stored in the configured S3 backend is unaffected.
"""

from __future__ import annotations

import os
import re
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .errors import ConcordatError

APP_DIRNAME: typ.Final = "concordat"
OWNERS_DIRNAME: typ.Final = "owners"
HEADLINE_FILENAME: typ.Final = "config.yaml"
OWNER_CONFIG_FILENAME: typ.Final = "config.yaml"
CREDENTIALS_FILENAME: typ.Final = "credentials.yaml"
ACTIVE_OWNER_KEY: typ.Final = "github_owner"

# GitHub owner names: alphanumerics and internal hyphens only.
_OWNER_PATTERN: typ.Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")

ERROR_INVALID_OWNER = (
    "invalid GitHub owner name {owner!r}: expected alphanumerics and "
    "internal hyphens only"
)

_yaml = YAML()
_yaml.default_flow_style = False

type EnvMapping = typ.Mapping[str, str]


def _environ(env: EnvMapping | None) -> EnvMapping:
    return os.environ if env is None else env


def _base(env: EnvMapping | None, variable: str, fallback: tuple[str, ...]) -> Path:
    source = _environ(env)
    # The XDG specification requires relative base directories to be
    # ignored, falling back to the default location.
    if root := source.get(variable):
        candidate = Path(root).expanduser()
        if candidate.is_absolute():
            return candidate / APP_DIRNAME
    return Path.home().joinpath(*fallback) / APP_DIRNAME


def config_root(env: EnvMapping | None = None) -> Path:
    """Return the concordat configuration root."""
    return _base(env, "XDG_CONFIG_HOME", (".config",))


def cache_root(env: EnvMapping | None = None) -> Path:
    """Return the concordat cache root."""
    return _base(env, "XDG_CACHE_HOME", (".cache",))


def state_root(env: EnvMapping | None = None) -> Path:
    """Return the concordat state root."""
    return _base(env, "XDG_STATE_HOME", (".local", "state"))


def validate_owner(owner: str) -> str:
    """Return *owner* unchanged, raising when it is not a valid owner name."""
    if not _OWNER_PATTERN.match(owner or ""):
        raise ConcordatError(ERROR_INVALID_OWNER.format(owner=owner))
    return owner


def headline_config_path(env: EnvMapping | None = None) -> Path:
    """Return the path of the headline configuration file."""
    return config_root(env) / HEADLINE_FILENAME


def owner_config_dir(owner: str, env: EnvMapping | None = None) -> Path:
    """Return one owner's configuration directory."""
    return config_root(env) / OWNERS_DIRNAME / validate_owner(owner)


def owner_config_path(owner: str, env: EnvMapping | None = None) -> Path:
    """Return one owner's estate configuration file."""
    return owner_config_dir(owner, env) / OWNER_CONFIG_FILENAME


def owner_credentials_path(owner: str, env: EnvMapping | None = None) -> Path:
    """Return one owner's credentials file."""
    return owner_config_dir(owner, env) / CREDENTIALS_FILENAME


def owner_cache_dir(owner: str, env: EnvMapping | None = None) -> Path:
    """Return one owner's cache directory."""
    return cache_root(env) / OWNERS_DIRNAME / validate_owner(owner)


def owner_estates_cache_dir(owner: str, env: EnvMapping | None = None) -> Path:
    """Return the directory caching one owner's estate repositories."""
    return owner_cache_dir(owner, env) / "estates"


def owner_state_dir(owner: str, env: EnvMapping | None = None) -> Path:
    """Return one owner's state directory."""
    return state_root(env) / OWNERS_DIRNAME / validate_owner(owner)


def owner_runs_dir(owner: str, env: EnvMapping | None = None) -> Path:
    """Return the directory holding one owner's OpenTofu run workspaces."""
    return owner_state_dir(owner, env) / "runs"


def tofu_plugin_cache_dir(env: EnvMapping | None = None) -> Path:
    """Return the shared OpenTofu provider plugin cache directory."""
    return cache_root(env) / "tofu" / "plugin-cache"


def _load_headline(env: EnvMapping | None) -> dict[str, typ.Any]:
    path = headline_config_path(env)
    if not path.is_file():
        return {}
    try:
        loaded = _yaml.load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        message = f"cannot read headline configuration {path}: {error}"
        raise ConcordatError(message) from error
    return dict(loaded) if isinstance(loaded, dict) else {}


def get_active_owner(env: EnvMapping | None = None) -> str | None:
    """Return the active GitHub owner from the headline config, if any."""
    owner = _load_headline(env).get(ACTIVE_OWNER_KEY)
    if isinstance(owner, str) and owner.strip():
        return owner.strip()
    return None


def set_active_owner(owner: str, env: EnvMapping | None = None) -> None:
    """Record *owner* as the active GitHub owner in the headline config.

    Keys other than ``github_owner`` are preserved so the headline file can
    grow additional settings without this writer discarding them.
    """
    validate_owner(owner)
    data = _load_headline(env)
    data[ACTIVE_OWNER_KEY] = owner
    path = headline_config_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(data, handle)
