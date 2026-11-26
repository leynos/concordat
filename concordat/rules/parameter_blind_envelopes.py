"""Registry builders for packages that read no manifest parameters.

`packages` is near the size a module may grow to, so the builders that only
adapt an envelope builder to the registry's one callable type live here. Each
policy behind them declares no tunables, or reads them through
`data.parameters` in the policy, so the second argument exists only to give
every builder the type the registry expects.
"""

from __future__ import annotations

import typing as typ

from .codescene_coverage_envelope import (
    CoverageEnvelope,
    build_codescene_coverage_envelope,
)
from .dependabot_envelope import DependabotEnvelope, build_dependabot_envelope
from .dependency_family_envelope import (
    DependencyFamilyEnvelope,
    build_dependency_family_envelope,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

__all__ = [
    "coverage_envelope",
    "dependabot_envelope",
    "dependency_family_envelope",
]


def coverage_envelope(
    checkout: pathlib.Path,
    _parameters: cabc.Mapping[str, object] | None = None,
) -> CoverageEnvelope:
    """Build the CV-005 workflow envelope, ignoring the manifest parameters.

    CV-005 declares no tunables: every clause it states is a property of the
    estate's topology rather than something a repository may configure, so
    there is nothing for the second argument to change.

    Returns
    -------
    CoverageEnvelope
        The `policy-input/main-owned-codescene-coverage` document for
        *checkout*.
    """
    return build_codescene_coverage_envelope(checkout)


def dependabot_envelope(
    checkout: pathlib.Path,
    _parameters: cabc.Mapping[str, object] | None = None,
) -> DependabotEnvelope:
    """Build the DB-005 Dependabot envelope, ignoring the manifest parameters.

    DB-005 declares no tunables: the update shape is estate policy rather
    than something a repository may configure.

    Returns
    -------
    DependabotEnvelope
        The `policy-input/dependabot-update-shape` document for *checkout*.
    """
    return build_dependabot_envelope(checkout)


def dependency_family_envelope(
    checkout: pathlib.Path,
    _parameters: cabc.Mapping[str, object] | None = None,
) -> DependencyFamilyEnvelope:
    """Build the dependency-family envelope, ignoring the manifest parameters.

    The relationships come from the estate's versioned data and the exceptions
    from the repository's `.concordat`, so the rule has no tunables.

    Returns
    -------
    DependencyFamilyEnvelope
        The `policy-input/dependency-family-contracts` document for *checkout*.
    """
    return build_dependency_family_envelope(checkout)
