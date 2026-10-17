"""Tests for the locally cached, digest-verified Vale style archive."""

from __future__ import annotations

import hashlib
import io
import pathlib
import sys
import typing as typ
import zipfile

import pytest

from scripts import install_release_binary as installer
from scripts import vale_style_archive as style_archive

_ARCHIVE_URL: typ.Final[str] = (
    "https://github.com/leynos/concordat-vale/releases/download/v0.3.1/"
    "concordat-0.3.1.zip"
)
_ARCHIVE_SHA256: typ.Final[str] = (
    "af015d043de1deda51bc0b583d2abf5a76dff73c896196dbf58ad4d184380bbb"
)
_ARCHIVE_RELATIVE_PATH: typ.Final[str] = ".vale/packages/concordat-0.3.1.zip"
_INSTALL_INSTRUCTION: typ.Final[str] = "make install-vale-styles"
_SNAPSHOTS: typ.Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parents[2] / "tests" / "unit" / "snapshots"
)


def _snapshot(name: str) -> str:
    """Read a checked-in plain-text Vale archive diagnostic snapshot."""
    return (_SNAPSHOTS / name).read_text(encoding="utf-8")


def _zip_body() -> bytes:
    """Build a small valid ZIP body without accessing the network."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("styles/config/vocabularies/concordat/accept.txt", b"Vale\n")
    return output.getvalue()


def _digest(body: bytes) -> str:
    """Return the SHA-256 used by the fixture's injected pin."""
    return hashlib.sha256(body).hexdigest()


def _forbid_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any attempted HTTP acquisition fail this local-cache test."""

    def fail_fetch(url: str) -> bytes:
        pytest.fail(f"cache validation attempted to fetch {url}")

    monkeypatch.setattr(installer, "fetch_https", fail_fetch)


def test_constants_match_the_approved_release_and_checkout_path() -> None:
    """The helper uses the approved package pin and fixed checkout cache path."""
    expected_path = (
        pathlib.Path(style_archive.__file__).resolve().parents[1]
        / _ARCHIVE_RELATIVE_PATH
    )

    assert style_archive.ARCHIVE_URL == _ARCHIVE_URL
    assert style_archive.ARCHIVE_SHA256 == _ARCHIVE_SHA256
    assert style_archive.ARCHIVE_RELATIVE_PATH == _ARCHIVE_RELATIVE_PATH
    assert expected_path == style_archive.ARCHIVE_PATH


def test_install_stages_the_complete_zip_at_a_path_with_spaces(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installation preserves the whole ZIP and requests the fixed URL."""
    body = _zip_body()
    requested: list[str] = []
    destination = tmp_path / "Vale package cache" / "concordat-0.3.1.zip"
    monkeypatch.setattr(style_archive, "ARCHIVE_SHA256", _digest(body))

    def fetch(url: str) -> bytes:
        requested.append(url)
        return body

    installed = style_archive.install_archive(destination, fetch)

    assert installed == destination
    assert requested == [_ARCHIVE_URL]
    assert destination.read_bytes() == body


def test_digest_mismatch_preserves_an_existing_destination(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body outside the pin cannot replace a previously cached package."""
    expected = b"the package bytes selected by the test pin"
    body = _zip_body()
    requested: list[str] = []
    destination = tmp_path / "packages" / "concordat-0.3.1.zip"
    destination.parent.mkdir()
    destination.write_bytes(b"previous verified package")
    monkeypatch.setattr(style_archive, "ARCHIVE_SHA256", _digest(expected))

    def fetch(url: str) -> bytes:
        requested.append(url)
        return body

    with pytest.raises(installer.InstallError, match="digest mismatch"):
        style_archive.install_archive(destination, fetch)

    assert requested == [_ARCHIVE_URL]
    assert destination.read_bytes() == b"previous verified package"
    assert list(destination.parent.iterdir()) == [destination]


def test_download_failure_leaves_no_destination_or_parent(
    tmp_path: pathlib.Path,
) -> None:
    """A failed fetch propagates without creating an archive or staging file."""
    requested: list[str] = []
    destination = tmp_path / "packages" / "concordat-0.3.1.zip"

    def fail_fetch(url: str) -> bytes:
        requested.append(url)
        message = "stand-in download failure"
        raise installer.InstallError(message)

    with pytest.raises(installer.InstallError, match="stand-in download failure"):
        style_archive.install_archive(destination, fail_fetch)

    assert requested == [_ARCHIVE_URL]
    assert not destination.parent.exists()


def test_check_accepts_a_valid_cached_zip_without_fetching(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached archive matching the pin passes local verification only."""
    body = _zip_body()
    destination = tmp_path / "packages" / "concordat-0.3.1.zip"
    destination.parent.mkdir()
    destination.write_bytes(body)
    monkeypatch.setattr(style_archive, "ARCHIVE_SHA256", _digest(body))
    _forbid_fetch(monkeypatch)

    assert style_archive.check_archive(destination) == destination
    assert style_archive.run("check", destination) == 0


def test_check_rejects_a_tampered_archive_without_fetching(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A modified cache reports both digests and the repair command."""
    expected = b"the expected archive body"
    body = _zip_body()
    destination = tmp_path / "packages" / "concordat-0.3.1.zip"
    destination.parent.mkdir()
    destination.write_bytes(body)
    monkeypatch.setattr(style_archive, "ARCHIVE_SHA256", _digest(expected))
    _forbid_fetch(monkeypatch)

    with pytest.raises(installer.InstallError) as failure:
        style_archive.check_archive(destination)

    message = str(failure.value)
    assert _INSTALL_INSTRUCTION in message
    assert _digest(expected) in message
    assert _digest(body) in message

    status = style_archive.run("check", destination)
    output = capsys.readouterr()
    normalized = output.err.replace(str(destination), "<archive-path>")
    normalized = normalized.replace(_digest(expected), "<expected-sha256>")
    normalized = normalized.replace(_digest(body), "<actual-sha256>")

    assert status == 1
    assert output.out == ""
    assert _INSTALL_INSTRUCTION in output.err
    assert normalized == _snapshot("vale_style_archive_digest_mismatch.txt")


def test_check_reports_a_missing_archive_and_repair_command(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing local package gives actionable guidance and preserves its cause."""
    destination = tmp_path / "missing" / "concordat-0.3.1.zip"
    _forbid_fetch(monkeypatch)

    with pytest.raises(installer.InstallError, match=_INSTALL_INSTRUCTION) as failure:
        style_archive.check_archive(destination)

    assert isinstance(failure.value.__cause__, FileNotFoundError)


def test_check_wraps_a_local_read_error_without_fetching(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path that cannot be read yields the same actionable local failure."""
    destination = tmp_path / "package-directory"
    destination.mkdir()
    _forbid_fetch(monkeypatch)

    with pytest.raises(installer.InstallError, match=_INSTALL_INSTRUCTION) as failure:
        style_archive.check_archive(destination)

    assert isinstance(failure.value.__cause__, OSError)


def test_cli_returns_nonzero_and_prints_one_actionable_diagnostic(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command-line boundary reports a missing cache as one failure."""
    destination = tmp_path / "missing" / "concordat-0.3.1.zip"
    actual_run = style_archive.run
    original_read_bytes = pathlib.Path.read_bytes

    def run_with_test_destination(operation: str) -> int:
        return actual_run(operation, destination)

    def read_stable_missing_archive(path: pathlib.Path) -> bytes:
        if path == destination:
            message = "stable missing archive fixture"
            raise FileNotFoundError(message)
        return original_read_bytes(path)

    monkeypatch.setattr(style_archive, "run", run_with_test_destination)
    monkeypatch.setattr(sys, "argv", ["vale_style_archive", "check"])
    monkeypatch.setattr(pathlib.Path, "read_bytes", read_stable_missing_archive)
    _forbid_fetch(monkeypatch)

    status = style_archive.main()
    output = capsys.readouterr()

    assert status == 1
    assert output.out == ""
    assert output.err.count("\n") == 1
    assert _INSTALL_INSTRUCTION in output.err
    assert output.err.replace(str(destination), "<archive-path>") == _snapshot(
        "vale_style_archive_missing_cache.txt"
    )


def test_run_rejects_an_unknown_operation(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unsupported operation returns failure without touching the cache."""
    destination = tmp_path / "packages" / "concordat-0.3.1.zip"

    status = style_archive.run("publish", destination)
    output = capsys.readouterr()

    assert status == 1
    assert output.out == ""
    assert "unknown Vale style archive operation: publish" in output.err
    assert output.err == _snapshot("vale_style_archive_unknown_operation.txt")
    assert not destination.parent.exists()
