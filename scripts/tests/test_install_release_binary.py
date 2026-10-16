"""Tests for installing a pinned release binary in CI.

The install runs end to end against a stand-in download, so the digest check,
the staged write and the `PATH` publication are exercised as CI runs them, and
each failure is shown to install nothing.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import typing as typ
import zlib
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
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and script
        [sys.executable, str(_SCRIPT), "install", "makeutil"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1, completed.stderr
    assert "MAKEUTIL_VERSION" in completed.stderr, completed.stderr


def _archive(entries: list[tuple[str, bytes, bytes]]) -> bytes:
    """Build a release-shaped gzip tar without touching the filesystem."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, body, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(body) if kind == tarfile.REGTYPE else 0
            member.linkname = (
                "tool" if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE} else ""
            )
            archive.addfile(member, io.BytesIO(body))
    return output.getvalue()


def _archive_pin(body: bytes) -> dict[str, str]:
    """Pin one exact-size executable member of an authenticated archive."""
    return {
        **_pin(body),
        "MAKEUTIL_ARCHIVE_FORMAT": "tar.gz",
        "MAKEUTIL_ARCHIVE_MEMBER": "tool",
        "MAKEUTIL_ARCHIVE_MEMBER_SIZE": str(len(_BODY)),
    }


@pytest.mark.parametrize(
    ("name", "unsafe"),
    [
        ("bin/tool", False),
        ("", True),
        ("/tool", True),
        ("a//tool", True),
        ("./tool", True),
        ("a/../tool", True),
    ],
)
def test_archive_path_components_are_canonical(name: str, *, unsafe: bool) -> None:
    """Empty and relative components cannot acquire canonical path meaning."""
    assert installer._has_unsafe_member_components(name) is unsafe


@pytest.mark.parametrize(
    ("name", "unsafe"),
    [("bin/tool", False), ("a\\tool", True), ("C:tool", True)],
)
def test_archive_paths_refuse_platform_syntax(name: str, *, unsafe: bool) -> None:
    """A POSIX member cannot be reinterpreted as Windows path syntax."""
    assert installer._has_platform_path_syntax(name) is unsafe


@pytest.mark.parametrize("text", ["1", "27", "+2", " 3 "])
def test_archive_member_size_parser_preserves_integer_inputs(text: str) -> None:
    """The size parser retains the previously accepted positive integer grammar."""
    assert installer._positive_member_size(text) == int(text)


@pytest.mark.parametrize("text", ["", "0", "-1", "1.5"])
def test_archive_member_size_parser_refuses_nonpositive_inputs(text: str) -> None:
    """Invalid and non-positive sizes share the established diagnostic."""
    with pytest.raises(installer.InstallError, match="must be a positive integer"):
        installer._positive_member_size(text)


def test_archive_member_validation_refuses_sparse_regular_files() -> None:
    """Regular type flags alone do not authorize sparse members."""
    output = io.BytesIO()
    member = tarfile.TarInfo("tool")
    member.size = 1
    member.pax_headers = {"GNU.sparse.map": "0,1", "GNU.sparse.realsize": "2"}
    with tarfile.open(
        fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT
    ) as archive:
        archive.addfile(member, io.BytesIO(b"x"))
    with tarfile.open(fileobj=io.BytesIO(output.getvalue()), mode="r:gz") as archive:
        parsed_member = archive.getmember("tool")

    assert parsed_member.isreg()
    assert parsed_member.sparse is not None
    with pytest.raises(installer.InstallError, match="not an ordinary file"):
        installer._validate_archive_member(parsed_member)


def test_archive_validation_checks_members_after_the_selected_file() -> None:
    """Validation traverses the whole archive before any member is selected."""
    body = _archive([
        ("tool", _BODY, tarfile.REGTYPE),
        ("sibling", b"", tarfile.SYMTYPE),
    ])
    with (
        tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive,
        pytest.raises(installer.InstallError, match="not an ordinary file"),
    ):
        installer._validated_archive_members(archive)


def test_archive_selection_returns_the_exact_pinned_member() -> None:
    """A sibling regular file cannot displace the approved executable."""
    sibling = tarfile.TarInfo("LICENSE")
    selected = tarfile.TarInfo("tool")
    selected.size = len(_BODY)
    approved = installer.ArchiveMember("tool", len(_BODY))
    assert installer._select_archive_member([sibling, selected], approved) is selected


def test_archive_member_reader_returns_exact_bytes() -> None:
    """An ordinary selected stream is read through the bounded reader."""
    body = _archive([("tool", _BODY, tarfile.REGTYPE)])
    approved = installer.ArchiveMember("tool", len(_BODY))
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        member = archive.getmembers()[0]
        assert installer._read_member_bytes(archive, member, approved) == _BODY


def test_archive_installs_only_the_named_member(tmp_path: Path) -> None:
    """A release archive installs executable bytes rather than the tar stream."""
    body = _archive([
        ("LICENSE", b"licence", tarfile.REGTYPE),
        ("tool", _BODY, tarfile.REGTYPE),
    ])
    environment = _environment(tmp_path, _archive_pin(body))

    status = installer.run("makeutil", environment, _serving(body, []))

    binary = tmp_path / "runner" / "makeutil-bin" / "makeutil"
    assert status == 0, "the authenticated named member must install"
    assert binary.read_bytes() == _BODY, "only member bytes may be installed"
    assert os.access(binary, os.X_OK), "the selected member must be executable"
    assert list(binary.parent.iterdir()) == [binary], "other entries must not escape"
    assert (tmp_path / "github_path").read_text() == f"{binary.parent}\n"


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param([], id="missing"),
        pytest.param([("tool", _BODY, tarfile.REGTYPE)] * 2, id="duplicate"),
        pytest.param(
            [
                ("tool", _BODY, tarfile.REGTYPE),
                ("../sibling", b"unsafe", tarfile.REGTYPE),
            ],
            id="selected-before-unsafe-sibling-path",
        ),
        pytest.param(
            [
                ("tool", _BODY, tarfile.REGTYPE),
                ("sibling", b"", tarfile.SYMTYPE),
            ],
            id="selected-before-unsafe-sibling-type",
        ),
        pytest.param([("../tool", _BODY, tarfile.REGTYPE)], id="traversal"),
        pytest.param([("/tool", _BODY, tarfile.REGTYPE)], id="absolute"),
        pytest.param([("./tool", _BODY, tarfile.REGTYPE)], id="ambiguous-dot"),
        pytest.param([("a//tool", _BODY, tarfile.REGTYPE)], id="empty-component"),
        pytest.param([("tool", b"", tarfile.SYMTYPE)], id="symbolic-link"),
        pytest.param([("tool", b"", tarfile.LNKTYPE)], id="hard-link"),
        pytest.param([("tool", b"", tarfile.DIRTYPE)], id="directory"),
        pytest.param([("tool", b"", tarfile.CHRTYPE)], id="device"),
        pytest.param([("tool", b"", tarfile.FIFOTYPE)], id="fifo"),
        pytest.param([("tool", _BODY + b"excess", tarfile.REGTYPE)], id="too-large"),
        pytest.param([("tool", _BODY[:-1], tarfile.REGTYPE)], id="too-small"),
    ],
)
def test_unsafe_archive_preserves_installed_binary(
    tmp_path: Path, entries: list[tuple[str, bytes, bytes]]
) -> None:
    """A refused replacement leaves the previous executable and PATH untouched."""
    body = _archive(entries)
    environment = _environment(tmp_path, _archive_pin(body))
    binary = tmp_path / "runner" / "makeutil-bin" / "makeutil"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"previous binary")

    status = installer.run("makeutil", environment, _serving(body, []))

    assert status == 1, "unsafe archive input must refuse the installation"
    assert binary.read_bytes() == b"previous binary", "replacement must be atomic"
    assert list(binary.parent.iterdir()) == [binary], "no staging file may remain"
    assert not (tmp_path / "github_path").exists(), "PATH must not be published"


def _late_corrupt_archive() -> bytes:
    """Place invalid deflate after a complete tar header and decoded block.

    The first non-final stored deflate block contains a tar header declaring
    16384 bytes. A reserved block type follows its 8192 decoded bytes. This
    corpus specifies controlled rejection, not which library path detects it.

    Returns
    -------
    bytes
        A gzip stream with a complete initial tar header and invalid deflate data.
    """
    member = tarfile.TarInfo("tool")
    member.size = 16384
    decoded = member.tobuf() + b"\x00" * (8192 - 512)
    gzip_header = b"\x1f\x8b\x08\x00" + b"\x00" * 6
    stored_block = b"\x00\x00\x20\xff\xdf" + decoded
    return gzip_header + stored_block + b"\x07" + b"\x00" * 8


@pytest.mark.parametrize(
    "body",
    [
        b"not gzip",
        b"\x1f\x8btruncated",
        pytest.param(
            b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\x07" + b"\x00" * 8,
            id="corrupt-deflate",
        ),
        pytest.param(_late_corrupt_archive(), id="late-corrupt-deflate"),
    ],
)
def test_malformed_archive_returns_failure(tmp_path: Path, body: bytes) -> None:
    """Authenticated malformed bytes are still refused before installation."""
    status = installer.run(
        "makeutil", _environment(tmp_path, _archive_pin(body)), _serving(body, [])
    )
    assert status == 1, "malformed archive errors must become a failing status"
    assert not (tmp_path / "runner").exists(), "no executable may be staged"
    assert not (tmp_path / "github_path").exists(), "PATH must not be published"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ARCHIVE_FORMAT", "zip"),
        ("ARCHIVE_FORMAT", ""),
        ("ARCHIVE_MEMBER", ""),
        ("ARCHIVE_MEMBER", "../tool"),
        ("ARCHIVE_MEMBER", "/tool"),
        ("ARCHIVE_MEMBER", "./tool"),
        ("ARCHIVE_MEMBER_SIZE", ""),
        ("ARCHIVE_MEMBER_SIZE", "0"),
        ("ARCHIVE_MEMBER_SIZE", "-1"),
        ("ARCHIVE_MEMBER_SIZE", "1.5"),
    ],
)
def test_invalid_archive_pin_is_refused_before_download(
    tmp_path: Path, field: str, value: str
) -> None:
    """An archive requires an explicit supported format, exact name and size."""
    pin = _archive_pin(b"unused")
    pin[f"MAKEUTIL_{field}"] = value
    requested: list[str] = []

    status = installer.run(
        "makeutil", _environment(tmp_path, pin), _serving(b"unused", requested)
    )

    assert status == 1, "an invalid archive contract must fail closed"
    assert requested == [], "an invalid archive contract must not download"
    assert not (tmp_path / "github_path").exists(), "PATH must not be published"


def test_digest_is_checked_before_archive_decompression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replaced archive never reaches even the archive parser."""

    def refuse_open(*args: object, **kwargs: object) -> typ.NoReturn:
        pytest.fail("digest verification must precede archive parsing")

    monkeypatch.setattr(tarfile, "open", refuse_open)
    environment = _environment(tmp_path, _archive_pin(b"pinned archive"))
    status = installer.run("makeutil", environment, _serving(b"replacement", []))

    assert status == 1, "a digest mismatch must fail before decompression"
    assert not (tmp_path / "github_path").exists(), "PATH must not be published"


@pytest.mark.parametrize(
    "field", ["ARCHIVE_FORMAT", "ARCHIVE_MEMBER", "ARCHIVE_MEMBER_SIZE"]
)
def test_partial_archive_pin_is_refused(tmp_path: Path, field: str) -> None:
    """Removing any archive field cannot silently select raw installation."""
    pin = _archive_pin(b"unused")
    del pin[f"MAKEUTIL_{field}"]
    requested: list[str] = []
    status = installer.run(
        "makeutil", _environment(tmp_path, pin), _serving(b"unused", requested)
    )
    assert status == 1, "partial archive pins must fail closed"
    assert requested == [], "partial pins must fail before downloading"


@pytest.mark.parametrize("actual", [_BODY[:-1], _BODY + b"excess"])
def test_archive_read_is_bounded_and_actual_length_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actual: bytes
) -> None:
    """Declared tar size does not replace checking the bounded stream read."""
    body = _archive([("tool", _BODY, tarfile.REGTYPE)])
    requested_sizes: list[int | None] = []

    class MemberStream(io.BytesIO):
        """Record the maximum bytes requested from the selected member."""

        def read(self, size: int | None = -1) -> bytes:
            """Record the read boundary and provide the stand-in member bytes."""
            requested_sizes.append(size)
            return super().read(size)

    def extract_member(
        _archive: tarfile.TarFile, _member: tarfile.TarInfo
    ) -> MemberStream:
        return MemberStream(actual)

    monkeypatch.setattr(tarfile.TarFile, "extractfile", extract_member)
    status = installer.run(
        "makeutil", _environment(tmp_path, _archive_pin(body)), _serving(body, [])
    )
    assert status == 1, "actual member bytes must match the declared pin"
    assert requested_sizes == [len(_BODY) + 1], "member reads must be bounded"
    assert not (tmp_path / "github_path").exists(), "PATH must not be published"


@pytest.mark.parametrize("operation", ["chmod", "replace"])
def test_failed_staging_preserves_previous_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Filesystem errors leave the previous binary and no staging debris."""
    binary = tmp_path / "runner" / "makeutil-bin" / "makeutil"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"previous binary")

    def refuse(*_args: object, **_kwargs: object) -> typ.NoReturn:
        message = "controlled installation failure"
        raise OSError(message)

    monkeypatch.setattr(Path, operation, refuse)
    status = installer.run(
        "makeutil", _environment(tmp_path, _pin()), _serving(_BODY, [])
    )

    assert status == 1, "installation IO errors must reach the command status"
    assert binary.read_bytes() == b"previous binary", "old bytes must survive"
    assert list(binary.parent.iterdir()) == [binary], "staging debris must be removed"
    assert not (tmp_path / "github_path").exists(), "PATH must not be published"


def test_failed_path_publication_returns_failure(tmp_path: Path) -> None:
    """An installation is not reported successful when PATH cannot be written."""
    environment = _environment(tmp_path, _pin())
    github_path = tmp_path / "github_path"
    github_path.mkdir()

    status = installer.run("makeutil", environment, _serving(_BODY, []))

    assert status == 1, "PATH publication IO errors must fail the command"
    assert list(github_path.iterdir()) == [], "no PATH entry may be published"


def test_late_decompression_failure_preserves_existing_executable(
    tmp_path: Path,
) -> None:
    """Late payload corruption cannot change an already installed executable."""
    body = _late_corrupt_archive()
    pin = _archive_pin(body)
    pin["MAKEUTIL_ARCHIVE_MEMBER_SIZE"] = "16384"
    environment = _environment(tmp_path, pin)
    binary = tmp_path / "runner" / "makeutil-bin" / "makeutil"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"previous executable")
    binary.chmod(0o751)
    original_mode = binary.stat().st_mode
    github_path = tmp_path / "github_path"
    github_path.write_text("existing PATH entry\n")

    status = installer.run("makeutil", environment, _serving(body, []))

    assert status == 1, "late decompression failure must reach the command status"
    assert binary.read_bytes() == b"previous executable", "old bytes must survive"
    assert binary.stat().st_mode == original_mode, "old executable mode must survive"
    assert list(binary.parent.iterdir()) == [binary], "no staging file may remain"
    assert github_path.read_text() == "existing PATH entry\n", (
        "PATH must not be appended"
    )


def test_dependency_deflate_error_preserves_existing_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synthetic zlib dependency failure is translated without side effects.

    This injects the real library exception at the archive-opening boundary;
    it does not claim a real archive reproduces an unhandled exception.
    """
    body = _archive([("tool", _BODY, tarfile.REGTYPE)])
    environment = _environment(tmp_path, _archive_pin(body))
    binary = tmp_path / "runner" / "makeutil-bin" / "makeutil"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"previous executable")
    binary.chmod(0o751)
    original_mode = binary.stat().st_mode
    github_path = tmp_path / "github_path"
    github_path.write_text("existing PATH entry\n")

    def fail_decompression(*_args: object, **_kwargs: object) -> typ.NoReturn:
        message = "synthetic dependency deflate failure"
        raise zlib.error(message)

    monkeypatch.setattr(tarfile, "open", fail_decompression)
    status = installer.run("makeutil", environment, _serving(body, []))

    assert status == 1, "the dependency exception must become a failing status"
    assert binary.read_bytes() == b"previous executable", "old bytes must survive"
    assert binary.stat().st_mode == original_mode, "old executable mode must survive"
    assert list(binary.parent.iterdir()) == [binary], "no staging file may remain"
    assert github_path.read_text() == "existing PATH entry\n", (
        "PATH must not be appended"
    )
