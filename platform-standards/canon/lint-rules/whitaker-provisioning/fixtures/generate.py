"""Regenerate the policy-input fixture envelopes for whitaker-provisioning.

Each directory under ``repos/`` is a miniature checkout holding the workflows,
composite actions, Makefiles and scripts one behaviour needs. The production
envelope builder is run over each, so the Rego suite verifies against the
documents the sensor actually produces.

A fixture checkout has no ``origin`` remote, so the builder records no
repository name. The names the exemption clauses match on are therefore set
here, from ``REPOSITORY_NAMES``, as the one field not taken from the builder.

One synthetic envelope covers what no checkout can: an envelope of an unknown
schema version.

Run from the rule package directory::

    uv run python fixtures/generate.py

``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify --data``.
"""

from __future__ import annotations

import json
import typing as typ
from pathlib import Path

from concordat.rules.whitaker_provisioning_envelope import (
    build_whitaker_provisioning_envelope,
)

FIXTURES_DIR: typ.Final = Path(__file__).resolve().parent
REPOS_DIR: typ.Final = FIXTURES_DIR / "repos"
ENVELOPES_DIR: typ.Final = FIXTURES_DIR / "envelopes"

#: The GitHub slug each fixture checkout stands for. Every other fixture is
#: an ordinary consumer.
REPOSITORY_NAMES: typ.Final[dict[str, str]] = {
    "action-repository": "leynos/shared-actions",
    "exempt-developer-script": "leynos/agent-helper-scripts",
    "producer": "leynos/whitaker",
}
CONSUMER_NAME: typ.Final = "leynos/consumer"


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
            "dict[str, object]", dict(build_whitaker_provisioning_envelope(repo))
        )
        envelope["repository"] = {
            "path": repo.name,
            "name": REPOSITORY_NAMES.get(repo.name, CONSUMER_NAME),
        }
        envelopes[repo.name.replace("-", "_")] = envelope
    envelopes["unknown_schema"] = {
        "schema_version": 2,
        "kind": "policy-input/whitaker-provisioning",
        "repository": {"path": "unknown-schema", "name": CONSUMER_NAME},
        "workflows": [],
        "actions": [],
        "scripts": [],
    }
    return envelopes


def main() -> None:
    """Write each envelope to ``envelopes/`` and the bundle to ``data.json``."""
    envelopes = build_envelopes()
    ENVELOPES_DIR.mkdir(exist_ok=True)
    for name, envelope in envelopes.items():
        (ENVELOPES_DIR / f"{name}.json").write_text(
            json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    (FIXTURES_DIR / "data.json").write_text(
        json.dumps({"fixtures": envelopes}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
