"""Shared helpers for working with Git remotes."""

from __future__ import annotations

from urllib.parse import urlparse

import pygit2
from pygit2 import KeypairFromAgent, RemoteCallbacks


def build_remote_callbacks(specification: str) -> RemoteCallbacks | None:
    """Return SSH callbacks for the provided remote URL if possible."""
    username = _username_for(specification)
    try:
        credentials = KeypairFromAgent(username)
    except pygit2.GitError:
        return None
    return RemoteCallbacks(credentials=credentials)


def _username_for(specification: str) -> str:
    if specification.startswith("git@"):
        return specification.split("@", 1)[0]
    parsed = urlparse(specification)
    if parsed.scheme in {"ssh", "git"} and parsed.username:
        return parsed.username
    return "git"
