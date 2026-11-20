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
(`!(binary(ui) | binary(other))`) excludes every name it holds. The scan for
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


PREDICATE: typ.Final = re.compile(r"\b(?:binary|binary_id|test)\(")
# A filter can exclude a binary instead of selecting it, with `!` or `not`
# before the predicate. The exclusion names the same tests, so a name inside it
# says nothing about which tests the override places in the group.
NEGATION: typ.Final = re.compile(r"(?:!\s*\(?\s*|\bnot\s*\(?\s*)$")
# A group of predicates inside one negation: `not(a | b)` or `!(a | b)`. Every
# predicate in the group is excluded, not only the one written beside the `!`.
NEGATION_GROUP: typ.Final = re.compile(r"(?:!|\bnot)\s*\(")


def _negated_spans(text: str) -> list[tuple[int, int]]:
    """Return the spans a negation's parentheses hold.

    A filter can exclude a group of predicates at once
    (`!(binary(ui) | binary(other))`), and each of them names a test the
    override does not place in the group. The span runs from the group's
    opening `(` to its balancing `)`, counted rather than matched lazily
    because a regex argument holds parentheses of its own.

    Returns
    -------
    list[tuple[int, int]]
        The half-open spans held by each negated group.
    """
    spans: list[tuple[int, int]] = []
    for found in NEGATION_GROUP.finditer(text):
        open_at = found.end() - 1
        depth = 0
        for offset in range(open_at, len(text)):
            depth += (text[offset] == "(") - (text[offset] == ")")
            if depth == 0:
                spans.append((open_at, offset))
                break
    return spans


def _predicate_arguments(text: str) -> list[str]:
    """Return the arguments of every selecting predicate, negations skipped.

    A filter such as `not(binary(ui))` or `!test(/^ui/)` excludes those names
    rather than selecting them, so their arguments are not returned: an
    override that only excludes `ui` does not place it in the group. A
    negated group excludes every predicate it holds, not only the first, so
    `!(binary(other) | binary(ui))` does not name `ui` either. The
    parentheses are balanced rather than matched lazily, because a regex
    argument such as `/^name($|::)/` holds parentheses of its own.

    Returns
    -------
    list[str]
        The text between the parentheses of each predicate that is not
        negated.
    """
    excluded = _negated_spans(text)
    found: list[str] = []
    for match in PREDICATE.finditer(text):
        if any(start < match.start() < stop for start, stop in excluded):
            continue
        if NEGATION.search(text[: match.start()]) is not None:
            continue
        depth, start = 1, match.end()
        for offset, char in enumerate(text[start:], start):
            depth += (char == "(") - (char == ")")
            if depth == 0:
                found.append(text[start:offset])
                break
    return found


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
