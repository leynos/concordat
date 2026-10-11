"""Specify the dependency-family facts for the `dependency-family-contracts` rule.

The envelope builder reads manifests, locks, the Dependabot configuration and
the repository's recorded exceptions, so the policy reads facts alone. These
tests describe what the builder establishes and, as important, what it refuses
to read as a pass: a missing lock, an unparsable manifest and a Python project
outside the root each stay a fact with a reason.
"""

from __future__ import annotations

import os
import typing as typ

import pytest

from concordat.rules.dependency_family_envelope import (
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    Declaration,
    DependencyFamilyEnvelope,
    build_dependency_family_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

_CONFIG = """\
version: 2
updates:
  - package-ecosystem: cargo
    directory: /rust
    schedule:
      interval: daily
    groups:
      pyo3:
        patterns: ['pyo3', 'pyo3-build-config']
      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
    ignore:
      - dependency-name: serde-pyobject
  - package-ecosystem: uv
    directory: /
    schedule:
      interval: daily
    groups:
      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
"""
_WORKSPACE = """\
[workspace]
members = ["native"]

[workspace.dependencies]
pyo3 = "0.28.3"
"""
_MEMBER = """\
[package]
name = "native"
version = "0.1.0"

[dependencies]
pyo3 = { workspace = true }
pyo3-serde = { package = "serde-pyobject", version = "0.8.0", optional = true }

[build-dependencies]
pyo3-build-config = "0.28.3"

[target.'cfg(unix)'.dev-dependencies]
rstest = "0.18"
"""
_LOCK = """\
version = 4

[[package]]
name = "pyo3"
version = "0.28.3"

[[package]]
name = "serde-pyobject"
version = "0.8.0"
"""
_PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
dependencies = ["maturin[patchelf]==1.13.3", "Typing_Extensions>=4,<5"]

[dependency-groups]
dev = ["pytest>=8", "pylint~=4.0"]
"""
_SURFACES = (
    "language:\n  rust:\n    surfaces:\n"
    "      - path: rust/Cargo.toml\n      - path: rust/native/Cargo.toml\n"
)


def _write(checkout: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *checkout*."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def layout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide a nested PyO3 workspace with a Python project and a lock."""
    _write(tmp_path, ".concordat", _SURFACES)
    _write(tmp_path, "rust/Cargo.toml", _WORKSPACE)
    _write(tmp_path, "rust/native/Cargo.toml", _MEMBER)
    _write(tmp_path, "rust/Cargo.lock", _LOCK)
    _write(tmp_path, "pyproject.toml", _PYPROJECT)
    _write(tmp_path, ".github/dependabot.yml", _CONFIG)
    return tmp_path


def _by_package(
    envelope: DependencyFamilyEnvelope,
) -> dict[tuple[str, str], Declaration]:
    """Index declarations by `(manifest, published package)`."""
    return {(d["manifest"], d["package"]): d for d in envelope["declarations"]}


def test_the_envelope_identifies_itself(layout: pathlib.Path) -> None:
    """The policy checks the kind and version so another package's facts fail."""
    envelope = build_dependency_family_envelope(layout)

    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert envelope["kind"] == ENVELOPE_KIND
    assert envelope["problems"] == []


def test_a_renamed_dependency_is_identified_by_its_published_name(
    layout: pathlib.Path,
) -> None:
    """Dependabot matches `serde-pyobject`, not the `pyo3-serde` alias."""
    declared = _by_package(build_dependency_family_envelope(layout))
    bridge = declared["rust/native/Cargo.toml", "serde-pyobject"]

    assert bridge["declared_name"] == "pyo3-serde"
    assert bridge["optional"] is True
    assert bridge["coverage"]["ignored"] is True


def test_a_workspace_inherited_requirement_is_resolved_from_the_root(
    layout: pathlib.Path,
) -> None:
    """`pyo3 = { workspace = true }` takes the root's requirement."""
    pyo3 = _by_package(build_dependency_family_envelope(layout))[
        "rust/native/Cargo.toml", "pyo3"
    ]

    assert pyo3["workspace_inherited"] is True
    assert pyo3["requirement"] == "0.28.3"
    assert pyo3["resolved"] == {
        "versions": ["0.28.3"],
        "lock": "rust/Cargo.lock",
        "reason": None,
    }


def test_a_member_is_covered_through_its_workspace_root(layout: pathlib.Path) -> None:
    """The entry for `/rust` covers `rust/native`, and a narrow group takes both."""
    declared = _by_package(build_dependency_family_envelope(layout))

    for package in ("pyo3", "pyo3-build-config"):
        coverage = declared["rust/native/Cargo.toml", package]["coverage"]
        assert coverage["group"] == "pyo3"
        assert coverage["catch_all"] is False
        assert coverage["matching"] == ["pyo3", "minor-and-patch"]
        assert coverage["matching_narrow"] == ["pyo3"]


def test_build_and_target_tables_are_declarations_too(layout: pathlib.Path) -> None:
    """A build dependency and a target-specific dev dependency are not skipped."""
    declared = _by_package(build_dependency_family_envelope(layout))

    assert declared["rust/native/Cargo.toml", "pyo3-build-config"]["section"] == (
        "build-dependencies"
    )
    rstest = declared["rust/native/Cargo.toml", "rstest"]
    assert rstest["section"] == "dev-dependencies"
    assert rstest["target"] == "cfg(unix)"


@pytest.mark.parametrize(
    ("package", "kind", "bounded"),
    [
        ("maturin", "exact", False),
        ("typing-extensions", "bounded", True),
        ("pytest", "caret", False),
        ("pylint", "bounded", True),
    ],
)
def test_python_requirements_are_normalised_and_classified(
    layout: pathlib.Path, package: str, kind: str, *, bounded: bool
) -> None:
    """An exact pin is not an upper bound; `<` and `~=` are."""
    declared = _by_package(build_dependency_family_envelope(layout))[
        "pyproject.toml", package
    ]

    assert declared["requirement_kind"] == kind
    assert declared["upper_bounded"] is bounded
    assert declared["ecosystem"] == "pip"


def test_a_uv_lock_makes_the_python_ecosystem_uv(layout: pathlib.Path) -> None:
    """The ecosystem Dependabot sees follows the lock the project uses."""
    _write(
        layout,
        "uv.lock",
        'version = 1\n[[package]]\nname = "pytest"\nversion = "8.4.0"\n',
    )

    declared = _by_package(build_dependency_family_envelope(layout))[
        "pyproject.toml", "pytest"
    ]

    assert declared["ecosystem"] == "uv"
    assert declared["coverage"]["group"] == "minor-and-patch"
    assert declared["resolved"]["versions"] == ["8.4.0"]


def test_an_absent_lock_is_a_reason_not_a_pass(layout: pathlib.Path) -> None:
    """Without a lock the resolved identity is unknown, with the reason."""
    resolved = _by_package(build_dependency_family_envelope(layout))[
        "pyproject.toml", "maturin"
    ]["resolved"]

    assert resolved["versions"] == []
    assert resolved["reason"] == "uv.lock is absent"


def test_without_a_configuration_every_declaration_says_why(
    tmp_path: pathlib.Path,
) -> None:
    """No Dependabot configuration leaves coverage unknown, never compliant."""
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "a"\nversion = "0.1.0"\n[dependencies]\npyo3 = "0.28"\n',
    )

    pyo3 = build_dependency_family_envelope(tmp_path)["declarations"][0]

    assert pyo3["coverage"]["reason"] == "no decodable Dependabot configuration"
    assert pyo3["coverage"]["group"] is None


def test_an_unparsable_manifest_is_a_problem(tmp_path: pathlib.Path) -> None:
    """The envelope reports it rather than reading no dependencies as clean."""
    _write(tmp_path, "Cargo.toml", "[package\n")

    envelope = build_dependency_family_envelope(tmp_path)

    assert envelope["declarations"] == []
    assert len(envelope["problems"]) == 1
    assert envelope["problems"][0].startswith("the Rust surfaces could not be resolved")
    assert str(tmp_path) not in envelope["problems"][0]


@pytest.mark.parametrize("crate", ["rust", "rust_extension", "tei-py"])
def test_each_nested_crate_layout_is_named(tmp_path: pathlib.Path, crate: str) -> None:
    """The layouts the pilots use (a `rust/`, `rust_extension/` or named crate)."""
    _write(tmp_path, f"{crate}/Cargo.toml", _MEMBER)

    problems = build_dependency_family_envelope(tmp_path)["problems"]

    assert [p for p in problems if p.startswith(f"{crate}/Cargo.toml is a Cargo")], (
        f"expected a problem naming {crate}/Cargo.toml as an ungoverned manifest; "
        f"got {problems}"
    )


def test_an_undeclared_nested_cargo_manifest_is_a_problem(
    tmp_path: pathlib.Path,
) -> None:
    """Without declared surfaces only the root is read; others are named."""
    _write(tmp_path, "rust/Cargo.toml", _MEMBER)
    _write(tmp_path, "target/debug/Cargo.toml", _MEMBER)
    _write(tmp_path, ".cargo/Cargo.toml", _MEMBER)
    _write(tmp_path, "vendor/serde/Cargo.toml", _MEMBER)

    envelope = build_dependency_family_envelope(tmp_path)

    assert envelope["declarations"] == []
    assert envelope["problems"] == [
        (
            "rust/Cargo.toml is a Cargo manifest outside the governed Rust "
            "surfaces; declare language.rust.surfaces in .concordat to read it"
        )
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can list any directory")
def test_a_directory_the_scan_cannot_list_is_a_problem(
    tmp_path: pathlib.Path,
) -> None:
    """A shorter result is never silent: the unreadable directory is named."""
    _write(tmp_path, "rust/locked/Cargo.toml", _MEMBER)
    locked = tmp_path / "rust" / "locked"
    locked.chmod(0)
    try:
        problems = build_dependency_family_envelope(tmp_path)["problems"]
    finally:
        locked.chmod(0o755)

    assert any(
        problem.startswith("cannot scan rust/locked for Cargo.toml")
        for problem in problems
    )


def test_a_declared_surface_list_is_authoritative(layout: pathlib.Path) -> None:
    """A repository that declares its surfaces is not second-guessed."""
    _write(layout, "other/Cargo.toml", _MEMBER)

    assert build_dependency_family_envelope(layout)["problems"] == []


def test_a_nested_python_project_is_reported_not_read(tmp_path: pathlib.Path) -> None:
    """Only the root `pyproject.toml` is read, and the others are named."""
    _write(tmp_path, "sub/pyproject.toml", _PYPROJECT)

    assert build_dependency_family_envelope(tmp_path)["problems"] == [
        "sub/pyproject.toml is not read: only the root pyproject.toml is"
    ]


def test_a_deeply_nested_python_project_is_reported_not_read(
    tmp_path: pathlib.Path,
) -> None:
    """A project at any depth is named; hidden and vendored trees are not."""
    _write(tmp_path, "packages/a/pyproject.toml", _PYPROJECT)
    _write(tmp_path, ".venv/lib/pyproject.toml", _PYPROJECT)
    _write(tmp_path, "node_modules/x/pyproject.toml", _PYPROJECT)

    assert build_dependency_family_envelope(tmp_path)["problems"] == [
        "packages/a/pyproject.toml is not read: only the root pyproject.toml is"
    ]


def test_optional_dependencies_are_optional_declarations(
    tmp_path: pathlib.Path,
) -> None:
    """An extra's requirement is recorded with its extra and as optional."""
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[project.optional-dependencies]\nfast = ["Typing_Extensions>=4,<5"]\n',
    )
    _write(tmp_path, "uv.lock", "version = 1\n")
    _write(tmp_path, ".github/dependabot.yml", _CONFIG)

    declared = _by_package(build_dependency_family_envelope(tmp_path))[
        "pyproject.toml", "typing-extensions"
    ]

    assert declared["section"] == "optional-dependencies:fast"
    assert declared["optional"] is True
    assert declared["requirement_kind"] == "bounded"
    assert declared["upper_bounded"] is True
    assert declared["coverage"]["entry"] is not None


def test_an_ignore_wildcard_and_python_spelling_protect_a_package(
    tmp_path: pathlib.Path,
) -> None:
    """`serde-*` ignores serde-pyobject; `Typing_Extensions` ignores its package."""
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        'dependencies = ["typing.extensions>=4,<5"]\n',
    )
    _write(
        tmp_path,
        ".github/dependabot.yml",
        "version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /\n"
        "    schedule:\n      interval: daily\n    ignore:\n"
        "      - dependency-name: Typing_Extensions\n",
    )

    declared = _by_package(build_dependency_family_envelope(tmp_path))[
        "pyproject.toml", "typing-extensions"
    ]

    assert declared["coverage"]["ignored"] is True


def test_an_exception_needs_a_rationale_and_a_review_trigger(
    layout: pathlib.Path,
) -> None:
    """A complete exception is carried; an incomplete one is a problem."""
    _write(
        layout,
        ".concordat",
        _SURFACES + "dependencies:\n  family_exceptions:\n"
        "    - relationship: pyo3-build-config-family\n"
        "      rationale: older generation\n"
        "      review_trigger: PyO3 0.30\n"
        "    - relationship: maturin-pyo3\n"
        "      rationale: no trigger\n",
    )

    envelope = build_dependency_family_envelope(layout)

    assert envelope["exceptions"] == [
        {
            "relationship": "pyo3-build-config-family",
            "rationale": "older generation",
            "review_trigger": "PyO3 0.30",
        }
    ]
    assert len(envelope["problems"]) == 1


def test_the_estate_relationships_are_loaded(layout: pathlib.Path) -> None:
    """The versioned data ships with the canon and reaches the envelope."""
    envelope = build_dependency_family_envelope(layout)

    assert envelope["relationships_version"] == 1
    ids = {item["id"] for item in envelope["relationships"]}
    assert {
        "pyo3-build-config-family",
        "maturin-pyo3",
        "ortho-config-fluent-bundle",
    } <= ids
