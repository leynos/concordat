"""Build the facts for the `nested-cargo-test-builds` rule (NC-011 to NC-013).

The other nested-cargo rules judge how a test's nested build behaves: offline,
into a warm target, inside a deadline. This one asks why the test spends a real
compile at all. Its envelope carries three kinds of evidence:

- the *builders*: every function in test scope that owns a compiler spawn, a
  trybuild or macrotest suite, or a `cargo_metadata` query, with whether the
  reader could see what it spawns and whether it has the shape of a harness test
  (it writes a scratch crate and names the repository's own harness code);
- the repository's *declarations*: the entries of `.concordat/nested-builds.toml`,
  each with the subject the build is the test of and the warm-up that pays for
  it, and whether that warm-up resolves to something that runs before the tests;
- the *problems*: everything the reader could not establish, so an unreadable
  repository is never reported clean.

Nothing here decides whether a finding is reported. The subject vocabulary, the
severity and the verdict belong to the policy.
"""

from __future__ import annotations

import dataclasses
import tomllib
import typing as typ

from .fs_probe import probe_file
from .nested_cargo import load_manifests, origin_slug
from .nested_cargo_builders import BuilderFact, Scan, collect_builders
from .nested_cargo_spawns import collect_rust_evidence, fixture_directories
from .nested_cargo_warmups import Warmups, coverage, read_warmups
from .rust_sources import has_rust_sources

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .codescene_coverage_envelope import Repository

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/nested-cargo-test-builds"
DECLARATIONS: typ.Final = ".concordat/nested-builds.toml"
_REQUIRED: typ.Final = ("path", "subject", "covered_by")


class DeclarationFact(typ.TypedDict):
    """Store one entry of the repository's declaration file.

    Attributes
    ----------
    path:
        The file the declaration names, relative to the checkout.
    function:
        The function it names, or empty for every builder in the file.
    subject:
        What the build is the test of, as the file wrote it.
    covered_by:
        The warm-up that pays for the build, as the file wrote it.
    coverage:
        `resolved` when the warm-up exists and runs before the tests,
        `unresolved` when it does not, `unreadable` when the reader could not
        tell (an unknown form, or a Makefile it cannot follow).
    """

    path: str
    function: str
    subject: str
    covered_by: str
    coverage: str


class NestedCargoBuildsEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the nested-cargo test-builds rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug.
    declarations_state:
        `absent`, `read` or `invalid`, for the declaration file.
    builders:
        Every function in test scope that owns a real compile.
    declarations:
        Every well-formed declaration, with whether its warm-up resolves.
    problems:
        Every fact the builder could not establish, as sentences.
    """

    schema_version: int
    kind: str
    repository: Repository
    declarations_state: str
    builders: list[BuilderFact]
    declarations: list[DeclarationFact]
    problems: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class _Reading:
    """Hold the declaration file's decoded entries and its state."""

    state: str
    entries: list[dict[str, object]]


def _harness_names(parameters: cabc.Mapping[str, object] | None) -> list[str]:
    """Return the harness names the rule is configured with.

    Returns
    -------
    list[str]
        The non-blank string entries of `harness_paths`, or none.
    """
    configured = (parameters or {}).get("harness_paths", [])
    if not isinstance(configured, list):
        return []
    return [name for name in configured if isinstance(name, str) and name.strip()]


def _read_declarations(checkout: pathlib.Path, problems: list[str]) -> _Reading:
    """Read the declaration file's `builder` entries.

    Returns
    -------
    _Reading
        `absent` for no file, `invalid` when it cannot be decoded (the reason is
        in *problems*), otherwise `read` with the entries it holds.
    """
    path = checkout / DECLARATIONS
    probe = probe_file(path)
    if probe.read_error is not None:
        problems.append(f"{DECLARATIONS}: {probe.read_error}")
        return _Reading("invalid", [])
    if not probe.present:
        return _Reading("absent", [])
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        problems.append(f"{DECLARATIONS}: {error}")
        return _Reading("invalid", [])
    entries = document.get("builder", [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        problems.append(f"{DECLARATIONS}: `builder` must be an array of tables")
        return _Reading("invalid", [])
    return _Reading("read", typ.cast("list[dict[str, object]]", entries))


def _bad_fields(raw: dict[str, object]) -> list[str]:
    """Return the fields of a raw entry that are missing, blank or not strings.

    Returns
    -------
    list[str]
        The offending field names; `function` is optional but must be a string.
    """
    bad = [
        key
        for key in _REQUIRED
        if not isinstance(raw.get(key), str) or not str(raw[key]).strip()
    ]
    if not isinstance(raw.get("function", ""), str):
        bad.append("function")
    return bad


def _declaration(
    raw: dict[str, object], warmups: Warmups, problems: list[str], index: int
) -> DeclarationFact | None:
    """Return one declaration as a fact, or None when it is malformed.

    Returns
    -------
    DeclarationFact | None
        The fact; None when a field is missing or mistyped, with the reason
        added to *problems*.
    """
    bad = _bad_fields(raw)
    if bad:
        problems.append(
            f"{DECLARATIONS}: builder entry {index} has a missing or non-string "
            f"field: {', '.join(bad)}"
        )
        return None
    path, covered_by = str(raw["path"]), str(raw["covered_by"])
    return {
        "path": path,
        "function": str(raw.get("function", "")),
        "subject": str(raw["subject"]),
        "covered_by": covered_by,
        "coverage": coverage(path, covered_by, warmups),
    }


def _declarations(
    reading: _Reading, warmups: Warmups, problems: list[str]
) -> list[DeclarationFact]:
    """Return every well-formed declaration of the file.

    Returns
    -------
    list[DeclarationFact]
        The facts, in file order.
    """
    facts = (
        _declaration(raw, warmups, problems, index)
        for index, raw in enumerate(reading.entries, start=1)
    )
    return [fact for fact in facts if fact is not None]


@dataclasses.dataclass(frozen=True, slots=True)
class _Facts:
    """Hold the facts one envelope carries."""

    state: str
    builders: list[BuilderFact]
    declarations: list[DeclarationFact]
    problems: list[str]


def _envelope(checkout: pathlib.Path, facts: _Facts) -> NestedCargoBuildsEnvelope:
    """Return the envelope document for *checkout*.

    Returns
    -------
    NestedCargoBuildsEnvelope
        The facts with the problems sorted and de-duplicated.
    """
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "declarations_state": facts.state,
        "builders": facts.builders,
        "declarations": facts.declarations,
        "problems": sorted(set(facts.problems)),
    }


def _owns_no_rust(
    checkout: pathlib.Path,
    manifests: cabc.Mapping[pathlib.Path, object],
    problems: list[str],
) -> bool:
    """Return whether the checkout has no manifest, no problem and no Rust."""
    if manifests or problems:
        return False
    return not has_rust_sources(checkout)


def build_nested_cargo_builds_envelope(
    checkout: pathlib.Path, parameters: cabc.Mapping[str, object] | None = None
) -> NestedCargoBuildsEnvelope:
    """Assemble the test-builds evidence for the nested-cargo rule.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.
    parameters:
        The rule's configured parameters. `harness_paths` is read here, because
        it decides which names a builder is recorded as using.

    Returns
    -------
    NestedCargoBuildsEnvelope
        The builders, the declarations and every fact that could not be read.
    """
    problems: list[str] = []
    manifests = load_manifests(checkout, problems)
    if _owns_no_rust(checkout, manifests, problems):
        return _envelope(checkout, _Facts("absent", [], [], []))
    evidence = collect_rust_evidence(
        checkout, manifests, problems, cold_build=True, compilers=True
    )
    scan = Scan(checkout, evidence, fixture_directories(checkout, manifests))
    builders = collect_builders(scan, _harness_names(parameters), problems)
    reading = _read_declarations(checkout, problems)
    warmups = read_warmups(checkout, manifests, evidence.reached_by, problems)
    declarations = _declarations(reading, warmups, problems)
    return _envelope(checkout, _Facts(reading.state, builders, declarations, problems))
