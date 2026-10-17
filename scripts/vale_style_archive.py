"""Install and locally verify the pinned Concordat Vale style archive."""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
import typing as typ

from scripts import install_release_binary as installer

if typ.TYPE_CHECKING:
    import collections.abc as cabc

ARCHIVE_URL: typ.Final[str] = (
    "https://github.com/leynos/concordat-vale/releases/download/v0.3.1/"
    "concordat-0.3.1.zip"
)
ARCHIVE_SHA256: typ.Final[str] = (
    "af015d043de1deda51bc0b583d2abf5a76dff73c896196dbf58ad4d184380bbb"
)
ARCHIVE_RELATIVE_PATH: typ.Final[str] = ".vale/packages/concordat-0.3.1.zip"
ARCHIVE_PATH: typ.Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parent.parent / ARCHIVE_RELATIVE_PATH
)
_INSTALL_INSTRUCTION: typ.Final[str] = "make install-vale-styles"


def install_archive(
    destination: pathlib.Path = ARCHIVE_PATH,
    fetch: cabc.Callable[[str], bytes] = installer.fetch_https,
) -> pathlib.Path:
    """Install the whole release ZIP through the shared verified-asset path.

    The shared installer verifies the fixed SHA-256 before its atomic staging
    step. The ZIP remains opaque here because Vale consumes the package file.
    It propagates the installer's ``InstallError`` when acquisition, digest
    validation or atomic installation fails.

    Returns
    -------
    pathlib.Path
        The installed archive path.
    """
    asset = installer.ReleaseAsset(ARCHIVE_URL, ARCHIVE_SHA256, archive=None)
    return installer.install_release(asset, destination, fetch)


def check_archive(destination: pathlib.Path = ARCHIVE_PATH) -> pathlib.Path:
    """Return a cached archive only when its bytes match the fixed release pin.

    Returns
    -------
    pathlib.Path
        The same path after its bytes match the release pin.

    Raises
    ------
    InstallError
        If the archive is missing, unreadable or differs from the pinned digest.
    """
    try:
        body = destination.read_bytes()
    except OSError as error:
        message = (
            f"Vale style archive is unavailable at {destination}: {error}; "
            f"run {_INSTALL_INSTRUCTION}"
        )
        raise installer.InstallError(message) from error

    actual_digest = hashlib.sha256(body).hexdigest()
    if actual_digest != ARCHIVE_SHA256:
        message = (
            f"Vale style archive digest mismatch at {destination}: "
            f"expected {ARCHIVE_SHA256}, got {actual_digest}; "
            f"run {_INSTALL_INSTRUCTION}"
        )
        raise installer.InstallError(message)
    return destination


def run(
    operation: str,
    destination: pathlib.Path = ARCHIVE_PATH,
    fetch: cabc.Callable[[str], bytes] = installer.fetch_https,
) -> int:
    """Run one archive operation and report expected installation errors once.

    Returns
    -------
    int
        Zero on success and one when the operation cannot complete.
    """
    if operation not in {"install", "check"}:
        message = f"unknown Vale style archive operation: {operation}"
        print(message, file=sys.stderr)
        return 1

    try:
        if operation == "install":
            install_archive(destination, fetch)
        else:
            check_archive(destination)
    except installer.InstallError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Parse the operation and use the fixed checkout cache path.

    Returns
    -------
    int
        The status returned by :func:`run`.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "check"))
    arguments = parser.parse_args()
    return run(arguments.operation)


if __name__ == "__main__":
    raise SystemExit(main())
