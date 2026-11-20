"""Build the facts for the `nested-cargo-nextest` rule (NC-004, NC-005).

Tests that start `cargo` or `rustc` do compiler work inside a timed test body.
Left to nextest's defaults a cold build looks like a hang and consumes the
test's whole allowance: netsuke hit 900 s and rstest-bdd had about 120 s of
slack in 75 minutes. The remedy is configuration, and the envelope records what
the repository's `.config/nextest.toml` provides:

- a serialized test group (`max-threads = 1`) that an override places the
  spawning tests in, so their builds do not compete with each other;
- an explicit `slow-timeout`, in a profile or an override, so a stuck build is
  terminated rather than merely reported;
- a `global-timeout`, so a hung run is bounded by the tool and not only by the
  job.

It also records, per top-level `tests/<stem>.rs` spawner, whether a grouped
override's filter names the binary or one of its tests; the other
spawners (helpers under `src/` or `tests/support/`) are judged at repository
level only. A predicate the filter negates (`not(binary(ui))`, `!test(ui)`)
excludes that name rather than selecting it, so it does not count as naming
the spawner; a group of predicates inside one negation
(`!(binary(ui) | binary(other))`) excludes every name it holds. Parentheses
are balanced rather than matched lazily, and a matcher's `/regex/` text is
skipped while they are counted, because a regex holds brackets of its own
(`!(test(/foo[)]/) | binary(ui))`). A `/` opens a regex only at the first
non-space character of a matcher's argument, so a slash in a `~contains`
argument (`test(~src/foo)`) is data and does not hide the group's brackets or
the names beside it. The scan for
spawners lives in `nested_cargo_spawn` and is conservative by design. A
configuration that cannot be decoded stays a fact, so the policy answers
indeterminate.
"""

from __future__ import annotations

import re
import typing as typ

from .dependency_family_coverage import mapping, sequence
from .fs_probe import probe_file
from .nested_cargo import origin_slug, read_toml
from .nested_cargo_spawn import Spawner, find_spawners

if typ.TYPE_CHECKING:
    import pathlib

    from .codescene_coverage_envelope import Repository
    from .nested_cargo import Manifest

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/nested-cargo-nextest"
CONFIG: typ.Final = ".config/nextest.toml"


class Nextest(typ.TypedDict):
    """Store what the nextest configuration provides.

    Attributes
    ----------
    state:
        `present`, `absent` or `unreadable`.
    error:
        Why the configuration could not be read, or None.
    serial_groups:
        Names of test groups with `max-threads = 1`.
    grouped_override:
        Whether some override places tests in a serial group.
    slow_timeout:
        Whether a profile or an override sets `slow-timeout`.
    global_timeout:
        Whether a profile sets `global-timeout`.
    """

    state: str
    error: str | None
    serial_groups: list[str]
    grouped_override: bool
    slow_timeout: bool
    global_timeout: bool


class SpawnerFact(Spawner):
    """Store a spawner and whether a grouped filter names its binary or tests.

    Attributes
    ----------
    named:
        True when the file is not a top-level test binary (judged at repository
        level), or a grouped override's filter names the binary or a test fn.
    """

    named: bool


class NestedCargoNextestEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the nested-cargo nextest rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug.
    nextest:
        What the nextest configuration provides.
    spawners:
        Every Rust test source that spawns a compiler.
    problems:
        Every fact the builder could not establish, as sentences.
    """

    schema_version: int
    kind: str
    repository: Repository
    nextest: Nextest
    spawners: list[SpawnerFact]
    problems: list[str]


def _profiles(document: Manifest) -> list[dict[str, object]]:
    """Return the nextest profile tables."""
    table = mapping(document.get("profile")) or {}
    return [profile for value in table.values() if (profile := mapping(value))]


def _overrides(profile: dict[str, object]) -> list[dict[str, object]]:
    """Return one profile's override tables."""
    return [
        item
        for raw in sequence(profile.get("overrides")) or []
        if (item := mapping(raw))
    ]


def _serial_groups(document: Manifest) -> list[str]:
    """Return the names of test groups limited to one thread."""
    groups = mapping(document.get("test-groups")) or {}
    return sorted(
        str(name)
        for name, value in groups.items()
        if (mapping(value) or {}).get("max-threads") == 1
    )


def _facts(document: Manifest) -> tuple[Nextest, list[str]]:
    """Return what a decoded configuration provides, and its grouped filters."""
    serial = _serial_groups(document)
    profiles = _profiles(document)
    grouped = [
        override
        for profile in profiles
        for override in _overrides(profile)
        if override.get("test-group") in serial
    ]
    slow = any(
        "slow-timeout" in profile
        or any("slow-timeout" in override for override in _overrides(profile))
        for profile in profiles
    )
    facts: Nextest = {
        "state": "present",
        "error": None,
        "serial_groups": serial,
        "grouped_override": bool(grouped),
        "slow_timeout": slow,
        "global_timeout": any("global-timeout" in profile for profile in profiles),
    }
    return facts, [str(override.get("filter", "")) for override in grouped]


def _nextest(checkout: pathlib.Path) -> tuple[Nextest, list[str]]:
    """Return the configuration fact and the filters of its grouped overrides."""
    absent: Nextest = {
        "state": "absent",
        "error": None,
        "serial_groups": [],
        "grouped_override": False,
        "slow_timeout": False,
        "global_timeout": False,
    }
    path = checkout / CONFIG
    probe = probe_file(path)
    if probe.read_error is not None:
        return {**absent, "state": "unreadable", "error": probe.read_error}, []
    if not probe.present:
        return absent, []
    document, error = read_toml(path)
    if error is not None:
        return {**absent, "state": "unreadable", "error": error}, []
    return _facts(document)


# The predicates whose argument can name a binary or a test function. Another
# predicate's argument names something else (a package, a platform), so it is
# not read for the spawner's name however it is written.
NAMING_PREDICATES: typ.Final = frozenset({"binary", "binary_id", "test"})
# The filterset's operators as one character each, and as words. `-` is set
# difference, which selects its left operand and excludes its right one.
_OPERATOR_CHARS: typ.Final = {
    "!": "not",
    "-": "diff",
    "&": "and",
    "|": "or",
    "+": "or",
    "(": "open",
    ")": "close",
}
_OPERATOR_WORDS: typ.Final = {"and": "and", "or": "or", "not": "not"}
_WORD: typ.Final = re.compile(r"\w+")


class _FilterToken(typ.NamedTuple):
    """One structural token of a filterset expression.

    Attributes
    ----------
    kind:
        `predicate`, `not`, `and`, `or`, `diff`, `open`, `close` or `unknown`.
    name:
        The predicate's name, or an empty string for any other token.
    argument:
        The text between a predicate's parentheses, or an empty string.
    """

    kind: str
    name: str
    argument: str


def _balanced_close(text: str, open_at: int) -> int:
    r"""Return the offset of the `)` that balances the `(` at *open_at*.

    A filter argument is either bare or a `/regex/` matcher, and both hold
    parentheses and brackets of their own (`test(/foo[)]/)`, `test(=other\))`).
    Those are not delimiters of the predicate or the group that holds them, so
    an escape pair is skipped wherever it is written and the matcher's text is
    skipped whole, with `\\/` read as an escaped slash rather than as its end.
    A `/` opens a matcher only where a matcher begins, at the first non-space
    character after a `(`; elsewhere it is data, as in the contains matchers
    `test(~src/foo)` and `test(~bar/baz)`, whose slashes would otherwise pair
    and swallow the group between them.

    Returns
    -------
    int
        The offset of the balancing `)`, or -1 when the text has none.
    """
    depth = 0
    in_regex = False
    escaped = False
    matcher_start = False
    for offset in range(open_at, len(text)):
        char = text[offset]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if in_regex:
            if char == "/":
                in_regex = False
            continue
        if char.isspace():
            continue
        starts_matcher, matcher_start = matcher_start, False
        if starts_matcher and char == "/":
            in_regex = True
            continue
        match char:
            case "(":
                depth += 1
                matcher_start = True
            case ")":
                depth -= 1
                if depth == 0:
                    return offset
    return -1


def _after_space(text: str, index: int) -> int:
    """Return the offset of the next non-space character at or after *index*.

    Returns
    -------
    int
        The offset, or the text's end when only whitespace follows.
    """
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _filter_tokens(text: str) -> typ.Iterator[_FilterToken]:
    """Yield the structural tokens of a filterset expression.

    A predicate's parentheses hold bare or `/regex/` matchers and escapes, so
    each argument is skipped whole rather than tokenised: a `!` or a `-` in a
    test name is data, not an operator. The operators `and`, `or` and `not`
    can be written as words, so a name is read as an operator before it is
    read as a predicate.

    Yields
    ------
    _FilterToken
        Each token in source order.
    """
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        kind = _OPERATOR_CHARS.get(text[index])
        if kind is not None:
            yield _FilterToken(kind, "", "")
            index += 1
            continue
        word = _WORD.match(text, index)
        if word is None:
            yield _FilterToken("unknown", "", "")
            index += 1
            continue
        name = word.group()
        operator = _OPERATOR_WORDS.get(name)
        if operator is not None:
            yield _FilterToken(operator, "", "")
            index = word.end()
            continue
        open_at = _after_space(text, word.end())
        if open_at < len(text) and text[open_at] == "(":
            close_at = _balanced_close(text, open_at)
            if close_at >= 0:
                yield _FilterToken("predicate", name, text[open_at + 1 : close_at])
                index = close_at + 1
                continue
        yield _FilterToken("unknown", "", "")
        index = word.end()


def _predicate_arguments(text: str) -> list[str]:
    """Return the arguments of the predicates a filter selects.

    A predicate is excluded by an odd number of exclusions around it: a `!` or
    `not` before it, or the right operand of a difference (`all() -
    binary(ui)`). The exclusions compose through nesting, so `!binary(ui)` and
    `!(binary(ui))` exclude `ui` while `!(!binary(ui))` selects it again.
    Exclusions are read from the filter's structure rather than from its text:
    a predicate's argument is skipped whole, so a `!` or a `-` in a test name
    selects nothing of its own.

    Returns
    -------
    list[str]
        The text between the parentheses of each selected predicate.
    """
    selected: list[str] = []
    stack: list[tuple[bool, bool]] = []
    flipped = False
    pending = False
    for token in _filter_tokens(text):
        match token.kind:
            case "not" | "diff":
                pending = not pending
            case "open":
                stack.append((flipped, pending))
                flipped = flipped != pending
                pending = False
            case "close":
                flipped, _ = stack.pop() if stack else (False, False)
                pending = False
            case "predicate":
                if token.name in NAMING_PREDICATES and flipped == pending:
                    selected.append(token.argument)
                pending = False
            case _:
                pending = False
    return selected


def _mentions(argument: str, name: str) -> bool:
    """Report whether *name* occurs in a predicate argument as a whole word.

    `ui` is in `binary(ui)` and in `test(/^ui($|::)/)` but not in
    `binary(ui_extra)`: a name that continues into more word characters is a
    different binary or test.

    Returns
    -------
    bool
        True when *name* is delimited by non-word characters or the ends.
    """
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", argument) is not None


def _named(spawner: Spawner, filters: list[str]) -> bool:
    """Report whether a grouped filter names the spawner's binary or a test.

    A spawner that is not a top-level test binary has nothing to name and is
    judged by the repository-level facts alone. A name counts only inside a
    `binary(..)`, `binary_id(..)` or `test(..)` predicate, only when that
    predicate selects rather than excludes, and only as a whole word, so a
    filter on `ui_extra` does not name `ui` and neither does `not(binary(ui))`.

    Returns
    -------
    bool
        True when a grouped filter's predicate mentions the binary or one of
        its functions.
    """
    if spawner["binary"] is None:
        return True
    arguments = _predicate_arguments("\n".join(filters))
    names = [spawner["binary"], *spawner["test_functions"]]
    return any(_mentions(argument, name) for argument in arguments for name in names)


def build_nested_cargo_nextest_envelope(
    checkout: pathlib.Path,
) -> NestedCargoNextestEnvelope:
    """Assemble the spawner and nextest evidence for the nested-cargo rule.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.

    Returns
    -------
    NestedCargoNextestEnvelope
        The nextest configuration's provisions, the compiler-spawning tests with
        whether a grouped override names them, and every fact that could not
        be established.
    """
    problems: list[str] = []
    facts, filters = _nextest(checkout)
    spawners = find_spawners(checkout, problems)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "nextest": facts,
        "spawners": [
            {**spawner, "named": _named(spawner, filters)} for spawner in spawners
        ],
        "problems": sorted(set(problems)),
    }
