"""Repository context stays hermetic across environment settings and pin layers."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.dev_build_context import resolve_dev_context
from concordat.rules.rust_surfaces import resolve_rust_surfaces
from tests.unit.test_dev_build_footprint import FLAGS, write_checkout

if typ.TYPE_CHECKING:
    import pathlib


@pytest.mark.parametrize(
    ("key", "refused"),
    [
        ("RUSTFLAGS", True),
        ("CARGO_ENCODED_RUSTFLAGS", True),
        ("CARGO_INCREMENTAL", True),
        ("CARGO_BUILD_INCREMENTAL", True),
        ("CARGO_BUILD_RUSTFLAGS", True),
        ("CARGO_PROFILE_DEV_DEBUG", True),
        ("CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS", True),
        ("UNRELATED", False),
    ],
)
def test_context_rejects_only_build_environment_overrides(
    tmp_path: pathlib.Path, key: str, *, refused: bool
) -> None:
    """Static proof cannot silently ignore a later Cargo environment override."""
    write_checkout(tmp_path, config=FLAGS + f'[env]\n{key} = "value"\n')
    surface = resolve_rust_surfaces(tmp_path).surfaces[0]
    context = resolve_dev_context(tmp_path, surface)
    assert bool(context.errors) is refused, context.errors
    assert context.dev == {"incremental": False, "debug": 0}, context.dev


@pytest.mark.parametrize("legacy", [False, True], ids=["toml", "legacy"])
def test_context_selects_the_nearest_repository_toolchain(
    tmp_path: pathlib.Path, *, legacy: bool
) -> None:
    """A nested pin shadows the ancestor, including extensionless legacy pins."""
    write_checkout(tmp_path, channel="stable")
    nested = tmp_path / "rust"
    write_checkout(nested, channel="nightly-2026-05-28")
    if legacy:
        (nested / "rust-toolchain.toml").unlink()
        (nested / "rust-toolchain").write_text("nightly", encoding="utf-8")
    (tmp_path / ".concordat").write_text(
        "language:\n  rust:\n    surfaces:\n      - path: rust/Cargo.toml\n",
        encoding="utf-8",
    )
    context = resolve_dev_context(tmp_path, resolve_rust_surfaces(tmp_path).surfaces[0])
    assert context.channel == ("nightly" if legacy else "nightly-2026-05-28"), context
    assert not context.errors, context.errors
