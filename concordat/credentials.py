"""Owner-scoped credential fallbacks for the concordat CLI.

Credentials resolve in a fixed order: an explicit CLI flag wins, then a
process environment variable, then the active owner's credentials file
(``$XDG_CONFIG_HOME/concordat/owners/<owner>/credentials.yaml``). The file
maps credential environment-variable names to values, for example::

    GITHUB_TOKEN: ghp_example
    SCW_ACCESS_KEY: SCWXXXXXXXXXXXXXXXXX
    SCW_SECRET_KEY: example-secret

Only the keys named in :data:`CREDENTIAL_KEYS` are honoured; anything else
is ignored. Keep the file readable only by its owner (``chmod 600``).
Credentials are never written by concordat.
"""

from __future__ import annotations

import os
import typing as typ

from ruamel.yaml import YAML

from . import xdg

CREDENTIAL_KEYS: typ.Final = (
    "GITHUB_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "SCW_ACCESS_KEY",
    "SCW_SECRET_KEY",
    "SPACES_ACCESS_KEY_ID",
    "SPACES_SECRET_ACCESS_KEY",
)

_yaml = YAML(typ="safe")


def _environ(env: xdg.EnvMapping | None) -> xdg.EnvMapping:
    return os.environ if env is None else env


def load_credentials(
    owner: str,
    *,
    env: xdg.EnvMapping | None = None,
) -> dict[str, str]:
    """Return the recognized credentials stored for *owner*, if any."""
    path = xdg.owner_credentials_path(owner, env)
    if not path.is_file():
        return {}
    loaded = _yaml.load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return {
        key: str(value).strip()
        for key, value in loaded.items()
        if key in CREDENTIAL_KEYS and str(value).strip()
    }


def credential_environment(
    *,
    owner: str | None = None,
    env: xdg.EnvMapping | None = None,
) -> dict[str, str]:
    """Return the environment overlaid with file-backed credential fallbacks.

    Environment variables always win; file values only fill gaps. When
    *owner* is omitted the headline active owner scopes the file; with no
    resolvable owner the environment passes through unchanged.
    """
    source = _environ(env)
    merged = dict(source)
    resolved_owner = owner or xdg.get_active_owner(source)
    if resolved_owner is None:
        return merged
    for key, value in load_credentials(resolved_owner, env=source).items():
        merged.setdefault(key, value)
    return merged


def github_token(
    *,
    owner: str | None = None,
    env: xdg.EnvMapping | None = None,
) -> str | None:
    """Resolve the GitHub token from the environment or credentials file."""
    return credential_environment(owner=owner, env=env).get("GITHUB_TOKEN") or None
