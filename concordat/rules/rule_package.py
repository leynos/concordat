"""Locate a canon lint-rule package and read the manifest it declares.

A rule package is a directory under the canon lint-rule tree holding a
`policy/` and a `rule.yaml`. This module answers where that directory is, and
what its manifest declares; choosing the policy-input envelope the package is
audited over belongs to `packages`.

The package root is resolved lazily rather than at import, so a missing rule
tree fails when a rule runs rather than when the CLI is imported.
"""

from __future__ import annotations

import functools
import importlib.resources
import pathlib
import re
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .fs_probe import probe_file

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
