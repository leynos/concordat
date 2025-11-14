"""Shared helpers for working with Git remotes."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pygit2
from pygit2 import KeypairFromAgent, RemoteCallbacks


def build_remote_callbacks(specification: str) -> RemoteCallbacks | None:
    """Return SSH callbacks for the provided remote URL if possible."""
    if _looks_like_local_path(specification):
        return None

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


def _looks_like_local_path(specification: str) -> bool:
    if specification.startswith("git@") or specification.startswith("ssh://"):
        return False
    parsed = urlparse(specification)
    if parsed.scheme and parsed.scheme not in {"file"}:
        return False
    # Treat strings without a scheme but pointing at filesystem locations as local
    candidate = Path(specification)
    return specification.startswith("/") or candidate.exists()
