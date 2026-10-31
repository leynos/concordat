"""A declared Cargo surface is judged as a build root only if it can be one.

`language.rust.surfaces` lists the manifests a repository governs. Two kinds of
reader use the list. The dependency-family rule wants every manifest that
carries dependencies. The development-build and Makefile rules judge each
surface as a project root, and Cargo honours profiles only at the root it builds
from, so a nested standalone crate or workspace with no `[profile]` of its own
cannot meet them. Each surface therefore records `build_root`: the repository
root manifest, or any manifest with a `[profile]` table of its own.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.dependency_family_envelope import build_dependency_family_envelope
from concordat.rules.dev_build_envelope import build_dev_build_envelope
from concordat.rules.envelope import build_build_defaults_envelope, build_envelope
from concordat.rules.rust_surfaces import build_root_surfaces, resolve_rust_surfaces
from tests.unit.test_dev_build_footprint import DISABLED, RULE_ID, write_checkout

if typ.TYPE_CHECKING:
    import pathlib

NESTED: typ.Final = "tools/native/Cargo.toml"
PACKAGE: typ.Final = (
    '[package]\nname = "native"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1"\n'
)
ENABLED_PROFILE: typ.Final = "\n[profile.dev]\nincremental = true\ndebug = 2\n"


def _declare(root: pathlib.Path, *paths: str) -> None:
    """Declare *paths* as the repository's Rust surfaces."""
    entries = "".join(f"      - path: {path}\n" for path in paths)
    (root / ".concordat").write_text(
        f"language:\n  rust:\n    surfaces:\n{entries}", encoding="utf-8"
    )


def _checkout(root: pathlib.Path, nested: str) -> None:
    """Write a compliant root crate and a nested manifest, both declared."""
    write_checkout(root)
    path = root / NESTED
    path.parent.mkdir(parents=True)
    path.write_text(nested, encoding="utf-8")
    _declare(root, "Cargo.toml", NESTED)


@pytest.mark.parametrize(
    ("nested", "expected"),
    [
        pytest.param(PACKAGE, False, id="nested-crate-without-a-profile"),
        pytest.param(PACKAGE + DISABLED, True, id="nested-crate-with-a-profile"),
        pytest.param(
            "[workspace]\nmembers = []\n", False, id="nested-workspace-without-profile"
        ),
        pytest.param(
            "[workspace]\nmembers = []\n" + DISABLED,
            True,
            id="nested-workspace-with-a-profile",
        ),
    ],
)
def test_a_nested_surface_is_a_build_root_only_with_its_own_profile(
    tmp_path: pathlib.Path, nested: str, *, expected: bool
) -> None:
    """The inference reads the manifest, not the declared role."""
    _checkout(tmp_path, nested)
    surfaces = {s["path"]: s for s in resolve_rust_surfaces(tmp_path).surfaces}
    assert surfaces["Cargo.toml"]["build_root"] is True, surfaces
    assert surfaces[NESTED]["build_root"] is expected, surfaces


def test_the_root_manifest_is_always_a_build_root(tmp_path: pathlib.Path) -> None:
    """Today's behaviour for the repository root does not depend on a profile."""
    (tmp_path / "Cargo.toml").write_text(PACKAGE, encoding="utf-8")
    (surface,) = resolve_rust_surfaces(tmp_path).surfaces
    assert surface["build_root"] is True, surface


def test_build_root_surfaces_keeps_declaration_order(tmp_path: pathlib.Path) -> None:
    """The filter drops surfaces and never reorders the rest."""
    _checkout(tmp_path, PACKAGE + DISABLED)
    kept = build_root_surfaces(resolve_rust_surfaces(tmp_path).surfaces)
    assert [s["path"] for s in kept] == ["Cargo.toml", NESTED], kept


def test_a_nested_crate_with_its_own_profile_is_judged_by_footprint(
    tmp_path: pathlib.Path,
) -> None:
    """A profile that turns incremental builds on is a real finding."""
    _checkout(tmp_path, PACKAGE + ENABLED_PROFILE)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {f.rule_id for f in result.findings} == {"DF-001", "DF-002"}, result
    assert {f.path for f in result.findings} == {NESTED}, result


def test_a_nested_crate_without_a_profile_is_not_judged_by_footprint(
    tmp_path: pathlib.Path,
) -> None:
    """The nested crate has nothing to judge, so the rule reads the root alone."""
    _checkout(tmp_path, PACKAGE)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "compliant", result
    assert not result.findings, result


def test_the_dev_build_envelope_lists_only_build_roots(tmp_path: pathlib.Path) -> None:
    """A surface without its own profile never reaches the footprint policy."""
    _checkout(tmp_path, PACKAGE)
    envelope = build_dev_build_envelope(tmp_path)
    assert [s["path"] for s in envelope["surfaces"]] == ["Cargo.toml"], envelope


@pytest.mark.parametrize(
    "builder",
    [
        pytest.param(build_envelope, id="makefile-envelope"),
        pytest.param(build_build_defaults_envelope, id="build-defaults-envelope"),
    ],
)
@pytest.mark.parametrize(
    ("nested", "expected"),
    [
        pytest.param(PACKAGE, ["Cargo.toml"], id="without-a-profile"),
        pytest.param(PACKAGE + DISABLED, ["Cargo.toml", NESTED], id="with-a-profile"),
    ],
)
def test_the_makefile_and_build_defaults_envelopes_list_only_build_roots(
    tmp_path: pathlib.Path,
    builder: typ.Callable[[pathlib.Path], typ.Any],
    nested: str,
    expected: list[str],
) -> None:
    """Both Makefile-centred envelopes apply the same filter."""
    _checkout(tmp_path, nested)
    cargo = builder(tmp_path)["cargo"]
    assert [s["path"] for s in cargo["surfaces"]] == expected, cargo


@pytest.mark.parametrize(
    "nested",
    [
        pytest.param(PACKAGE, id="without-a-profile"),
        pytest.param(PACKAGE + DISABLED, id="with-a-profile"),
    ],
)
def test_dependency_family_still_sees_every_declared_surface(
    tmp_path: pathlib.Path, nested: str
) -> None:
    """A surface the build rules skip still carries dependencies to the family rule."""
    _checkout(tmp_path, nested)
    envelope = build_dependency_family_envelope(tmp_path)
    manifests = {declaration["manifest"] for declaration in envelope["declarations"]}
    assert NESTED in manifests, manifests
