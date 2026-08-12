"""Decoding and validation of the Parabellum estate manifest.

The manifest is operator-supplied YAML, so every shape it can take is
checked here, at the boundary, before any name reaches a URL segment or a
clone directory.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from ruamel.yaml import YAML

from concordat.errors import OperationalRuleError

if typ.TYPE_CHECKING:
    import pathlib


@dataclasses.dataclass(frozen=True, slots=True)
class EstateEntry:
    """One repository in the campaign inventory.

    Attributes
    ----------
    name:
        The repository's name.
    excluded:
        The operator-supplied reason this repository is skipped, or
        ``None`` when it is to be audited.

    """

    name: str
    excluded: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Estate:
    """The parsed campaign inventory.

    Attributes
    ----------
    owner:
        The GitHub owner that every repository in the estate belongs to.
    repositories:
        The repository entries declared in the manifest.

    """

    owner: str
    repositories: tuple[EstateEntry, ...]


# GitHub owners are alphanumerics and hyphens, no leading or trailing hyphen.
# Repository names additionally allow dot and underscore, but must remain a
# single path component that is safe to use as a clone directory: `.` and `..`
# are excluded by requiring at least one character that is not a dot.
_OWNER_PATTERN: typ.Final = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"
)
_REPO_NAME_PATTERN: typ.Final = re.compile(r"^(?=.*[^.])[A-Za-z0-9._-]{1,100}$")


def _validated_identifier(
    value: object,
    pattern: re.Pattern[str],
    kind: str,
    resource: pathlib.Path | str,
) -> str:
    """Return *value* when it matches *pattern*, else reject the manifest.

    Names from the manifest become URL segments and clone-directory
    components, so they are validated here, at the boundary, rather than
    trusted downstream.

    Returns
    -------
    str
        The validated identifier.

    Raises
    ------
    OperationalRuleError
        If *value* does not match *pattern*.
    """
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    message = f"estate manifest declares an invalid {kind}: {value!r}"
    raise OperationalRuleError(
        message,
        operation="load-estate-manifest",
        resource=resource,
    )


def _manifest_error(path: pathlib.Path, detail: str) -> OperationalRuleError:
    """Return the rejection for a manifest whose shape cannot be decoded."""
    message = f"estate manifest {path} {detail}"
    return OperationalRuleError(
        message,
        operation="load-estate-manifest",
        resource=path,
    )


def _repository_entry(item: object, path: pathlib.Path) -> EstateEntry:
    """Decode one repository entry from an estate manifest."""
    if not isinstance(item, dict):
        raise _manifest_error(path, f"has a non-mapping repository entry: {item!r}")
    entry = typ.cast("dict[str, object]", item)
    if "name" not in entry:
        raise _manifest_error(path, "has a repository entry missing key 'name'")
    excluded = entry.get("excluded")
    # An exclusion reason is prose the ledger records verbatim. Dropping a
    # non-string would silently un-exclude the repository and audit it, so
    # the manifest is refused instead.
    if excluded is not None and not isinstance(excluded, str):
        raise _manifest_error(
            path,
            f"has a non-string exclusion reason: {excluded!r}",
        )
    return EstateEntry(
        name=_validated_identifier(
            entry["name"], _REPO_NAME_PATTERN, "repository name", path
        ),
        excluded=excluded,
    )


def _repository_entries(
    repositories: object,
    path: pathlib.Path,
) -> tuple[EstateEntry, ...]:
    """Decode the repository collection from an estate manifest.

    Only a list is a repository collection. A scalar raises on iteration and a
    bare string is walked character by character, so both are refused here
    rather than reaching the entry decoding as `TypeError`.

    Returns
    -------
    tuple[EstateEntry, ...]
        The decoded repository entries.

    Raises
    ------
    _manifest_error
        If *repositories* is not a list.
    """
    if not isinstance(repositories, list):
        raise _manifest_error(
            path,
            f"has a `repositories` value that is not a list: {repositories!r}",
        )
    return tuple(_repository_entry(item, path) for item in repositories)


def load_estate(path: pathlib.Path) -> Estate:
    """Parse the estate inventory YAML document.

    Every shape the document can take is checked before it is indexed. The
    file is operator-supplied, so a malformed one must surface as an
    operational error naming the manifest, not as a `TypeError` from a
    subscript.

    Name validation uses ``_validated_identifier`` and reports its own
    ``OperationalRuleError`` when an owner or repository name is invalid.

    Parameters
    ----------
    path:
        Path to the estate manifest YAML document.

    Returns
    -------
    Estate
        The parsed owner and repository entries.

    Raises
    ------
    _manifest_error
        With ``operation="load-estate-manifest"``, when the document is
        not a mapping, a required key (``repositories`` or ``owner``) is
        missing, the ``repositories`` value is not a list, a repository
        entry is malformed.

    """
    document = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise _manifest_error(
            path,
            f"is not a mapping: {type(document).__name__}",
        )
    for key in ("repositories", "owner"):
        if key not in document:
            raise _manifest_error(path, f"is missing key {key!r}")
    entries = _repository_entries(document["repositories"], path)
    owner = _validated_identifier(document["owner"], _OWNER_PATTERN, "owner", path)
    return Estate(owner=owner, repositories=entries)
