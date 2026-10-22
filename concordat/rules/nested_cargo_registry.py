"""Register the nested-cargo rule packages' envelope builders.

The packages share one registration shape, and `packages` is already near the
size a module may grow to, so their builders live here and `packages` merges the
two mappings below into its own. Each policy reads its exemptions and patterns
through `data.parameters`, so the second argument exists only to give every
builder the one callable type the registry expects.
"""

from __future__ import annotations

import types
import typing as typ

from .nested_cargo_lock_envelope import ENVELOPE_KIND as LOCK_ENVELOPE_KIND
from .nested_cargo_lock_envelope import (
    NestedCargoLockEnvelope,
    build_nested_cargo_lock_envelope,
)
from .nested_cargo_offline_envelope import ENVELOPE_KIND as OFFLINE_ENVELOPE_KIND
from .nested_cargo_offline_envelope import (
    NestedCargoOfflineEnvelope,
    build_nested_cargo_offline_envelope,
)
from .nested_cargo_manifest_envelope import ENVELOPE_KIND as MANIFEST_ENVELOPE_KIND
from .nested_cargo_manifest_envelope import (
    NestedCargoManifestEnvelope,
    build_nested_cargo_manifest_envelope,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

type NestedCargoEnvelope = (
    NestedCargoLockEnvelope | NestedCargoOfflineEnvelope | NestedCargoManifestEnvelope
)


def _lock_envelope(
    checkout: pathlib.Path,
    _parameters: cabc.Mapping[str, object] | None = None,
) -> NestedCargoLockEnvelope:
    """Build the NC-001/NC-002 envelope, ignoring the manifest parameters.

    Returns
    -------
    NestedCargoLockEnvelope
        The `policy-input/nested-cargo-fixture-lockfiles` document for *checkout*.
    """
    return build_nested_cargo_lock_envelope(checkout)


def _manifest_envelope(
    checkout: pathlib.Path,
    _parameters: cabc.Mapping[str, object] | None = None,
) -> NestedCargoManifestEnvelope:
    """Build the NC-003 envelope, ignoring the manifest parameters.

    Returns
    -------
    NestedCargoManifestEnvelope
        The `policy-input/nested-cargo-fixture-manifests` document for *checkout*.
    """
    return build_nested_cargo_manifest_envelope(checkout)


BY_PACKAGE: typ.Final = types.MappingProxyType({
    "nested-cargo-fixture-lockfiles": _lock_envelope,
    "nested-cargo-offline-tests": build_nested_cargo_offline_envelope,
    "nested-cargo-fixture-manifests": _manifest_envelope,
})

BY_KIND: typ.Final = types.MappingProxyType({
    LOCK_ENVELOPE_KIND: _lock_envelope,
    OFFLINE_ENVELOPE_KIND: build_nested_cargo_offline_envelope,
    MANIFEST_ENVELOPE_KIND: _manifest_envelope,
})
