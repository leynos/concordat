"""Pin root-manifest backend normalization at the build-defaults adapter boundary."""

from __future__ import annotations

import tomllib
import typing as typ

import pytest

from concordat.rules.cargo_config import codegen_backends
from concordat.rules.envelope import build_build_defaults_envelope

if typ.TYPE_CHECKING:
    import pathlib

_CASES: typ.Final = [
    pytest.param("", [], id="absent"),
    pytest.param('profile = "malformed"\n', [], id="malformed-profile"),
    pytest.param("[profile]\ndev = 3\n", [], id="malformed-entry"),
    pytest.param("[profile.dev]\ncodegen-backend = 3\n", [], id="non-string-backend"),
    pytest.param(
        '[profile.dev]\ncodegen-backend = "llvm"\n',
        [
            {
                "source": "profile.dev",
                "scope": "profile",
                "profile": "dev",
                "backend": "llvm",
            }
        ],
        id="profile-llvm",
    ),
    pytest.param(
        '[profile.test]\ncodegen-backend = "cranelift"\n',
        [
            {
                "source": "profile.test",
                "scope": "profile",
                "profile": "test",
                "backend": "cranelift",
            }
        ],
        id="profile-cranelift",
    ),
    pytest.param(
        '[profile.release.package.example]\ncodegen-backend = "cranelift"\n',
        [
            {
                "source": "profile.release.package.example",
                "scope": "package-override",
                "profile": "release",
                "backend": "cranelift",
            }
        ],
        id="package-cranelift",
    ),
    pytest.param(
        "[profile.dev.package]\nexample = 3\n", [], id="malformed-package-entry"
    ),
    pytest.param(
        '[profile.dev.package.example]\ncodegen-backend = "custom"\n',
        [
            {
                "source": "profile.dev.package.example",
                "scope": "package-override",
                "profile": "dev",
                "backend": "custom",
            }
        ],
        id="non-cranelift-package",
    ),
]


@pytest.mark.parametrize(("profile_text", "expected"), _CASES)
def test_root_manifest_backends_are_typed_and_distinct_from_cargo_config(
    tmp_path: pathlib.Path,
    profile_text: str,
    expected: list[dict[str, str]],
) -> None:
    """Normalize only root-manifest profile facts, retaining their scope and source."""
    (tmp_path / "Cargo.toml").write_text(
        profile_text + '\n[package]\nname = "fixture"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    envelope = build_build_defaults_envelope(tmp_path)
    assert envelope.get("cargo_manifest_backends", []) == expected
    assert envelope["cargo_config"] is None
    if not expected:
        assert "cargo_manifest_backends" not in envelope
    if not profile_text or expected:
        assert "cargo_manifest_backend_error" not in envelope


@pytest.mark.parametrize(("profile_text", "expected"), _CASES)
def test_existing_backend_adapter_normalizes_manifest_profile_shapes(
    profile_text: str, expected: list[dict[str, str]]
) -> None:
    """Reuse the existing Cargo profile normalizer instead of another TOML reader."""
    assert codegen_backends(tomllib.loads(profile_text), []) == expected


@pytest.mark.parametrize(
    "profile_text",
    [
        'profile = "malformed"\n',
        "[profile]\ndev = 3\n",
        "[profile.dev]\ncodegen-backend = 3\n",
        "[profile.dev.package]\nexample = 3\n",
        "[profile.dev]\npackage = 3\n",
        '[profile.dev]\ncodegen-backend = ""\n',
    ],
    ids=[
        "profile-not-table",
        "profile-entry-not-table",
        "backend-not-string",
        "package-not-table",
        "package-container-not-table",
        "empty-backend",
    ],
)
def test_malformed_backend_shapes_are_not_reported_as_legitimate_absence(
    tmp_path: pathlib.Path, profile_text: str
) -> None:
    """Unsupported backend-relevant shapes retain a binding unmeasured fact."""
    (tmp_path / "Cargo.toml").write_text(
        profile_text + '\n[package]\nname = "fixture"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    envelope = build_build_defaults_envelope(tmp_path)
    error = envelope.get("cargo_manifest_backend_error")
    assert isinstance(error, str)
    assert error
