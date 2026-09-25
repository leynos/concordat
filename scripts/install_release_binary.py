#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13"
# dependencies = ["cyclopts"]
# ///
"""Install a tool's released binary after verifying it against a pinned digest.

CI lanes that shell out to a tool install it from a release asset rather than
compiling it. This script downloads the asset over HTTPS, requires its SHA-256
digest to equal the one pinned in the workflow (never the release's own
checksum file, which a replaced asset would carry too), and only then places
it on `PATH`. A download failure or a digest mismatch installs nothing.

The release is described by four environment variables named after the tool,
which the workflow sets at job level:

    MAKEUTIL_VERSION=0.1.0
    MAKEUTIL_ASSET=makeutil-x86_64-unknown-linux-musl
    MAKEUTIL_SHA256=<64 hex digits>
    MAKEUTIL_RELEASES=https://github.com/leynos/makeutil/releases/download
    uv run scripts/install_release_binary.py install makeutil

The binary lands in ``$RUNNER_TEMP/<tool>-bin`` and that directory is appended
to the file named by ``GITHUB_PATH``, as GitHub Actions expects.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import sys
import tempfile
import typing as typ
import urllib.error
import urllib.request
from pathlib import Path

from cyclopts import App

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# A release asset is a few megabytes; a download still running after this long
# is hung on the network, and the install must fail rather than stall CI.
DOWNLOAD_TIMEOUT_SECONDS: typ.Final = 120

app = App()


class InstallError(RuntimeError):
    """The release binary could not be installed as pinned."""


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseAsset:
    """One pinned release asset: where it is and what it must hash to."""

    url: str
    sha256: str

    @classmethod
    def from_environment(
        cls, tool: str, environment: cabc.Mapping[str, str]
    ) -> ReleaseAsset:
        """Read the tool's release pin from ``<TOOL>_*`` variables.

        Returns
        -------
        ReleaseAsset
            The asset URL and the lower-case digest it must match.

        Raises
        ------
        InstallError
            If any of the four variables is unset or empty.

        Examples
        --------
        >>> ReleaseAsset.from_environment("tool", {
        ...     "TOOL_VERSION": "1.0.0", "TOOL_ASSET": "tool-x86_64",
        ...     "TOOL_SHA256": "ab" * 32, "TOOL_RELEASES": "https://example.com/dl",
        ... }).url
        'https://example.com/dl/v1.0.0/tool-x86_64'
        """
        prefix = tool.upper()
        names = ("VERSION", "ASSET", "SHA256", "RELEASES")
        values = {name: environment.get(f"{prefix}_{name}", "") for name in names}
        missing = [f"{prefix}_{name}" for name, value in values.items() if not value]
        if missing:
            message = f"release pin incomplete; unset: {', '.join(missing)}"
            raise InstallError(message)
        url = f"{values['RELEASES']}/v{values['VERSION']}/{values['ASSET']}"
        return cls(url=url, sha256=values["SHA256"].lower())


def fetch_https(url: str) -> bytes:
    """Download ``url`` over HTTPS and return its body.

    Returns
    -------
    bytes
        The response body.

    Raises
    ------
    InstallError
        If the URL is not HTTPS or the download fails.
    """
    if not url.startswith("https://"):
        message = f"refusing a non-HTTPS release URL: {url}"
        raise InstallError(message)
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310 - scheme checked above
            return response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        message = f"could not download {url}: {error}"
        raise InstallError(message) from error


def install_release(
    asset: ReleaseAsset,
    destination: Path,
    fetch: cabc.Callable[[str], bytes] = fetch_https,
) -> Path:
    """Download ``asset``, verify its digest, and install it at ``destination``.

    The body is written beside the destination and renamed into place only
    after the digest matches, so a mismatch leaves nothing to run.

    Returns
    -------
    Path
        The installed file, which is ``destination``.

    Raises
    ------
    InstallError
        If the download fails or the digest differs from the pin.
    """
    body = fetch(asset.url)
    actual = hashlib.sha256(body).hexdigest()
    if actual != asset.sha256:
        message = (
            f"digest mismatch for {asset.url}: expected {asset.sha256}, got {actual}"
        )
        raise InstallError(message)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as staged:
        staged.write(body)
    staged_path = Path(staged.name)
    staged_path.chmod(0o755)
    staged_path.replace(destination)
    return destination


def publish_path(directory: Path, github_path: Path) -> None:
    """Append ``directory`` to the file GitHub Actions reads `PATH` entries from."""
    with github_path.open("a", encoding="utf-8") as path_file:
        path_file.write(f"{directory}\n")


def _runner_paths(environment: cabc.Mapping[str, str]) -> tuple[Path, Path]:
    """Return the runner's scratch directory and its `PATH` file.

    Returns
    -------
    tuple[Path, Path]
        ``RUNNER_TEMP`` and ``GITHUB_PATH`` as paths.

    Raises
    ------
    InstallError
        If either variable is unset or empty.
    """
    runner_temp = environment.get("RUNNER_TEMP", "")
    github_path = environment.get("GITHUB_PATH", "")
    if not runner_temp or not github_path:
        message = "RUNNER_TEMP and GITHUB_PATH must both be set"
        raise InstallError(message)
    return Path(runner_temp), Path(github_path)


def run(
    tool: str,
    environment: cabc.Mapping[str, str],
    fetch: cabc.Callable[[str], bytes] = fetch_https,
) -> int:
    """Install ``tool`` as pinned in ``environment`` and report an exit status."""
    try:
        asset = ReleaseAsset.from_environment(tool, environment)
        runner_temp, github_path = _runner_paths(environment)
        directory = runner_temp / f"{tool}-bin"
        install_release(asset, directory / tool, fetch)
        publish_path(directory, github_path)
    except InstallError as error:
        print(f"install_release_binary: {error}", file=sys.stderr)
        return 1
    print(f"installed {tool} from {asset.url}")
    return 0


@app.command
def install(tool: str) -> int:
    """Install ``tool``'s pinned release binary and add it to `PATH`."""
    return run(tool, os.environ)


if __name__ == "__main__":  # pragma: no cover - exercised via CLI
    sys.exit(app())
