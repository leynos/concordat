"""Build the Dependabot configuration facts for the update-shape rule.

The policy judges `.github/dependabot.yml` against the estate's update shape,
and one clause depends on the checkout as well as the configuration: local
actions under `.github/actions` are updated only when a `github-actions`
entry lists their directories. The envelope therefore carries the decoded
configuration and the directories that hold an action manifest.

A configuration that cannot be decoded stays a fact with its reason, so the
policy returns an indeterminate verdict rather than treating the repository
as one without Dependabot. A checkout that cannot be read at all raises an
operational error.
"""

from __future__ import annotations

import json
import pathlib
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .fs_probe import probe_dir, probe_file, probe_symlink

if typ.TYPE_CHECKING:
    from .codescene_coverage_envelope import Repository

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/dependabot-update-shape"
GITHUB_DIRECTORY: typ.Final = pathlib.PurePosixPath(".github")
CONFIG_NAMES: typ.Final = ("dependabot.yml", "dependabot.yaml")
ACTIONS_DIRECTORY: typ.Final = GITHUB_DIRECTORY / "actions"
ACTION_MANIFESTS: typ.Final = frozenset({"action.yml", "action.yaml"})
OPERATION_READ_DEPENDABOT: typ.Final = "read-dependabot-config"
OPERATION_READ_ACTIONS: typ.Final = "read-local-actions"

_yaml = YAML(typ="safe")


class ConfigFile(typ.TypedDict):
    """Store the decoded configuration or the reason it could not be decoded.

    Attributes
    ----------
    path:
        The configuration's path relative to the checkout, in POSIX form.
    parsed:
        The decoded YAML document, or None when it could not be decoded.
    error:
        Why the document could not be decoded, or None when it was.
    group_order:
        For each `updates` entry, its group names in document order, or None
        where the entry has no `groups` mapping. Dependabot assigns a
        dependency to the first group that matches it, so order is part of
        the configuration's meaning, and a Rego object does not keep it.
    """

    path: str
    parsed: object | None
    error: str | None
    group_order: list[list[str] | None]


class DependabotEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the Dependabot update-shape rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks so another
        package's facts cannot be evaluated by this one.
    repository:
        The audited checkout's identity.
    config:
        The Dependabot configuration, or None when the repository has none.
    action_directories:
        Every directory under `.github/actions` holding an action manifest,
        as a Dependabot directory (`/`-rooted, POSIX), sorted.
    """

    schema_version: int
    kind: str
    repository: Repository
    config: ConfigFile | None
    action_directories: list[str]


def _config_error(relative: pathlib.PurePosixPath, error: str) -> ConfigFile:
    """Return a configuration fact that carries only its failure."""
    return {"path": str(relative), "parsed": None, "error": error, "group_order": []}


def _group_order(parsed: object) -> list[list[str] | None]:
    """Return each `updates` entry's group names in document order.

    Returns
    -------
    list[list[str] | None]
        One item per entry; None where the entry has no `groups` mapping.
    """
    updates = parsed.get("updates") if isinstance(parsed, dict) else None
    if not isinstance(updates, list):
        return []
    return [
        [str(name) for name in groups]
        if isinstance(entry, dict) and isinstance(groups := entry.get("groups"), dict)
        else None
        for entry in updates
    ]


def _decode_config(relative: pathlib.PurePosixPath, text: str) -> ConfigFile:
    """Return the decoded configuration, or the content error that stopped it.

    The document is round-tripped through JSON with non-finite numbers
    refused, because Conftest reads JSON: a value that cannot be encoded is
    this file's content error, not an operational failure of the whole run.

    Returns
    -------
    ConfigFile
        The decoded document, or the reason it could not be decoded.
    """
    try:
        parsed: object = _yaml.load(text)
    except YAMLError as error:
        return _config_error(relative, f"invalid YAML: {error}")
    if not isinstance(parsed, dict):
        return _config_error(relative, "configuration is not a mapping")
    try:
        encoded = json.dumps(parsed, default=str, allow_nan=False)
    except (TypeError, ValueError) as error:
        return _config_error(relative, f"configuration is not JSON-safe: {error}")
    return {
        "path": str(relative),
        "parsed": json.loads(encoded),
        "error": None,
        "group_order": _group_order(parsed),
    }


def _load_config(checkout: pathlib.Path, name: str) -> ConfigFile | None:
    """Return one candidate configuration fact, or None when it is absent.

    Returns
    -------
    ConfigFile | None
        The decoded configuration or its failure, or None when no file of
        that name exists.

    Raises
    ------
    OperationalRuleError
        If the file exists but reading it fails other than by encoding.
    """
    relative = GITHUB_DIRECTORY / name
    path = checkout / relative
    if probe_symlink(path).present:
        return _config_error(relative, "configuration file is a symlink")
    probe = probe_file(path)
    if probe.read_error is not None:
        return _config_error(relative, f"cannot read: {probe.read_error}")
    if not probe.present:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return _config_error(relative, f"not UTF-8 text: {error}")
    except OSError as error:
        message = f"cannot read {path}: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION_READ_DEPENDABOT, resource=path
        ) from error
    return _decode_config(relative, text)


def _find_config(checkout: pathlib.Path) -> ConfigFile | None:
    """Return the repository's Dependabot configuration fact, if it has one.

    GitHub reads one configuration file. A repository carrying both
    spellings is reported rather than resolved by picking one, since the
    audit cannot know which the service honours.

    Returns
    -------
    ConfigFile | None
        The configuration fact, or None when neither spelling exists.
    """
    found = [fact for name in CONFIG_NAMES if (fact := _load_config(checkout, name))]
    if len(found) > 1:
        both = " and ".join(fact["path"] for fact in found)
        return _config_error(
            GITHUB_DIRECTORY / CONFIG_NAMES[0], f"both {both} are present"
        )
    return found[0] if found else None


def _contained_actions_directory(checkout: pathlib.Path) -> pathlib.Path:
    """Return the checkout's local-actions directory, refusing one outside it.

    Returns
    -------
    pathlib.Path
        The local-actions directory, unresolved.

    Raises
    ------
    OperationalRuleError
        If the directory cannot be resolved or resolves outside the checkout.
    """
    directory = checkout / ACTIONS_DIRECTORY
    try:
        root = checkout.resolve(strict=False)
        resolved = directory.resolve(strict=False)
    except OSError as error:
        message = f"cannot resolve {directory}: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION_READ_ACTIONS, resource=directory
        ) from error
    if not resolved.is_relative_to(root):
        message = f"{directory} resolves outside the checkout, to {resolved}"
        raise OperationalRuleError(
            message, operation=OPERATION_READ_ACTIONS, resource=directory
        )
    return directory


def _raise_walk_error(error: OSError) -> typ.NoReturn:
    """Turn a directory-walk failure into an operational error."""
    message = f"cannot list {error.filename}: {error}"
    raise OperationalRuleError(
        message, operation=OPERATION_READ_ACTIONS, resource=str(error.filename)
    ) from error


def _action_directories(checkout: pathlib.Path) -> list[str]:
    """Return every local action directory as a `/`-rooted Dependabot path.

    Symbolic links are not followed: a linked directory is not this
    repository's action, and following one could leave the checkout.

    Returns
    -------
    list[str]
        The directories holding `action.yml` or `action.yaml`, sorted.

    Raises
    ------
    OperationalRuleError
        If the directory exists but cannot be read.
    """
    directory = _contained_actions_directory(checkout)
    probe = probe_dir(directory)
    if probe.read_error is not None:
        message = f"cannot read {directory}: {probe.read_error}"
        raise OperationalRuleError(
            message, operation=OPERATION_READ_ACTIONS, resource=directory
        )
    if not probe.present:
        return []
    return sorted(
        f"/{current.relative_to(checkout).as_posix()}"
        for current, _, files in directory.walk(on_error=_raise_walk_error)
        if ACTION_MANIFESTS.intersection(files)
    )


def build_dependabot_envelope(checkout: pathlib.Path) -> DependabotEnvelope:
    """Assemble the Dependabot configuration and local-action evidence.

    An `OperationalRuleError` reaches the caller when the checkout cannot be
    read; an absent configuration or an absent `.github/actions` is not a
    failure.

    Returns
    -------
    DependabotEnvelope
        The decoded configuration, if any, and the local action directories.
    """
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "config": _find_config(checkout),
        "action_directories": _action_directories(checkout),
    }
