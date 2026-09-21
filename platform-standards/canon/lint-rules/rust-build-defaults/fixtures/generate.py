"""Regenerate the policy-input fixture envelopes for rust-build-defaults.

Each directory under ``repos/`` is a miniature checkout: a ``Cargo.toml``, and
whichever of ``.cargo/config.toml``, ``rust-toolchain.toml``, and
``docs/developers-guide.md`` the behaviour under test needs. The production
envelope builder is run over each one, so the fixtures the Rego suite verifies
against are the documents the sensor actually produces rather than a
handwritten approximation of them.

Three synthetic envelopes cover cases that cannot be built from a checkout: an
envelope of an unknown schema version, one whose ``cargo`` payload has the
wrong shape, and one whose exception document the filesystem refused to read.
The last cannot be a checkout because git records no permission bits beyond the
executable one, so an unreadable file does not survive a clone.

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


NIGHTLY_PIN: typ.Final[dict[str, object]] = {
    "path": "rust-toolchain.toml",
    "channel": "nightly-2026-08-23",
    "channel_kind": "nightly-dated",
    "parse_error": None,
}

STANDARD_SOURCES: typ.Final[list[dict[str, object]]] = [
    {
        "name": "build",
        "kind": "build",
        "key": None,
        "linux": False,
        "linux_only": False,
        "classified": True,
        "flags": ["-Zthreads=8"],
    },
    {
        "name": 'target.cfg(target_os = "linux")',
        "kind": "target",
        "key": 'cfg(target_os = "linux")',
        "linux": True,
        "linux_only": True,
        "classified": True,
        "flags": ["-Zthreads=8", "-Clink-arg=-fuse-ld=mold"],
    },
]


def _applicability(*, cargo_config: bool, toolchain_file: bool) -> dict[str, object]:
    """Return an applicability block for a checkout with a root manifest.

    Returns
    -------
    dict[str, object]
        The applicability block.
    """
    return {
        "root_cargo_toml": True,
        "rust_surfaces_declared": False,
        "cargo_config": cargo_config,
        "toolchain_file": toolchain_file,
    }


def _malformed(
    name: str, *, schema_version: int, surfaces: object
) -> dict[str, object]:
    """Return an envelope the policy must refuse before reading anything else.

    Both guard cases are the same document with one field spoiled, so they are
    one builder: an unrecognized schema version, and a `cargo.surfaces` that is
    not an array.

    Returns
    -------
    dict[str, object]
        The malformed envelope.
    """
    return {
        "schema_version": schema_version,
        "kind": "policy-input/rust-build-defaults",
        "repository": {"path": name, "name": None},
        "applicability": _applicability(cargo_config=False, toolchain_file=False),
        "cargo": {"parsed": None, "surfaces": surfaces},
        "toolchain": None,
        "cargo_config": None,
        "exceptions": [],
    }


def _exception_unreadable() -> dict[str, object]:
    """Return an envelope whose exception document could not be read.

    Not a checkout, because git records no permission bits beyond the
    executable one: an unreadable file does not survive a clone.

    Returns
    -------
    dict[str, object]
        The envelope whose exception document carries a read error.
    """
    return {
        "schema_version": 1,
        "kind": "policy-input/rust-build-defaults",
        "repository": {"path": "exception-unreadable", "name": None},
        "applicability": _applicability(cargo_config=True, toolchain_file=True),
        "cargo": {"parsed": None, "surfaces": [{"path": "Cargo.toml"}]},
        "toolchain": NIGHTLY_PIN,
        "cargo_config": {
            "path": ".cargo/config.toml",
            "sources": STANDARD_SOURCES,
            "backends": [],
            "unstable_codegen_backend": None,
            "parse_error": None,
        },
        "exceptions": [
            {
                "path": "docs/developers-guide.md",
                "present": True,
                "read_error": "[Errno 13] Permission denied",
                "sections": [],
            }
        ],
    }


def synthetic_envelopes() -> dict[str, dict[str, object]]:
    """Return the envelopes that no checkout could produce.

    Returns
    -------
    dict[str, dict[str, object]]
        Malformed and unreadable envelopes covering the fail-closed findings.
    """
    return {
        "unknown_schema": _malformed("unknown-schema", schema_version=2, surfaces=[]),
        "invalid_cargo": _malformed(
            "invalid-cargo", schema_version=1, surfaces="Cargo.toml"
        ),
        "exception_unreadable": _exception_unreadable(),
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
