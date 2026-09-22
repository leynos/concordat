"""Locate a canon lint-rule package, read its manifest, and choose its input.

Three questions about a rule package, none of them about evaluating one: where
its policy lives, what its manifest declares, and which policy-input envelope
it is audited over. `runner` answers the fourth — what Conftest made of that
envelope — and imports this module for the rest.

The envelope choice is the part with teeth. A package reads facts of one shape
and its policy expects that shape; hand it another and the policy does not
degrade, it answers confidently about a document it was never written for.
There is therefore no default, only the two routes below and a refusal.
"""

from __future__ import annotations

import functools
import importlib.resources
import pathlib
import re
import types
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .envelope import (
    BUILD_DEFAULTS_ENVELOPE_KIND,
    ENVELOPE_KIND,
    BuildDefaultsEnvelope,
    PolicyEnvelope,
    build_build_defaults_envelope,
    build_envelope,
)
from .fs_probe import probe_file
from .markdown_envelope import ENVELOPE_KIND as MARKDOWN_ENVELOPE_KIND
from .markdown_envelope import MarkdownEnvelope, build_markdown_envelope

if typ.TYPE_CHECKING:
    import collections.abc as cabc

type RuleEnvelope = PolicyEnvelope | BuildDefaultsEnvelope | MarkdownEnvelope
type EnvelopeResolver = cabc.Callable[[str, pathlib.Path], RuleEnvelope]

_yaml = YAML(typ="safe")


def _resolve_rule_packages_dir() -> pathlib.Path:
    """Return the canon lint-rule tree, whether installed or run from source.

    A wheel ships the policies inside the package at ``concordat/canon/
    lint-rules`` (see the ``concordat.canon`` package-data mapping in
    ``pyproject.toml``), reachable via ``importlib.resources``. A source
    checkout keeps them in the sibling ``platform-standards`` tree, so that
    layout is used as a fallback.

    Returns
    -------
    pathlib.Path
        Directory containing the canon lint-rule packages.
    """
    packaged = importlib.resources.files("concordat") / "canon" / "lint-rules"
    if isinstance(packaged, pathlib.Path) and packaged.is_dir():
        return packaged
    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "platform-standards"
        / "canon"
        / "lint-rules"
    )
    if source.is_dir():
        return source
    return pathlib.Path(str(packaged))


@functools.lru_cache(maxsize=1)
def _rule_packages_dir() -> pathlib.Path:
    """Return the cached canon lint-rule tree."""
    return _resolve_rule_packages_dir()


# A rule package is one canonical name: lower-case ASCII words joined by
# single hyphens. Anything else — a separator, a dot segment, punctuation — is
# refused before it can be joined to a path.
_RULE_ID_PATTERN: typ.Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validated_rule_id(rule_id: str) -> str:
    """Return *rule_id* if it is a canonical package name, else raise."""
    if not _RULE_ID_PATTERN.fullmatch(rule_id):
        message = (
            f"invalid rule package {rule_id!r}; expected lower-case words "
            "joined by single hyphens"
        )
        raise OperationalRuleError(
            message,
            operation="load-rule-package",
            resource=rule_id,
        )
    return rule_id


def rule_package_dir(rule_id: str) -> pathlib.Path:
    """Return the rule package directory for *rule_id*, or raise if unknown.

    The identifier is validated before it is joined to a path, and the joined
    path is then confirmed to stay under the packages root. The pattern alone
    already excludes traversal, but the containment check means a future
    loosening of the pattern cannot silently reach outside the root.

    The packages root is resolved here rather than at import, so a missing or
    unreadable rule tree fails when a rule is run rather than when the module
    is imported — importing the CLI should not depend on the policy tree.

    Returns
    -------
    pathlib.Path
        Directory containing the requested rule package.

    Raises
    ------
    OperationalRuleError
        If *rule_id* is invalid or its package directory is unavailable.
    """
    # Validation first: a malformed identifier is a local error, and must not
    # cost the packages-root lookup (which touches the filesystem) to reject.
    validated = _validated_rule_id(rule_id)
    packages_root = _rule_packages_dir()
    rule_dir = packages_root / validated
    root = packages_root.resolve()
    candidate = rule_dir.resolve()
    if not candidate.is_relative_to(root):
        message = f"rule package {rule_id!r} resolves outside {root}"
        raise OperationalRuleError(
            message,
            operation="load-rule-package",
            resource=rule_id,
        )
    if not (rule_dir / "policy").is_dir():
        message = f"unknown rule package {rule_id!r}; expected {rule_dir}/policy"
        raise OperationalRuleError(
            message,
            operation="load-rule-package",
            resource=rule_id,
        )
    return rule_dir


def rule_manifest(rule_dir: pathlib.Path) -> dict[str, object]:
    """Return the rule package's parsed manifest, or an empty mapping.

    Returns
    -------
    dict[str, object]
        The parsed `rule.yaml`, or `{}` when the package ships none. Values
        are narrowed by each caller after its own shape check rather than
        handed out as `Any`, which would let a malformed manifest reach the
        policy as if it were valid.

    Raises
    ------
    OperationalRuleError
        If the rule manifest cannot be read or is not a mapping.
    """
    manifest_path = rule_dir / "rule.yaml"
    # Not `is_file()`. It answers False for a manifest that is absent, for one
    # that is a directory, for a dangling link, and for a path the filesystem
    # refused to describe. Returning `{}` for the last three hands the policy
    # its own baked-in defaults in place of the ones the package declares, and
    # silently loses the `sensor.input` declaration that decides which envelope
    # the package is audited over. That is this change's own subject, one level
    # down. Reported by jm-concordat-176, who fixed it separately on #176.
    probe = probe_file(manifest_path)
    if probe.read_error is not None:
        message = f"cannot read rule manifest {probe.read_error}"
        raise OperationalRuleError(
            message,
            operation="load-rule-manifest",
            resource=manifest_path,
        )
    if not probe.present:
        return {}
    try:
        manifest = _yaml.load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        message = f"cannot read rule manifest {manifest_path}: {error}"
        raise OperationalRuleError(
            message,
            operation="load-rule-manifest",
            resource=manifest_path,
        ) from error
    if not isinstance(manifest, dict):
        message = f"rule manifest {manifest_path} is not a mapping"
        raise OperationalRuleError(
            message,
            operation="load-rule-manifest",
            resource=manifest_path,
        )
    return typ.cast("dict[str, object]", manifest)


def rule_parameters(rule_dir: pathlib.Path) -> dict[str, object]:
    """Return the rule manifest's parameter defaults.

    The policies read their tunables from ``data.parameters``; without this
    the manifest's declared defaults would be inert and only the ``default``
    rules baked into the Rego would ever apply.

    A manifest that cannot be read propagates `_rule_manifest`'s operational
    error unchanged; a manifest that declares no defaults yields none.

    Returns
    -------
    dict[str, object]
        Parameter defaults declared by the rule manifest.
    """
    parameters = rule_manifest(rule_dir).get("parameters")
    if not isinstance(parameters, dict):
        return {}
    defaults = typ.cast("dict[str, object]", parameters).get("defaults")
    if not isinstance(defaults, dict):
        return {}
    return dict(typ.cast("dict[str, object]", defaults))


def _makefile_envelope(
    checkout: pathlib.Path,
    _parameters: cabc.Mapping[str, object] | None = None,
) -> PolicyEnvelope:
    """Build the Makefile envelope, ignoring parameters it does not read.

    The signature matches every other builder so the mappings below can hold
    one callable type. `rust-makefile-baseline` reads its tunables through
    `data.parameters` in the policy rather than while the envelope is built,
    so there is nothing here for the second argument to change.

    Returns
    -------
    PolicyEnvelope
        The `policy-input/rust-makefile-baseline` document for *checkout*.
    """
    return build_envelope(checkout)


def _markdown_envelope(
    checkout: pathlib.Path,
    _parameters: cabc.Mapping[str, object] | None = None,
) -> MarkdownEnvelope:
    """Build the Markdown envelope, ignoring parameters it does not read.

    `markdown-formatting-baseline` reads its tunables through
    `data.parameters` in the policy, as `rust-makefile-baseline` does, so the
    second argument exists only to give every builder one callable type.

    Returns
    -------
    MarkdownEnvelope
        The `policy-input/markdown-formatting-baseline` document for
        *checkout*.
    """
    return build_markdown_envelope(checkout)


# Every rule package's envelope builder, keyed by package identifier. The
# mapping is the complete list rather than the exceptions to a default: a
# package that reads facts of one shape and a policy that expects another
# produce a confident verdict about the wrong document, which is the failure a
# fail-closed audit exists to avoid. An identifier that is not here, and
# declares no input kind of its own, is refused.
#
# The view is read-only. Package selection is a composition decision, not state
# a caller may reach in and change.
PACKAGE_ENVELOPE_BUILDERS: typ.Final = types.MappingProxyType({
    "rust-makefile-baseline": _makefile_envelope,
    "rust-build-defaults": build_build_defaults_envelope,
})

# The same builders by the envelope kind they produce, so a package whose input
# is a shape another package already builds can declare it in its own
# `rule.yaml` rather than needing an edit here.
INPUT_KIND_ENVELOPE_BUILDERS: typ.Final = types.MappingProxyType({
    ENVELOPE_KIND: _makefile_envelope,
    BUILD_DEFAULTS_ENVELOPE_KIND: build_build_defaults_envelope,
    MARKDOWN_ENVELOPE_KIND: _markdown_envelope,
})


def _declared_input_kind(rule_dir: pathlib.Path) -> str | None:
    """Return the envelope kind the package manifest declares, if any.

    Returns
    -------
    str | None
        The declared `sensor.input` kind, or ``None`` when the manifest
        declares none.
    """
    manifest = rule_manifest(rule_dir)
    sensor = manifest.get("sensor")
    if not isinstance(sensor, dict):
        return None
    declared = typ.cast("dict[str, object]", sensor).get("input")
    return declared if isinstance(declared, str) else None


def _unresolvable_package_message(rule_id: str, declared: str | None) -> str:
    """Return the diagnostic for a package whose envelope cannot be chosen.

    Both forms name the way out, because an error that says only "no" leaves
    the reader to find the mapping and the manifest key themselves.

    Returns
    -------
    str
        The message, naming the package and what would have resolved it.
    """
    registered = ", ".join(sorted(PACKAGE_ENVELOPE_BUILDERS))
    kinds = ", ".join(sorted(INPUT_KIND_ENVELOPE_BUILDERS))
    if declared is None:
        return (
            f"rule package {rule_id!r} declares no sensor.input and is not one "
            f"of the packages with an envelope builder ({registered}); declare "
            f"one of the known input kinds ({kinds}) in its rule.yaml, or "
            "register a builder for it"
        )
    return (
        f"rule package {rule_id!r} declares the unknown input kind "
        f"{declared!r}; the known kinds are {kinds}"
    )


def default_envelope_builder(rule_id: str, checkout: pathlib.Path) -> RuleEnvelope:
    """Return the policy input *rule_id* is evaluated over.

    This is the composition layer's resolver: it decides which builder a
    package takes and supplies the manifest parameters that builder needs.
    `run_rule` calls whatever resolver it is given, so a caller — including a
    test — can substitute one without touching the mappings above.

    A package is matched by identifier first, then by the input kind its
    manifest declares. There is no third case: an envelope of the wrong shape
    is not a degraded audit but a confident answer about facts the policy never
    asked for, so the package is refused instead.

    Returns
    -------
    RuleEnvelope
        The envelope built by the package's own builder.

    Raises
    ------
    OperationalRuleError
        If no builder can be chosen for *rule_id*.
    """
    rule_dir = rule_package_dir(rule_id)
    builder = PACKAGE_ENVELOPE_BUILDERS.get(rule_id)
    declared: str | None = None
    if builder is None:
        declared = _declared_input_kind(rule_dir)
        builder = (
            INPUT_KIND_ENVELOPE_BUILDERS.get(declared) if declared is not None else None
        )
    if builder is None:
        raise OperationalRuleError(
            _unresolvable_package_message(rule_id, declared),
            operation="select-policy-envelope",
            resource=rule_id,
        )
    return builder(checkout, rule_parameters(rule_dir))
