"""Bounded archive installation invariants across payloads and member ordering.

Each property uses 64 examples, at most eight members and 128-byte payloads.
Fresh temporary directories and patch contexts isolate every example. Deadlines
are disabled because filesystem latency is unrelated to the bounded invariant.
The constructors below serve only this module's public installer boundary.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from scripts import install_release_binary as installer


def _archive(entries: list[tuple[str, bytes, bytes]]) -> bytes:
    """Encode this property's member sequence without extracting any paths."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, payload, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(payload) if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def _install(body: bytes, root: Path, size: int, member: str = "tool") -> int:
    """Run the real installer against an authenticated in-memory release."""
    environment = {
        "TOOL_VERSION": "1.0.0",
        "TOOL_ASSET": "tool.tar.gz",
        "TOOL_SHA256": hashlib.sha256(body).hexdigest(),
        "TOOL_RELEASES": "https://example.com/releases/download",
        "TOOL_ARCHIVE_FORMAT": "tar.gz",
        "TOOL_ARCHIVE_MEMBER": member,
        "TOOL_ARCHIVE_MEMBER_SIZE": str(size),
        "RUNNER_TEMP": str(root / "runner"),
        "GITHUB_PATH": str(root / "github_path"),
    }
    return installer.run("tool", environment, lambda _url: body)


@settings(max_examples=64, deadline=None)
@example(
    siblings=[b"licence"],
    payload=b"tool",
    selected_position=0,
    unsafe_position=2,
    unsafe=("../escape", tarfile.REGTYPE),
)
@given(
    siblings=st.lists(st.binary(max_size=128), max_size=6),
    payload=st.binary(min_size=1, max_size=128),
    selected_position=st.integers(min_value=0, max_value=7),
    unsafe_position=st.integers(min_value=0, max_value=7),
    unsafe=st.sampled_from([
        ("../escape", tarfile.REGTYPE),
        ("/escape", tarfile.REGTYPE),
        ("unsafe", tarfile.SYMTYPE),
        ("unsafe", tarfile.DIRTYPE),
    ]),
)
def test_unsafe_members_refuse_installation_at_every_position(
    siblings: list[bytes],
    payload: bytes,
    selected_position: int,
    unsafe_position: int,
    unsafe: tuple[str, bytes],
) -> None:
    """No selected-member position can hide an unsafe sibling from validation."""
    entries = [
        (f"docs/sibling-{index}", body, tarfile.REGTYPE)
        for index, body in enumerate(siblings)
    ]
    entries.insert(
        selected_position % (len(entries) + 1), ("tool", payload, tarfile.REGTYPE)
    )
    name, kind = unsafe
    entries.insert(unsafe_position % (len(entries) + 1), (name, b"", kind))
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.MonkeyPatch.context() as patch,
    ):
        root = Path(directory)
        binary = root / "runner" / "tool-bin" / "tool"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"previous executable")
        binary.chmod(0o751)
        original_mode = binary.stat().st_mode
        github_path = root / "github_path"
        github_path.write_text("existing PATH entry\n")

        def refuse_staging(_body: bytes, _destination: Path) -> None:
            pytest.fail("unsafe archive validation must finish before staging")

        patch.setattr(installer, "_stage_binary", refuse_staging)
        status = _install(_archive(entries), root, len(payload))

        assert status == 1, "an unsafe member must reject the entire archive"
        assert binary.read_bytes() == b"previous executable", "old bytes must survive"
        assert binary.stat().st_mode == original_mode, "old mode must survive"
        assert list(binary.parent.iterdir()) == [binary], "no staged sibling may remain"
        assert github_path.read_text() == "existing PATH entry\n", (
            "unsafe archives must not publish another PATH entry"
        )


@settings(max_examples=64, deadline=None)
@given(
    siblings=st.lists(st.binary(max_size=128), max_size=6),
    payload=st.binary(min_size=2, max_size=128),
    position=st.integers(min_value=0, max_value=7),
    name=st.sampled_from(["tool", "bin/tool", "sbin/runner"]),
    size_delta=st.sampled_from([-1, 1]),
)
def test_only_unique_exact_size_members_install(
    siblings: list[bytes], payload: bytes, position: int, name: str, size_delta: int
) -> None:
    """Regular siblings do not alter exact-name, unique-member, or size approval."""
    safe_entries = [
        (f"docs/sibling-{index}", body, tarfile.REGTYPE)
        for index, body in enumerate(siblings)
    ]
    selected = (name, payload, tarfile.REGTYPE)
    safe_entries.insert(position % (len(safe_entries) + 1), selected)
    scenarios = [
        (safe_entries, len(payload), True),
        ([*safe_entries, selected], len(payload), False),
        (safe_entries, len(payload) + size_delta, False),
    ]
    for entries, pinned_size, approved in scenarios:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = _install(_archive(entries), root, pinned_size, name)
            binary = root / "runner" / "tool-bin" / "tool"
            github_path = root / "github_path"
            if approved:
                assert status == 0, "a unique exact-size selected member must install"
                assert binary.read_bytes() == payload, "only approved bytes may install"
                assert list(binary.parent.iterdir()) == [binary], (
                    "safe siblings must not be installed"
                )
                assert github_path.read_text() == f"{binary.parent}\n", (
                    "successful installation must publish its exact directory"
                )
            else:
                assert status == 1, "duplicates and size mismatches must be refused"
                assert not (root / "runner").exists(), "rejection must precede staging"
                assert not github_path.exists(), (
                    "rejected selection must not publish PATH"
                )


@settings(max_examples=64, deadline=None)
@example(pinned_size=2, declared_delta=0, actual=b"ok")
@example(pinned_size=2, declared_delta=0, actual=b"overflow")
@given(
    pinned_size=st.integers(min_value=2, max_value=127),
    declared_delta=st.sampled_from([-1, 0, 1]),
    actual=st.binary(min_size=1, max_size=128),
)
def test_member_reads_are_bounded_and_only_exact_bytes_install(
    pinned_size: int, declared_delta: int, actual: bytes
) -> None:
    """Gate reads by declared size and installation by bounded actual size."""
    declared_size = pinned_size + declared_delta
    body = _archive([("tool", b"x" * declared_size, tarfile.REGTYPE)])
    requests: list[int | None] = []

    class RecordedStream(io.BytesIO):
        """Observe the exact read bound while supplying varied actual bytes."""

        def read(self, size: int | None = -1) -> bytes:
            """Record requests without altering BytesIO's read behaviour."""
            requests.append(size)
            return super().read(size)

    def extract_member(
        _archive: tarfile.TarFile, _member: tarfile.TarInfo
    ) -> RecordedStream:
        return RecordedStream(actual)

    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.MonkeyPatch.context() as patch,
    ):
        root = Path(directory)
        patch.setattr(tarfile.TarFile, "extractfile", extract_member)
        status = _install(body, root, pinned_size)
        exact = declared_size == pinned_size and len(actual) == pinned_size
        assert status == (0 if exact else 1), (
            "installation requires both declared and actual lengths to equal the pin"
        )
        assert requests == ([pinned_size + 1] if declared_delta == 0 else []), (
            "only an approved header may read, with one overflow byte "
            "and no unbounded call"
        )
        binary = root / "runner" / "tool-bin" / "tool"
        github_path = root / "github_path"
        if exact:
            assert binary.read_bytes() == actual, "exact approved bytes must install"
            assert github_path.read_text() == f"{binary.parent}\n", (
                "only exact-size installation may publish PATH"
            )
        else:
            assert not (root / "runner").exists(), "size refusal must precede staging"
            assert not github_path.exists(), "size refusal must not publish PATH"
