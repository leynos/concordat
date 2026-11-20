"""Collect the dependency declarations of every `pyproject.toml`.

The root project and each nested one (hidden and vendored trees excepted) are
read, each against the `uv.lock` beside it and the Dependabot entries for its
own directory. The ecosystem is chosen from the lock's presence: `uv` when
there is one, otherwise `pip`, so the monitoring fact names what Dependabot
would actually need rather than a preference the repository does not express.
"""

from __future__ import annotations

import pathlib
import re
import tomllib
import typing as typ

from .dependency_family_coverage import coverage, mapping, normalise, sequence
from .dependency_family_discovery import nested_files
from .dependency_family_facts import (
    Declaration,
    Monitoring,
    classify,
    covered_ecosystems,
    no_resolution,
    read_lock,
    representative,
    strings,
    text,
)
from .dependency_family_resolution import Resolution, resolve_python
from .fs_probe import probe_file

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from .dependabot_envelope import ConfigFile

_REQUIREMENT_NAME: typ.Final = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*"
    r"(?:\[(?P<extras>[^\]]*)\])?\s*"
    r"(?P<spec>[^;]*?)\s*"
    r"(?:;(?P<marker>.*))?$"
)


def python_declarations(
    checkout: pathlib.Path,
    config: ConfigFile | None,
    problems: list[str],
    member_names: cabc.Collection[str],
) -> tuple[list[Declaration], list[Monitoring]]:
    """Return every dependency edge of every `pyproject.toml` in the checkout.

    The root project and each nested one (hidden and vendored trees excepted)
    are read, each against the `uv.lock` beside it and the Dependabot entries
    for its own directory.

    Returns
    -------
    tuple[list[Declaration], list[Monitoring]]
        One declaration per requirement, with its locked identity and
        coverage, and one monitoring fact per project.
    """
    manifests = ["pyproject.toml"] + [
        path.relative_to(checkout).as_posix()
        for path in nested_files(checkout, "pyproject.toml", problems)
    ]
    declarations: list[Declaration] = []
    monitoring: list[Monitoring] = []
    for manifest in manifests:
        parsed = _read_pyproject(checkout, manifest, problems)
        if parsed is None:
            continue
        found, fact = _project_declarations(
            (checkout, config), (manifest, parsed), member_names, problems
        )
        declarations += found
        monitoring.append(fact)
    return declarations, monitoring


def _python_requirements(
    parsed: dict[str, object],
) -> list[tuple[str, str | None, str]]:
    """Return the `(section, extra, requirement)` strings of a pyproject."""
    project = mapping(parsed.get("project")) or {}
    found = _named_rows("dependencies", None, project.get("dependencies"))
    extras = mapping(project.get("optional-dependencies")) or {}
    groups = mapping(parsed.get("dependency-groups")) or {}
    for section, tables in (
        ("optional-dependencies", extras),
        ("dependency-groups", groups),
    ):
        for name, items in tables.items():
            found.extend(_named_rows(section, str(name), items))
    return found


def _named_rows(
    section: str, name: str | None, items: object
) -> list[tuple[str, str | None, str]]:
    """Return the string requirements of one list as `(section, name, text)`."""
    return [
        (section, name, item) for item in sequence(items) or [] if isinstance(item, str)
    ]


def _read_pyproject(
    checkout: pathlib.Path, relative: str, problems: list[str]
) -> dict[str, object] | None:
    """Return the decoded `pyproject.toml` at *relative*, or None with the reason.

    Returns
    -------
    dict[str, object] | None
        The decoded document, or None when absent or unreadable.
    """
    path = checkout / relative
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"cannot read {relative}: {probe.read_error}")
        return None
    if not probe.present:
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
        problems.append(f"cannot parse {relative}: {error}")
        return None


def _project_declarations(
    context: tuple[pathlib.Path, ConfigFile | None],
    project: tuple[str, dict[str, object]],
    member_names: cabc.Collection[str],
    problems: list[str],
) -> tuple[list[Declaration], Monitoring]:
    """Return the dependency edges and monitoring fact of one `pyproject.toml`."""
    checkout, config = context
    manifest, parsed = project
    parent = pathlib.PurePosixPath(manifest).parent
    lock_name = (parent / "uv.lock").as_posix()
    has_lock = probe_file(checkout / lock_name).present
    ecosystem = "uv" if has_lock else "pip"
    valid, requirements = _requirement_rows(manifest, parsed, problems)
    resolutions = _resolutions(
        checkout, lock_name, requirements, member_names, has_lock=has_lock
    )
    directory = "/" if str(parent) == "." else f"/{parent.as_posix()}"
    declarations = [
        _python_declaration(
            (match, section, extra),
            (config, ecosystem, resolutions),
            (manifest, directory),
        )
        for match, section, extra in valid
    ]
    covered = covered_ecosystems(config, [directory])
    fact: Monitoring = {
        "manifest": manifest,
        "directory": directory,
        "native": ecosystem,
        "covered": covered,
        "duplicate": "pip" in covered and "uv" in covered,
    }
    return declarations, fact


def _requirement_rows(
    manifest: str, parsed: dict[str, object], problems: list[str]
) -> tuple[list[tuple[re.Match[str], str, str | None]], dict[str, str]]:
    """Return a project's matched requirements and each package's spec.

    A requirement string outside the supported grammar is reported as a
    problem, never dropped: an unreadable requirement must not shrink the
    declaration set the policy compares.

    Returns
    -------
    tuple[list[tuple[re.Match[str], str, str | None]], dict[str, str]]
        The matched `(match, section, extra)` rows and each package's spec.
    """
    rows = _python_requirements(parsed)
    matches = [
        (_REQUIREMENT_NAME.match(requirement), section, extra)
        for section, extra, requirement in rows
    ]
    problems.extend(
        f"cannot read the requirement {requirement!r} in {manifest}"
        for (match, _, _), (_, _, requirement) in zip(matches, rows, strict=True)
        if match is None
    )
    valid = [(match, section, extra) for match, section, extra in matches if match]
    requirements = {
        normalise(match.group("name")): match.group("spec") for match, _, _ in valid
    }
    return valid, requirements


def _resolutions(
    checkout: pathlib.Path,
    lock_name: str,
    requirements: dict[str, str],
    member_names: cabc.Collection[str],
    *,
    has_lock: bool,
) -> dict[str, Resolution]:
    """Return the locked identity behind each requirement of one project."""
    if not has_lock:
        return {name: no_resolution("no supported lock", None) for name in requirements}
    document, reason = read_lock(checkout / lock_name)
    if reason is not None:
        return {name: no_resolution(reason, None) for name in requirements}
    return resolve_python(document, lock_name, requirements, members=member_names)


def _python_declaration(
    requirement: tuple[re.Match[str], str, str | None],
    evidence: tuple[ConfigFile | None, str, dict[str, Resolution]],
    where: tuple[str, str],
) -> Declaration:
    """Return one `pyproject.toml` requirement as a declaration fact."""
    match, section, extra = requirement
    config, ecosystem, resolutions = evidence
    manifest, directory = where
    package = normalise(match.group("name"))
    spec = match.group("spec")
    kind, bounded = classify(spec)
    resolved = resolutions.get(package) or no_resolution(
        f"{package} was not resolved", None
    )
    extras = strings(match.group("extras").split(",") if match.group("extras") else [])
    marker = text(match.group("marker"))
    version = representative(spec, resolved)
    return {
        "manifest": manifest,
        "ecosystem": ecosystem,
        "section": section if extra is None else f"{section}:{extra}",
        "target": None,
        "optional": section == "optional-dependencies",
        "in_default": section == "dependencies",
        "default_features": True,
        "requested_features": [],
        "extras": extras,
        "markers": marker,
        "workspace_inherited": False,
        "declared_name": match.group("name"),
        "package": package,
        "requirement": spec,
        "requirement_kind": kind,
        "upper_bounded": bounded,
        "resolved": resolved,
        "coverage": coverage(
            config,
            ecosystem,
            [directory],
            package,
            version=version,
        ),
    }
