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

For a tar.gz asset, also set ``<TOOL>_ARCHIVE_FORMAT=tar.gz``,
``<TOOL>_ARCHIVE_MEMBER`` to its exact ordinary-file name, and
``<TOOL>_ARCHIVE_MEMBER_SIZE`` to its exact positive byte length. The archive
hash is verified before parsing; only the selected bytes are ever staged.

The binary lands in ``$RUNNER_TEMP/<tool>-bin`` and that directory is appended
to the file named by ``GITHUB_PATH``, as GitHub Actions expects.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import os
import sys
import tarfile
import tempfile
import typing as typ
import urllib.error
import urllib.request
import zlib
from pathlib import Path, PurePosixPath

from cyclopts import App

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# A release asset is a few megabytes; a download still running after this long
# is hung on the network, and the install must fail rather than stall CI.
DOWNLOAD_TIMEOUT_SECONDS: typ.Final = 120

app = App()


class InstallError(RuntimeError):
    """The release binary could not be installed as pinned."""


def _has_unsafe_path_component(parts: cabc.Sequence[str]) -> bool:
    """Return whether a path has an empty or non-canonical component."""
    return any(part in {"", ".", ".."} for part in parts)


def _has_unsafe_portable_path_character(name: str) -> bool:
    """Return whether a member name contains a non-portable path character."""
    return "\\" in name or ":" in name


def _validate_member_name(name: str) -> None:
    """Refuse non-canonical archive paths before selecting any member."""
    parts = name.split("/")
    if not name or PurePosixPath(name).is_absolute():
        message = f"unsafe archive member name: {name!r}"
        raise InstallError(message)
    if _has_unsafe_path_component(parts):
        message = f"unsafe archive member name: {name!r}"
        raise InstallError(message)
    if _has_unsafe_portable_path_character(name):
        message = f"unsafe archive member name: {name!r}"
        raise InstallError(message)


def _parse_member_size(size_text: str) -> int:
    """Parse and validate the approved archive member's positive byte size."""
    try:
        size = int(size_text)
    except ValueError as error:
        message = "archive member size must be a positive integer"
        raise InstallError(message) from error
    if size <= 0:
        message = "archive member size must be a positive integer"
        raise InstallError(message)
    return size


@dataclasses.dataclass(frozen=True, slots=True)
class ArchiveMember:
    """The exact ordinary member and byte length approved by a release pin."""

    name: str
    size: int

    @classmethod
    def from_environment(
        cls, prefix: str, environment: cabc.Mapping[str, str]
    ) -> ArchiveMember | None:
        """Read an optional complete tar.gz contract, leaving raw pins unchanged.

        Returns
        -------
        ArchiveMember | None
            The approved member, or None when all archive fields are absent.

        Raises
        ------
        InstallError
            If archive fields are incomplete, unsupported or unsafe.
        """
        names = ("ARCHIVE_FORMAT", "ARCHIVE_MEMBER", "ARCHIVE_MEMBER_SIZE")
        if not any(f"{prefix}_{name}" in environment for name in names):
            return None
        values = [environment.get(f"{prefix}_{name}", "") for name in names]
        archive_format, member, size_text = values
        if archive_format != "tar.gz":
            message = "archive format must be explicitly tar.gz"
            raise InstallError(message)
        _validate_member_name(member)
        size = _parse_member_size(size_text)
        return cls(member, size)


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseAsset:
    """One pinned release asset: where it is and what it must hash to."""

    url: str
    sha256: str
    archive: ArchiveMember | None = None

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
        return cls(
            url=url,
            sha256=values["SHA256"].lower(),
            archive=ArchiveMember.from_environment(prefix, environment),
        )


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
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # ruff: ignore[suspicious-url-open-usage] - scheme checked above
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
    binary = body if asset.archive is None else _read_archive(body, asset.archive)
    _stage_binary(binary, destination)
    return destination


def _validate_archive_member(member: tarfile.TarInfo) -> None:
    """Reject unsafe names and nonordinary or sparse archive entries."""
    _validate_member_name(member.name)
    if (
        member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}
        or member.sparse is not None
    ):
        message = f"archive member is not an ordinary file: {member.name}"
        raise InstallError(message)


def _select_approved_member(
    members: cabc.Sequence[tarfile.TarInfo], approved: ArchiveMember
) -> tarfile.TarInfo:
    """Validate every archive entry before selecting the pinned ordinary file."""
    for member in members:
        _validate_archive_member(member)
    selected = [member for member in members if member.name == approved.name]
    if len(selected) != 1:
        message = "archive must contain exactly one approved member"
        raise InstallError(message)
    member = selected[0]
    if member.size != approved.size:
        message = "archive member size differs from its pin"
        raise InstallError(message)
    return member


def _read_archive(body: bytes, approved: ArchiveMember) -> bytes:
    """Read one exact-size ordinary member without extracting an archive tree.

    Returns
    -------
    bytes
        The authenticated selected executable's bytes.

    Raises
    ------
    InstallError
        If archive contents, names, member type or byte length are invalid.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            members = archive.getmembers()
            member = _select_approved_member(members, approved)
            stream = archive.extractfile(member)
            if stream is None:
                message = "archive member has no ordinary file contents"
                raise InstallError(message)
            with stream:
                binary = stream.read(approved.size + 1)
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
        message = f"could not read release archive: {error}"
        raise InstallError(message) from error
    if len(binary) != approved.size:
        message = "archive member bytes differ from their pinned length"
        raise InstallError(message)
    return binary


def _stage_binary(body: bytes, destination: Path) -> None:
    """Atomically replace a binary and remove staging files on every failure.

    Raises
    ------
    InstallError
        If the executable cannot be staged or installed.
    """
    staged_path: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as staged:
            staged_path = Path(staged.name)
            staged.write(body)
        staged_path.chmod(0o755)
        staged_path.replace(destination)
    except OSError as error:
        message = f"could not install release binary: {error}"
        raise InstallError(message) from error
    finally:
        if staged_path is not None:
            try:
                staged_path.unlink(missing_ok=True)
            except OSError as error:
                message = f"could not remove staged binary: {error}"
                raise InstallError(message) from error


def publish_path(directory: Path, github_path: Path) -> None:
    """Append ``directory`` to the file GitHub Actions reads `PATH` entries from."""
    try:
        with github_path.open("a", encoding="utf-8") as path_file:
            path_file.write(f"{directory}\n")
    except OSError as error:
        message = f"could not publish installed binary directory: {error}"
        raise InstallError(message) from error


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
