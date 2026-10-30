"""Specify how a declaration is resolved to one exact locked package identity.

A type-identity relationship is a claim about resolved identities. The facts
must name the exact entry the lock records, repeat the owner's edges to the
other members, and refuse to pick one of several plausible entries.
"""

from __future__ import annotations

import typing as typ

from concordat.rules.dependency_family_resolution import (
    package_id,
    parse_cargo_version,
    resolve_cargo,
    resolve_python,
)

_LOCK: typ.Final[dict[str, object]] = {
    "version": 4,
    "package": [
        {
            "name": "demo",
            "version": "0.1.0",
            "dependencies": [
                "pyo3 0.28.3",
                "pyo3-build-config 0.28.3",
                "serde-pyobject 0.9.0",
            ],
        },
        {"name": "pyo3", "version": "0.28.3"},
        {"name": "pyo3-build-config", "version": "0.28.3"},
        {"name": "serde-pyobject", "version": "0.8.0"},
        {"name": "serde-pyobject", "version": "0.9.0"},
    ],
}


def test_a_package_is_resolved_to_the_one_entry_its_requirement_admits() -> None:
    """Two locked serde-pyobject versions are narrowed by the declaration."""
    resolved = resolve_cargo(
        _LOCK,
        "Cargo.lock",
        {"serde-pyobject": "0.8.0"},
        manifest_name="demo",
        manifest_version="0.1.0",
        members={"pyo3", "serde-pyobject"},
    )

    bridge = resolved["serde-pyobject"]
    assert bridge["package_id"] == "serde-pyobject 0.8.0"
    assert bridge["reason"] is None
    # The owner links the 0.9.0 entry, so the declaration's direct identity is
    # not the one the rest of the workspace was built against.
    assert bridge["member_edges"]["serde-pyobject"] == ["serde-pyobject 0.9.0"]
    assert bridge["member_edges"]["pyo3"] == ["pyo3 0.28.3"]


def test_an_ambiguous_package_is_not_silently_chosen() -> None:
    """When two entries both satisfy, the identity is unknown with a reason."""
    resolved = resolve_cargo(
        _LOCK,
        "Cargo.lock",
        {"serde-pyobject": "*"},
        manifest_name="demo",
        manifest_version="0.1.0",
        members={"serde-pyobject"},
    )

    assert resolved["serde-pyobject"]["package_id"] is None
    assert resolved["serde-pyobject"]["reason"] == (
        "serde-pyobject has more than one locked version"
    )


def test_a_package_absent_from_the_lock_says_so() -> None:
    """A missing entry is evidence, never an empty identity that might pass."""
    resolved = resolve_cargo(
        _LOCK,
        "Cargo.lock",
        {"sha2": "0.10.0"},
        manifest_name="demo",
        manifest_version="0.1.0",
        members={"sha2"},
    )

    assert resolved["sha2"]["versions"] == []
    assert resolved["sha2"]["reason"] == "sha2 is not in the lock"


def test_a_virtual_workspace_still_links_its_members() -> None:
    """The member's own entry supplies the edges when no root package exists."""
    lock: dict[str, object] = {
        "version": 4,
        "package": [
            {
                "name": "native",
                "version": "0.1.0",
                "dependencies": ["pyo3", "pyo3-build-config"],
            },
            {"name": "pyo3", "version": "0.28.3"},
            {"name": "pyo3-build-config", "version": "0.28.3"},
        ],
    }

    resolved = resolve_cargo(
        lock,
        "Cargo.lock",
        {"pyo3": "0.28.3", "pyo3-build-config": "0.28.3"},
        manifest_name="native",
        manifest_version="0.1.0",
        members={"pyo3", "pyo3-build-config"},
    )

    assert resolved["pyo3"]["member_edges"]["pyo3-build-config"] == [
        "pyo3-build-config 0.28.3"
    ]


def test_python_names_are_resolved_in_their_normalised_form() -> None:
    """The uv root's edge uses the PEP 503 spelling Dependabot matches."""
    document: dict[str, object] = {
        "version": 1,
        "package": [
            {
                "name": "demo",
                "version": "0.1.0",
                "source": {"virtual": "."},
                "dependencies": [{"name": "Typing_Extensions"}],
            },
            {"name": "typing-extensions", "version": "4.12.0"},
        ],
    }

    resolved = resolve_python(
        document,
        "uv.lock",
        {"typing-extensions": ">=4,<5"},
        members={"typing-extensions"},
    )

    assert resolved["typing-extensions"]["package_id"] == "typing-extensions 4.12.0"
    assert resolved["typing-extensions"]["member_edges"]["typing-extensions"] == [
        "typing-extensions 4.12.0"
    ]


def test_an_identity_is_stable_and_names_its_source_when_it_has_one() -> None:
    """Two registries may serve one name and version; the identity keeps them apart."""
    assert package_id("pyo3", "0.28.3") == "pyo3 0.28.3"
    assert package_id("pyo3", "0.28.3", "registry+https://example") == (
        "pyo3 0.28.3 (registry+https://example)"
    )


def test_a_manifest_version_must_be_parseable_to_resolve_the_owner() -> None:
    """A workspace placeholder such as `version.workspace = true` is not usable."""
    assert parse_cargo_version("0.1.0") == "0.1.0"
    assert parse_cargo_version({"workspace": True}) is None
