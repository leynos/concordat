"""Read the nextest setup scripts that run before a repository's timed tests.

A nextest setup script (`[scripts.setup.<name>]`) selected by a
`[[profile.<p>.scripts]]` rule runs before the tests the rule's `filter` covers,
so a build it starts is paid for outside any test's deadline. Whether a script
runs is a property of the rule, not of the script: the command lives in the
`[scripts.setup.<name>]` table and the `filter` in the profile rule that names
it. A rule naming a script nothing defines is a configuration nextest itself
refuses, so it prepares nothing.
"""

from __future__ import annotations

import dataclasses
import re
import tomllib
import typing as typ

from .dependency_family_coverage import mapping, sequence
from .fs_probe import probe_file
from .nested_cargo_gates import configured_strings

if typ.TYPE_CHECKING:
    import pathlib

NEXTEST_CONFIG: typ.Final = ".config/nextest.toml"
ALL_FILTER: typ.Final = "all()"


@dataclasses.dataclass(frozen=True, slots=True)
class SetupRule:
    """Store one nextest rule that runs a defined setup script.

    Attributes
    ----------
    filter:
        The rule's filterset expression; a missing one selects every test.
    """

    filter: str


def _defined_scripts(document: dict[str, object]) -> set[str]:
    """Return the names of the setup scripts a nextest configuration defines."""
    scripts = mapping(document.get("scripts")) or {}
    return set(mapping(scripts.get("setup")) or {})


def _profile_rules(document: dict[str, object]) -> list[dict[str, object]]:
    """Return every `[[profile.<name>.scripts]]` rule of every profile."""
    rules: list[dict[str, object]] = []
    for profile in (mapping(document.get("profile")) or {}).values():
        found = sequence((mapping(profile) or {}).get("scripts")) or []
        rules.extend(rule for item in found if (rule := mapping(item)) is not None)
    return rules


def _setup_names(rule: dict[str, object]) -> list[str]:
    """Return the script names a rule's `setup` key holds, one or a list."""
    names = rule.get("setup")
    return [names] if isinstance(names, str) else list(configured_strings(names))


def read_setup_rules(checkout: pathlib.Path, problems: list[str]) -> list[SetupRule]:
    """Return the nextest rules that run a setup script the file defines.

    Returns
    -------
    list[SetupRule]
        One entry per rule, empty when there is no readable configuration.
    """
    path = checkout / NEXTEST_CONFIG
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"{NEXTEST_CONFIG}: {probe.read_error}")
        return []
    if not probe.present:
        return []
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        problems.append(f"cannot read {NEXTEST_CONFIG}: {error}")
        return []
    defined = _defined_scripts(document)
    return [
        SetupRule(_filter_of(rule))
        for rule in _profile_rules(document)
        if (listed := _setup_names(rule)) and all(name in defined for name in listed)
    ]


def _filter_of(rule: dict[str, object]) -> str:
    """Return a rule's `filter`, which selects every test when it is absent."""
    selection = rule.get("filter")
    return selection if isinstance(selection, str) else ALL_FILTER


def _mentions(filterset: str, name: str) -> bool:
    """Report whether *name* occurs in *filterset* as a whole word."""
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", filterset) is not None


def covers(rules: list[SetupRule], names: list[str]) -> bool:
    """Report whether a setup rule selects every test or names one of *names*.

    Returns
    -------
    bool
        True when nextest runs a setup script before the named binary or crate.
    """
    return any(
        rule.filter.strip() == ALL_FILTER
        or any(_mentions(rule.filter, name) for name in names)
        for rule in rules
    )
