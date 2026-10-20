"""Select the repository pin rustup consults without changing canonical policy facts.

The toolchain policy audits a canonical TOML pin deliberately. Development
builds instead need rustup's effective choice: the nearest repository directory,
with the extensionless filename winning when both spellings are present.
"""

from __future__ import annotations

import tomllib
import typing as typ

from .fs_probe import probe_file
from .toolchain import CHANNEL_UNKNOWN, classify_channel

if typ.TYPE_CHECKING:
    import pathlib

    from .toolchain import ToolchainFacts

_PIN_NAMES: typ.Final = ("rust-toolchain", "rust-toolchain.toml")


def _facts(path: str, channel: str | None, error: str | None) -> ToolchainFacts:
    """Keep selection and read failures attached to the selected pin only."""
    return {
        "path": path,
        "channel": channel,
        "channel_kind": classify_channel(channel)
        if channel is not None
        else CHANNEL_UNKNOWN,
        "parse_error": error,
    }


def _channel(content: str, *, legacy: bool) -> str | None:
    """Parse the legacy plain channel or either filename's TOML format."""
    match legacy, content.split():
        case True, [channel] if not channel.startswith("["):
            return channel
    document = tomllib.loads(content)
    toolchain = document.get("toolchain")
    if not isinstance(toolchain, dict):
        return None
    candidate = toolchain.get("channel")
    return candidate if isinstance(candidate, str) else None


def _read_pin(path: pathlib.Path, checkout: pathlib.Path) -> ToolchainFacts:
    """Read selected evidence only when it remains inside the repository."""
    relative = path.relative_to(checkout).as_posix()
    try:
        if not path.resolve().is_relative_to(checkout.resolve()):
            return _facts(relative, None, f"{path.name} resolves outside the checkout")
        channel = _channel(
            path.read_text(encoding="utf-8"), legacy=path.name == "rust-toolchain"
        )
    except (OSError, ValueError, RuntimeError) as error:
        return _facts(relative, None, str(error))
    return _facts(relative, channel, None)


def inspect_effective_toolchain(
    checkout: pathlib.Path, directory: pathlib.Path
) -> ToolchainFacts | None:
    """Resolve the nearest rustup pin, bounded by the checkout root.

    Returns
    -------
    ToolchainFacts | None
        Selected pin facts or its error; ``None`` when no repository pin exists.
    """
    depth = len(directory.relative_to(checkout).parts)
    ancestors = [directory, *directory.parents][: depth + 1]
    for parent in ancestors:
        for name in _PIN_NAMES:
            path = parent / name
            probe = probe_file(path)
            if probe.read_error is not None:
                return _facts(
                    path.relative_to(checkout).as_posix(), None, probe.read_error
                )
            if probe.present:
                return _read_pin(path, checkout)
    return None
