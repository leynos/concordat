"""Tests for installing a pinned release binary in CI.

The install runs end to end against a stand-in download, so the digest check,
the staged write and the `PATH` publication are exercised as CI runs them, and
each failure is shown to install nothing.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import typing as typ
from pathlib import Path

import pytest

from scripts import install_release_binary as installer

_SCRIPT: typ.Final = Path(installer.__file__)
_BODY: typ.Final = b"\x7fELF stand-in release binary"
_RELEASES: typ.Final = "https://example.com/releases/download"


def _pin(body: bytes = _BODY) -> dict[str, str]:
    """Return the four ``MAKEUTIL_*`` variables pinning ``body``."""
    return {
        "MAKEUTIL_VERSION": "0.1.0",
        "MAKEUTIL_ASSET": "makeutil-x86_64-unknown-linux-musl",
        "MAKEUTIL_SHA256": hashlib.sha256(body).hexdigest(),
        "MAKEUTIL_RELEASES": _RELEASES,
    }


def _environment(tmp_path: Path, pin: dict[str, str]) -> dict[str, str]:
    """Return a CI-shaped environment rooted in ``tmp_path``."""
    return {
        **pin,
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "GITHUB_PATH": str(tmp_path / "github_path"),
    }


def _serving(body: bytes, requested: list[str]) -> typ.Callable[[str], bytes]:
    """Return a download stand-in that records each URL and returns ``body``."""

    def fetch(url: str) -> bytes:
        requested.append(url)
        return body

    return fetch


def test_a_matching_asset_is_installed_and_put_on_path(tmp_path: Path) -> None:
    """The pinned asset is fetched, installed executable, and published."""
    requested: list[str] = []
    environment = _environment(tmp_path, _pin())

    status = installer.run("makeutil", environment, _serving(_BODY, requested))

    binary = tmp_path / "runner" / "makeutil-bin" / "makeutil"
    assert status == 0, "a matching digest must install"
    assert requested == [f"{_RELEASES}/v0.1.0/makeutil-x86_64-unknown-linux-musl"], (
        "the release URL must be built from the pin"
    )
    assert binary.read_bytes() == _BODY, "the installed file must be the download"
    assert os.access(binary, os.X_OK), "the installed file must be executable"
    assert (tmp_path / "github_path").read_text() == f"{binary.parent}\n", (
        "the binary's directory must be appended to GITHUB_PATH"
    )


def test_a_digest_mismatch_installs_nothing(tmp_path: Path) -> None:
    """A replaced asset fails the install and leaves nothing to run."""
    environment = _environment(tmp_path, _pin(b"the release that was pinned"))

    status = installer.run("makeutil", environment, _serving(_BODY, []))

    bin_dir = tmp_path / "runner" / "makeutil-bin"
    assert status == 1, "a digest mismatch must fail the install"
    assert not bin_dir.exists() or not any(bin_dir.iterdir()), (
        "no file may be left behind, staged or installed"
    )
    assert not (tmp_path / "github_path").exists(), "PATH must not be published"


def test_a_failed_download_installs_nothing(tmp_path: Path) -> None:
    """A download error fails the install before anything is written."""

    def unreachable(url: str) -> bytes:
        message = f"could not download {url}"
        raise installer.InstallError(message)

    status = installer.run("makeutil", _environment(tmp_path, _pin()), unreachable)

    assert status == 1, "a failed download must fail the install"
    assert not (tmp_path / "runner").exists(), "nothing may be written"
    assert not (tmp_path / "github_path").exists(), "PATH must not be published"


@pytest.mark.parametrize(
    "unset",
    ["MAKEUTIL_VERSION", "MAKEUTIL_ASSET", "MAKEUTIL_SHA256", "MAKEUTIL_RELEASES"],
)
def test_an_incomplete_pin_is_refused(tmp_path: Path, unset: str) -> None:
    """Each pin variable is required; none may default to anything."""
    pin = _pin()
    del pin[unset]
    requested: list[str] = []

    status = installer.run(
        "makeutil", _environment(tmp_path, pin), _serving(_BODY, requested)
    )

    assert status == 1, f"an install without {unset} must fail"
    assert requested == [], f"nothing may be downloaded without {unset}"


def test_a_non_https_url_is_refused() -> None:
    """The real download refuses any scheme but HTTPS before connecting."""
    with pytest.raises(installer.InstallError, match="non-HTTPS"):
        installer.fetch_https("http://example.com/releases/tool")


def test_the_cli_reports_an_incomplete_pin_as_exit_status_1(tmp_path: Path) -> None:
    """The script run as CI runs it reads its pin from the environment.

    The environment is set on the child process only, and the pin is left
    incomplete so no download is attempted.
    """
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_PATH": str(tmp_path / "github_path"),
    }
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(_SCRIPT), "install", "makeutil"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1, completed.stderr
    assert "MAKEUTIL_VERSION" in completed.stderr, completed.stderr
