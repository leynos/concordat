"""Read and validate a rule package's `rule.yaml`.

The runner's concern is evaluating a policy; a package's manifest is a
separate one. It declares the parameter defaults the policy reads from
`data.parameters` and, under `sensor.input`, the policy-input document the
package is evaluated over. Both are decoded YAML, so both are untrusted
shapes that must be narrowed before they are read, and every way of failing
to read them has to be told apart from the package simply not declaring
anything.
"""

from __future__ import annotations

import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from . import fs_probe

if typ.TYPE_CHECKING:
    import pathlib

MANIFEST_FILENAME: typ.Final = "rule.yaml"
OPERATION: typ.Final = "load-rule-manifest"

_yaml = YAML(typ="safe")


def _fail(path: pathlib.Path, message: str) -> typ.NoReturn:
    """Raise the operational error for a manifest that cannot be used.

    Raises
    ------
    OperationalRuleError
        Always.
    """
    raise OperationalRuleError(message, operation=OPERATION, resource=path)


def _manifest_text(path: pathlib.Path) -> str | None:
    """Return the manifest's text, or ``None`` when the package has none.

    Absence and inaccessibility are different answers. `Path.is_file` gives
    the same one to both, and suppresses the error outright from Python 3.14,
    so a package whose manifest cannot be read would silently lose its
    declared parameters and its policy input.

    Returns
    -------
    str | None
        The manifest's text, or ``None`` when no manifest is present.

    Raises
    ------
    OperationalRuleError
        If the manifest exists but cannot be read.
    """
    probe = fs_probe.probe_file(path)
    if probe.read_error is not None:
        _fail(path, f"cannot read rule manifest {path}: {probe.read_error}")
    if not probe.present:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        message = f"cannot read rule manifest {path}: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION, resource=path
        ) from error


def _mapping(value: object) -> dict[str, object]:
    """Return *value* as a string-keyed mapping of unnarrowed values.

    Returns
    -------
    dict[str, object]
        The mapping, with its keys rendered as strings.
    """
    items = typ.cast("dict[object, object]", value).items()
    return {str(key): item for key, item in items}


def load(rule_dir: pathlib.Path) -> dict[str, object]:
    """Return the package's manifest mapping, or ``{}`` when it has none.

    The decoded document is typed as `dict[str, object]` rather than carrying
    `typ.Any` outward: `.rules/python-00.md` asks that decoded data be
    narrowed at its boundary, and every reader below tests the shape it needs.

    Returns
    -------
    dict[str, object]
        The decoded manifest, keyed by its top-level field names.

    Raises
    ------
    OperationalRuleError
        If the manifest cannot be read, decoded, or is not a mapping.
    """
    path = rule_dir / MANIFEST_FILENAME
    text = _manifest_text(path)
    if text is None:
        return {}
    try:
        decoded = _yaml.load(text)
    except YAMLError as error:
        message = f"cannot read rule manifest {path}: {error}"
        raise OperationalRuleError(
            message, operation=OPERATION, resource=path
        ) from error
    if not isinstance(decoded, dict):
        _fail(path, f"rule manifest {path} is not a mapping")
    return _mapping(decoded)


def parameter_defaults(rule_dir: pathlib.Path) -> dict[str, object]:
    """Return the parameter defaults the manifest declares.

    The policies read their tunables from ``data.parameters``; without this
    the manifest's declared defaults would be inert and only the ``default``
    rules baked into the Rego would ever apply.

    An `OperationalRuleError` propagates from the manifest read if the
    manifest cannot be read or is malformed.

    Returns
    -------
    dict[str, object]
        The declared defaults, or an empty mapping when none are declared.
    """
    parameters = load(rule_dir).get("parameters")
    if not isinstance(parameters, dict):
        return {}
    defaults = typ.cast("dict[object, object]", parameters).get("defaults")
    if not isinstance(defaults, dict):
        return {}
    return _mapping(defaults)


def declared_input(rule_dir: pathlib.Path, default: str) -> str:
    """Return the policy-input kind the manifest declares under `sensor.input`.

    A manifest without a `sensor` key predates the field and takes *default*,
    which is the documented legacy case. A `sensor` that is present and is
    not a mapping is refused rather than falling back: falling back would
    hand one policy the document another was written for, and a policy that
    cannot find its own facts reports compliance it never established.

    An `OperationalRuleError` propagates when `sensor` is present and is not
    a mapping, or declares an input that is not a string.

    Returns
    -------
    str
        The declared kind, or *default* for a manifest predating the field.
    """
    manifest = load(rule_dir)
    if "sensor" not in manifest:
        return default
    sensor = manifest["sensor"]
    if not isinstance(sensor, dict):
        path = rule_dir / MANIFEST_FILENAME
        _fail(
            path,
            f"rule manifest {path} declares `sensor` as "
            f"{type(sensor).__name__}; expected a mapping",
        )
    declared = typ.cast("dict[object, object]", sensor).get("input", default)
    if isinstance(declared, str):
        return declared
    path = rule_dir / MANIFEST_FILENAME
    _fail(path, f"rule manifest {path} declares the policy input {declared!r}")
