"""Regenerate the policy-input fixture envelopes for whitaker-provisioning.

Each directory under ``repos/`` is a miniature checkout holding the workflows,
composite actions, Makefiles and scripts one behaviour needs. The production
envelope builder is run over each, so the Rego suite verifies against the
documents the sensor actually produces.

A fixture checkout has no ``origin`` remote, so the builder records no
repository name. The names the exemption clauses match on are therefore set
here, from ``REPOSITORY_NAMES``, as the one field not taken from the builder.

Whether each pinned revision of install-whitaker is certified is decided over
a synthetic action repository, ``FixtureHistory``, by the production decision.

One synthetic envelope covers what no checkout can: an envelope of an unknown
schema version.

Run from the rule package directory::

    uv run python fixtures/generate.py

``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify --data``.
"""

from __future__ import annotations

import functools
import json
import typing as typ
from pathlib import Path

from concordat.rules.whitaker_certification import (
    CertificationUnavailable,
    Floor,
    NoSuchCommit,
    certify_revision,
)
from concordat.rules.whitaker_provisioning_envelope import (
    build_whitaker_provisioning_envelope,
    with_install_certifications,
)

FIXTURES_DIR: typ.Final = Path(__file__).resolve().parent
REPOS_DIR: typ.Final = FIXTURES_DIR / "repos"
ENVELOPES_DIR: typ.Final = FIXTURES_DIR / "envelopes"

#: The GitHub slug each fixture checkout stands for. Every other fixture is
#: an ordinary consumer.
REPOSITORY_NAMES: typ.Final[dict[str, str]] = {
    "action-repository": "leynos/shared-actions",
    "exempt-developer-script": "leynos/agent-helper-scripts",
    "exempt-pinned-suite": "leynos/example-pinned-suite",
    "producer": "leynos/whitaker",
}
CONSUMER_NAME: typ.Final = "leynos/consumer"
ACTION: typ.Final = "leynos/shared-actions/.github/actions/install-whitaker"

#: The checkout whose pin the audit leaves unresolved, as an envelope built
#: without the command step is. The policy must not read that as certified.
UNRESOLVED_FIXTURE: typ.Final = "unresolved-pin"

FLOOR_TAG: typ.Final = "certified/install-whitaker/0.2.12"
FLOOR: typ.Final = "f" * 40
APPROVED_DIGEST: typ.Final = "approved-runtime-digest"
CHANGED_DIGEST: typ.Final = "changed-runtime-digest"


class FixtureHistory:
    """A synthetic action repository, so each fixture's verdict is decided.

    One floor, and the commits around it. The verdicts in the envelopes are
    those the production decision gives over this history, not handwritten:

    * ``a``: after the floor, runtime content unchanged (certified);
    * ``b``: after the floor, runtime content changed (uncertified);
    * ``c``: on the default branch but before the floor (uncertified);
    * ``d``: not on the default branch, as a fork-only commit is (uncertified);
    * ``e``: a commit GitHub does not know (uncertified);
    * ``9``: a lookup the API refused for its rate limit (unknown).
    """

    on_branch: typ.ClassVar = frozenset({FLOOR, "a" * 40, "b" * 40, "c" * 40})
    after_floor: typ.ClassVar = frozenset({FLOOR, "a" * 40, "b" * 40})
    digests: typ.ClassVar = {
        FLOOR: APPROVED_DIGEST,
        "a" * 40: APPROVED_DIGEST,
        "b" * 40: CHANGED_DIGEST,
        "c" * 40: APPROVED_DIGEST,
    }

    def floors(self) -> list[Floor]:
        """Return the one certification tag."""
        return [Floor(FLOOR_TAG, FLOOR)]

    def is_on_default_branch(self, sha: str) -> bool:
        """Report whether *sha* is on the default branch.

        Returns
        -------
        bool
            True for the commits on the default branch.

        Raises
        ------
        NoSuchCommit
            For the commit no repository knows.
        CertificationUnavailable
            For the commit whose lookup the API refused.
        """
        if sha == "e" * 40:
            raise NoSuchCommit(sha)
        if sha == "9" * 40:
            message = "compare/main...9 was rate limited"
            raise CertificationUnavailable(message, rate_limited=True)
        return sha in self.on_branch

    def descends_from(self, floor: str, sha: str) -> bool:
        """Report whether *sha* equals or follows the *floor*."""
        return floor == FLOOR and sha in self.after_floor

    def runtime_digest(self, sha: str) -> str | None:
        """Return the digest of the commit's action directory."""
        return self.digests.get(sha)


def build_envelopes() -> dict[str, dict[str, object]]:
    """Return one envelope per fixture checkout, keyed by directory name.

    Returns
    -------
    dict[str, dict[str, object]]
        The generated envelopes, with machine-specific paths removed.
    """
    envelopes: dict[str, dict[str, object]] = {}
    for repo in sorted(path for path in REPOS_DIR.iterdir() if path.is_dir()):
        built = build_whitaker_provisioning_envelope(repo)
        if repo.name != UNRESOLVED_FIXTURE:
            built = with_install_certifications(
                built, functools.partial(certify_revision, FixtureHistory()), ACTION
            )
        envelope = typ.cast("dict[str, object]", dict(built))
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
        "install_certifications": {},
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
