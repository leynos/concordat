"""Compose the certification step of the Whitaker provisioning rule.

Whether a pinned `install-whitaker` revision is certified needs the action
repository, so it is not part of building the envelope, which only reads the
checkout. The rule-run command composes it explicitly, as it does for action
pins (`packages.resolving_pins`), so the network stays at the command boundary.
"""

from __future__ import annotations

import functools
import typing as typ

from .packages import rule_package_dir, rule_parameters
from .whitaker_provisioning_envelope import (
    ENVELOPE_KIND,
    ProvisioningEnvelope,
    with_install_certifications,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .packages import EnvelopeResolver, RuleEnvelope
    from .whitaker_certification import Certifier


def certifying(
    builder: EnvelopeResolver,
    make_certifier: cabc.Callable[[cabc.Mapping[str, object]], Certifier],
) -> EnvelopeResolver:
    """Return *builder* with a provisioning envelope's pins certified.

    *make_certifier* receives the package's manifest parameters (where the
    action lives, the tag prefix) and returns the certifier. Other envelope
    kinds pass through unchanged, and a checkout that pins no revision never
    calls the certifier.

    Returns
    -------
    EnvelopeResolver
        A resolver that builds with *builder*, then records the certifications.
    """
    return functools.partial(_build_certifying, builder, make_certifier)


def _build_certifying(
    builder: EnvelopeResolver,
    make_certifier: cabc.Callable[[cabc.Mapping[str, object]], Certifier],
    rule_id: str,
    checkout: pathlib.Path,
) -> RuleEnvelope:
    """Build *rule_id*'s envelope, certifying revisions when it is a provisioning one.

    Returns
    -------
    RuleEnvelope
        The built envelope, with `install_certifications` recorded for a
        provisioning one.
    """
    envelope = builder(rule_id, checkout)
    if envelope.get("kind") != ENVELOPE_KIND:
        return envelope
    parameters = rule_parameters(rule_package_dir(rule_id))
    repository = parameters.get("action_repository", "leynos/shared-actions")
    directory = parameters.get("action_directory", ".github/actions/install-whitaker")
    return with_install_certifications(
        typ.cast("ProvisioningEnvelope", envelope),
        make_certifier(parameters),
        f"{repository}/{directory}",
    )
