"""Regenerate the policy-input fixture envelopes for rust-build-defaults.

Each directory under ``repos/`` is a miniature checkout: a ``Cargo.toml``, and
whichever of ``.cargo/config.toml``, ``rust-toolchain.toml``, and
``docs/developers-guide.md`` the behaviour under test needs. The production
envelope builder is run over each one, so the fixtures the Rego suite verifies
against are the documents the sensor actually produces rather than a
handwritten approximation of them.

Two synthetic envelopes cover cases that cannot be built from a checkout: an
envelope of an unknown schema version, and one whose ``cargo`` payload has the
wrong shape.

Run from the rule package directory::

    uv run python fixtures/generate.py

``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify --data``.
"""

from __future__ import annotations

import json
import typing as typ
from pathlib import Path

from concordat.rules.envelope import build_build_defaults_envelope

FIXTURES_DIR: typ.Final = Path(__file__).resolve().parent
REPOS_DIR: typ.Final = FIXTURES_DIR / "repos"
ENVELOPES_DIR: typ.Final = FIXTURES_DIR / "envelopes"
RULE_DIR: typ.Final = FIXTURES_DIR.parent

# The manifest's own defaults, so the fixtures are evaluated under the
# parameters the sensor ships rather than under the builder's fallbacks.
PARAMETERS: typ.Final[dict[str, object]] = {
    "exception_documents": ["docs/developers-guide.md"],
    "exception_keyword": "Cranelift",
}


def build_envelopes() -> dict[str, dict[str, object]]:
    """Return one envelope per fixture checkout, keyed by directory name.

    Returns
    -------
    dict[str, dict[str, object]]
        The generated envelopes, with machine-specific paths removed.
    """
    envelopes: dict[str, dict[str, object]] = {}
    for repo in sorted(path for path in REPOS_DIR.iterdir() if path.is_dir()):
        envelope = typ.cast(
            "dict[str, object]",
            dict(build_build_defaults_envelope(repo, PARAMETERS)),
        )
        # The recorded path is the fixture's name, never the absolute location
        # of the checkout, so the checked-in envelopes stay identical on every
        # machine that regenerates them.
        envelope["repository"] = {"path": repo.name, "name": None}
        envelopes[repo.name.replace("-", "_")] = envelope
    return envelopes


def synthetic_envelopes() -> dict[str, dict[str, object]]:
    """Return the envelopes that no checkout could produce.

    Returns
    -------
    dict[str, dict[str, object]]
        Malformed envelopes covering the envelope-guard findings.
    """
    return {
        "unknown_schema": {
            "schema_version": 2,
            "kind": "policy-input/rust-build-defaults",
            "repository": {"path": "unknown-schema", "name": None},
            "applicability": {
                "root_cargo_toml": True,
                "rust_surfaces_declared": False,
                "cargo_config": False,
                "toolchain_file": False,
            },
            "cargo": {"parsed": None, "surfaces": []},
            "toolchain": None,
            "cargo_config": None,
            "exceptions": [],
        },
        "invalid_cargo": {
            "schema_version": 1,
            "kind": "policy-input/rust-build-defaults",
            "repository": {"path": "invalid-cargo", "name": None},
            "applicability": {
                "root_cargo_toml": True,
                "rust_surfaces_declared": False,
                "cargo_config": False,
                "toolchain_file": False,
            },
            "cargo": {"parsed": None, "surfaces": "Cargo.toml"},
            "toolchain": None,
            "cargo_config": None,
            "exceptions": [],
        },
    }


def main() -> None:
    """Regenerate every envelope and the bundled data document."""
    ENVELOPES_DIR.mkdir(exist_ok=True)
    envelopes = build_envelopes() | synthetic_envelopes()
    for key, envelope in sorted(envelopes.items()):
        target = ENVELOPES_DIR / f"{key}.json"
        target.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    bundle = FIXTURES_DIR / "data.json"
    bundle.write_text(
        json.dumps({"fixtures": dict(sorted(envelopes.items()))}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
