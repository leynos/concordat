"""Specify the selectable development fragment independently of audit defaults."""

from __future__ import annotations

import hashlib
import tomllib
import typing as typ
from pathlib import Path

import pytest
import ruamel.yaml

ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = Path("platform-standards/canon/build/rust/dev-fast/config.toml")


@pytest.fixture
def dev_fast_seed() -> dict[str, object]:
    """Read the reference fragment as Cargo configuration data."""
    return tomllib.loads((ROOT / SEED_PATH).read_text(encoding="utf-8"))


def test_dev_fast_seed_enables_unstable_backend_selection(
    dev_fast_seed: dict[str, object],
) -> None:
    """Cargo must explicitly enable its unstable code-generation backend key."""
    unstable = dev_fast_seed["unstable"]
    assert isinstance(unstable, dict)
    assert set(unstable) == {"codegen-backend"}
    unstable_fields = typ.cast("dict[str, object]", unstable)
    assert unstable_fields["codegen-backend"] is True


def test_dev_fast_seed_selects_cranelift_only_for_development(
    dev_fast_seed: dict[str, object],
) -> None:
    """Declare only dev; consumer routes own explicit inheritance overrides."""
    assert dev_fast_seed["profile"] == {"dev": {"codegen-backend": "cranelift"}}


def test_dev_fast_seed_uses_mold_only_for_linux_targets(
    dev_fast_seed: dict[str, object],
) -> None:
    """Leave macOS and Windows linker configuration unchanged."""
    assert dev_fast_seed["target"] == {
        'cfg(target_os = "linux")': {"rustflags": ["-Clink-arg=-fuse-ld=mold"]}
    }


def test_dev_fast_seed_does_not_override_global_build_settings(
    dev_fast_seed: dict[str, object],
) -> None:
    """The fragment carries no global flags or consumer-specific exceptions."""
    assert set(dev_fast_seed) == {"unstable", "profile", "target"}


def test_dev_fast_seed_has_its_own_checksum_registration() -> None:
    """Artifact selection must resolve this fragment rather than an audit rule."""
    manifest_path = ROOT / "platform-standards/canon/manifest.yaml"
    document = ruamel.yaml.YAML(typ="safe").load(
        manifest_path.read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    artifacts = typ.cast("list[dict[str, object]]", document["artifacts"])
    matches = [entry for entry in artifacts if entry["id"] == "rust-dev-fast-config"]
    assert len(matches) == 1, "the selectable dev-fast fragment needs its own artifact"
    artifact = matches[0]
    assert artifact["type"] == "build-template"
    assert artifact["path"] == SEED_PATH.as_posix()
    assert (
        artifact["sha256"]
        == hashlib.sha256((ROOT / SEED_PATH).read_bytes()).hexdigest()
    )
    assert artifact["path"] != (
        "platform-standards/canon/lint-rules/rust-build-defaults/rule.yaml"
    )
