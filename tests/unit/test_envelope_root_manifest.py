"""Keep both envelope builders' root marker tied to governed Cargo surfaces."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.envelope import build_build_defaults_envelope, build_envelope

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from concordat.rules.envelope import BuildDefaultsEnvelope, PolicyEnvelope


@pytest.mark.parametrize(
    "builder",
    [
        pytest.param(build_envelope, id="makefile-envelope"),
        pytest.param(build_build_defaults_envelope, id="build-defaults-envelope"),
    ],
)
@pytest.mark.parametrize(
    "surface_paths",
    [
        pytest.param(("nested/Cargo.toml", "Cargo.toml"), id="governed-root-last"),
        pytest.param(("nested/Cargo.toml",), id="nested-only-root-decoy"),
        pytest.param((), id="declared-empty-root-decoy"),
    ],
)
def test_root_marker_preserves_governed_surface_order_and_identity(
    tmp_path: pathlib.Path,
    builder: cabc.Callable[[pathlib.Path], PolicyEnvelope | BuildDefaultsEnvelope],
    surface_paths: tuple[str, ...],
) -> None:
    """A physical root must not replace the authoritative governed surfaces."""
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "root"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "Cargo.toml").write_text(
        "[workspace]\nmembers = []\n\n[profile.dev]\nincremental = false\n",
        encoding="utf-8",
    )
    declaration = "language:\n  rust:\n    surfaces: []\n"
    if surface_paths:
        declaration = "language:\n  rust:\n    surfaces:\n" + "".join(
            f"      - path: {path}\n" for path in surface_paths
        )
    (tmp_path / ".concordat").write_text(declaration, encoding="utf-8")

    document = builder(tmp_path)
    cargo = document["cargo"]
    surfaces = cargo["surfaces"]
    assert document["applicability"]["root_cargo_toml"] is True
    assert document["applicability"]["rust_surfaces_declared"] is True
    assert [surface["path"] for surface in surfaces] == list(surface_paths)
    if "Cargo.toml" in surface_paths:
        assert cargo["parsed"] == {"package": {"name": "root", "version": "0.1.0"}}
        assert cargo["parsed"] is surfaces[-1]["parsed"]
    else:
        assert cargo["parsed"] is None
