"""Decide whether a nextest profile's setup scripts fetch before every test.

Nextest configuration separates a rule from the script it names. A
`[[profile.<name>.scripts]]` entry says *when* a script runs, through its
`filter` and `platform` keys, and its `setup` key holds a script name or a list
of names. The command itself lives in a `[scripts.setup.<name>]` table under
`command`, which may be a Unix shell string, an argument list, or a table whose
`command-line` holds either.

Reading `setup` as though it were the command is the mistake this module exists
to avoid: a name never classifies as a fetch, so a correct configuration would
be reported as cold. A name nothing defines is a configuration nextest itself
refuses, which leaves the answer `uncertain` rather than `absent`.

Only a rule that applies to every test — `filter = "all()"` and no `platform`
restriction — proves the cache warm for the whole profile. A narrower rule
leaves the tests outside it with a cold cache, which is what `absent` means
here.
"""

from __future__ import annotations

import dataclasses
import shlex
import tomllib
import typing as typ

from .fs_probe import probe_file
from .makefile_expansion import UNRESOLVED_MARK
from .nested_cargo_scripts import script_commands

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

NEXTEST_CONFIG: typ.Final = ".config/nextest.toml"
DEFAULT_PROFILE: typ.Final = "default"
ALL_FILTER: typ.Final = "all()"

# The three states a policy acts on.
BEFORE: typ.Final = "before"
ABSENT: typ.Final = "absent"
UNCERTAIN: typ.Final = "uncertain"


@dataclasses.dataclass(frozen=True, slots=True)
class NextestProfile:
    """One nextest profile: whether its setup scripts fetch, and where it starts."""

    state: str
    line: int
    parent: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class NextestConfig:
    """The decoded `.config/nextest.toml`, or why it could not be decoded.

    ``is_present`` says a configuration file is there to read. A repository
    without one has no setup scripts and no profile rows; its nextest runs are
    still test entry points, but the configuration is not one of them.
    """

    profiles: dict[str, NextestProfile]
    is_readable: bool
    is_present: bool

    def profile(self, name: str) -> NextestProfile:
        """Return what *name* declares, defaulting to an absent prefetch.

        Returns
        -------
        NextestProfile
            The profile's state and the line its declaration starts on.
        """
        return self.profiles.get(name, NextestProfile(state=ABSENT, line=1))

    def state(self, name: str | None) -> str:
        """Return the prefetch state of the selected profile.

        No `--profile` flag selects nextest's own `default` profile, so a
        missing name is an ordinary selection rather than an unknown one. A
        name the expansion could not settle is the unknown case.

        Returns
        -------
        str
            One of `before`, `absent` or `uncertain`.
        """
        if not self.is_readable or (name is not None and UNRESOLVED_MARK in name):
            return UNCERTAIN
        return self._chain_state(name or DEFAULT_PROFILE)

    def _chain_state(self, name: str) -> str:
        """Return what every profile in *name*'s inheritance chain proves.

        Nextest resolves a profile through its `inherits` chain up to
        `default`, and merges each ancestor's setup scripts into the result.
        A fetch anywhere in the chain therefore warms the cache for the
        selected profile, while a profile whose parent cannot be resolved is
        a configuration nextest itself refuses.

        Returns
        -------
        str
            `before` when any profile in the chain fetches, `uncertain` when
            the chain is broken or cyclic, and `absent` otherwise.
        """
        state = ABSENT
        seen: set[str] = set()
        current: str | None = name
        while current is not None:
            if current in seen:
                # Nextest refuses a cycle, so nothing in it can be shown to run.
                return UNCERTAIN
            seen.add(current)
            found = self.profiles.get(current)
            if found is None:
                # `default` is nextest's own built-in profile and exists
                # whether or not the file declares it. Any other missing name
                # is a parent nextest cannot resolve.
                return state if current == DEFAULT_PROFILE else UNCERTAIN
            if found.state == UNCERTAIN:
                return UNCERTAIN
            if found.state == BEFORE:
                state = BEFORE
            current = found.parent
        return state


def _mapping(value: object) -> dict[str, object] | None:
    """Return *value* as a string-keyed mapping, or ``None`` when it is not one.

    Returns
    -------
    dict[str, object] | None
        *value* when it is a mapping, otherwise ``None``.
    """
    return typ.cast("dict[str, object]", value) if isinstance(value, dict) else None


def _table(value: object) -> dict[str, object]:
    """Return *value* as a string-keyed mapping, or an empty one."""
    found = _mapping(value)
    return {} if found is None else found


def _setup_names(setup: object) -> list[str] | None:
    """Return the setup script names one rule lists, or ``None`` if unreadable.

    Nextest accepts a single name or an array of names; anything else is a
    configuration nextest itself would refuse, so the caller cannot tell what
    runs.

    Returns
    -------
    list[str] | None
        The names, or ``None`` when `setup` is not a name or a list of names.
    """
    match setup:
        case str():
            return [setup]
        case list() if all(isinstance(item, str) for item in setup):
            return typ.cast("list[str]", setup)
    return None


def _setup_command(definition: object) -> str | None:
    """Return the command line a `[scripts.setup.<name>]` table declares.

    Nextest spells a command three ways: a Unix shell string, an argument
    list, and a table whose `command-line` holds either of those. All three
    are rendered as shell text so one classifier can read them, with an
    argument list quoted exactly as a shell would need it.

    A `relative-to` rewrite prefixes the command word with a directory nextest
    chooses, so the argv is no longer the line written here: the answer is
    `None`, which the caller reports as uncertain rather than misclassifying.

    Returns
    -------
    str | None
        The command, or ``None`` when the declaration has an unreadable shape.
    """
    match definition:
        case str():
            return definition
    table = _mapping(definition)
    return None if table is None else _table_command(table)


def _table_command(table: dict[str, object]) -> str | None:
    """Return the command a setup definition table's `command` key declares.

    Returns
    -------
    str | None
        The command, or ``None`` when the declaration has an unreadable shape.
    """
    match table.get("command"):
        case str() as command:
            return command
        case list() as command if all(isinstance(item, str) for item in command):
            return shlex.join(typ.cast("list[str]", command))
        case dict() as command:
            if str(command.get("relative-to", "none")).strip() != "none":
                return None
            return _setup_command(command.get("command-line"))
    return None


def _setup_state(setup: object, defined: cabc.Mapping[str, object]) -> str:
    """Return what one rule's `setup` names prove about the cache.

    A name nothing defines is a configuration nextest refuses, so the reader
    cannot say what would run: that is `uncertain`, not a proven-cold cache.

    Nextest splits the `command` string into words and executes them directly,
    with no shell in between. `cargo fetch --locked && cargo test` therefore
    runs `cargo` with `fetch`, `--locked`, `&&`, `cargo`, `test` as arguments:
    a fetch of nothing useful, not a fetch followed by a test. Reading the
    string as shell text would credit a prefetch that never happens.

    Returns
    -------
    str
        `before` when a script fetches, `uncertain` when the rule or a
        definition cannot be read or names more than one command, and
        `absent` when every named script runs one command that does not fetch.
    """
    names = _setup_names(setup)
    if names is None:
        return UNCERTAIN
    state = ABSENT
    for name in names:
        command = _setup_command(defined.get(name))
        if command is None:
            state = UNCERTAIN
            continue
        found = _setup_command_state(command)
        if found == BEFORE:
            return BEFORE
        if found == UNCERTAIN:
            state = UNCERTAIN
    return state


def _setup_command_state(command: str) -> str:
    """Return what one setup command proves about the cache.

    Returns
    -------
    str
        `before` for a single fetching command, `uncertain` for anything the
        reader cannot reduce to one readable command, and `absent` otherwise.
    """
    # Split into argv and executed directly, with no shell between: a `#` here
    # is an ordinary argument, not the start of a comment.
    commands = script_commands(command, comments=False)
    if len(commands) != 1:
        return UNCERTAIN
    only = commands[0].meaning
    if only.is_prefetch:
        return BEFORE
    return UNCERTAIN if only.is_unreadable else ABSENT


def _covers_every_test(entry: cabc.Mapping[str, object]) -> bool:
    """Report whether a profile script rule applies to every test.

    Only `all()` covers every test in the profile, and only an unscoped rule
    covers every platform; a narrower filter or a `platform` restriction
    leaves the tests outside it with a cold cache.

    Returns
    -------
    bool
        True when the rule is neither filtered down nor platform-scoped.
    """
    if entry.get("platform") is not None:
        return False
    return str(entry.get("filter", "")).strip() == ALL_FILTER


def _profile_state(
    body: cabc.Mapping[str, object], defined: cabc.Mapping[str, object]
) -> str:
    """Return whether a profile's setup scripts fetch before every test."""
    scripts = body.get("scripts")
    if not isinstance(scripts, list):
        return ABSENT
    state = ABSENT
    for entry in scripts:
        rule = _mapping(entry)
        if rule is None or not _covers_every_test(rule):
            continue
        covered = _setup_state(rule.get("setup"), defined)
        if covered == BEFORE:
            return BEFORE
        if covered == UNCERTAIN:
            state = UNCERTAIN
    return state


def _setup_definitions(parsed: cabc.Mapping[str, object]) -> dict[str, object]:
    """Return every setup script definition, by name.

    Nextest reads `[scripts.setup.<name>]`, and still honours the deprecated
    `[script.<name>]` table it replaced. A name defined in both is read from
    the current table, which is the one nextest prefers.

    Returns
    -------
    dict[str, object]
        The definition tables, keyed by script name.
    """
    defined = dict(_table(parsed.get("script")))
    scripts = _table(parsed.get("scripts"))
    defined.update(_table(scripts.get("setup")))
    return defined


def _script_line(text: str, profile: str) -> int:
    """Return the 1-based line a profile's setup scripts are declared on.

    The `scripts` array table is where a missing setup script would be added,
    so that line is the one a reviewer needs. A profile that declares none
    still appears as `[profile.<name>]`, and that heading is the next best
    place to look; a profile named nowhere points at the file itself.

    Returns
    -------
    int
        The line number, or 1 when the profile is named nowhere.
    """
    headings = (f"[[profile.{profile}.scripts]]", f"[profile.{profile}]")
    for heading in headings:
        for number, line in enumerate(text.splitlines(), start=1):
            if line.strip() == heading:
                return number
    return 1


def _nextest_config(
    profiles: dict[str, NextestProfile], *, is_readable: bool, is_present: bool
) -> NextestConfig:
    """Return a nextest configuration with every flag spelled out."""
    return NextestConfig(profiles, is_readable=is_readable, is_present=is_present)


def _decode_profiles(text: str) -> dict[str, NextestProfile]:
    """Return each profile the configuration declares, with its state.

    Returns
    -------
    dict[str, NextestProfile]
        The profiles by name, or none when the file declares no table this
        reader can use.
    """
    parsed: object = tomllib.loads(text)
    document = _mapping(parsed)
    if document is None:
        return {}
    profiles_table = _mapping(document.get("profile"))
    if profiles_table is None:
        return {}
    defined = _setup_definitions(document)
    profiles: dict[str, NextestProfile] = {}
    for key, body in profiles_table.items():
        name = str(key)
        table = _mapping(body)
        profiles[name] = NextestProfile(
            state=UNCERTAIN if table is None else _profile_state(table, defined),
            line=_script_line(text, name),
            parent=_profile_parent(name, table),
        )
    return profiles


def _profile_parent(name: str, table: dict[str, object] | None) -> str | None:
    """Return the profile *name* inherits, as nextest would resolve it.

    A custom profile that names no `inherits` still inherits `default`, so an
    absent key is different from a key naming `default`. A profile whose
    parent is not a literal name is a configuration nextest refuses.

    Returns
    -------
    str | None
        The parent's name, or ``None`` when the chain ends here.
    """
    if name == DEFAULT_PROFILE:
        return None
    if table is None:
        return DEFAULT_PROFILE
    match table.get("inherits"):
        case None:
            return DEFAULT_PROFILE
        case str() as parent:
            return parent
    return UNRESOLVED_MARK


def read_nextest(checkout: pathlib.Path, problems: list[str]) -> NextestConfig:
    """Decode `.config/nextest.toml`, recording a problem rather than guessing.

    Parameters
    ----------
    checkout:
        The repository root to read.
    problems:
        The fact list; every unreadable or undecodable file appends one
        sentence.

    Returns
    -------
    NextestConfig
        Each profile's setup scripts, and whether the file was there and could
        be read.
    """
    path = checkout / NEXTEST_CONFIG
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"{NEXTEST_CONFIG}: {probe.read_error}")
        return _nextest_config({}, is_readable=False, is_present=True)
    if not probe.present:
        return _nextest_config({}, is_readable=True, is_present=False)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        problems.append(f"cannot read {NEXTEST_CONFIG}: {error}")
        return _nextest_config({}, is_readable=False, is_present=True)
    try:
        profiles = _decode_profiles(text)
    except tomllib.TOMLDecodeError as error:
        problems.append(f"cannot decode {NEXTEST_CONFIG}: {error}")
        return _nextest_config({}, is_readable=False, is_present=True)
    return _nextest_config(profiles, is_readable=True, is_present=True)
