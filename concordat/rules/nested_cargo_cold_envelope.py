"""Build the facts for the `nested-cargo-cold-build` rule (NC-009, NC-010).

A test that starts a nested build inside its own deadline pays for whatever the
target directory does not already hold. Two defects share that symptom, and the
envelope carries the evidence for each:

- a nested spawn whose target directory is *private* — derived from a temporary
  directory or unique to the run — so no later run can find what an earlier one
  built (NC-009, read from the spawn's recorded calls);
- a test source that compiles in a timed test while the repository declares no
  preparation outside it: no nextest setup script, no named preparation test and
  no preparation gate in the crate (NC-010).

Nothing here decides whether a finding is reported. Exemptions, severity and the
verdict belong to the policy, which reads the repository slug and each fact's
path. The builder's job is to establish the facts, and to record as a problem
every input it could not read, so the policy answers indeterminate rather than
treating an unreadable repository as a clean one.
"""

from __future__ import annotations

import typing as typ

from .nested_cargo import load_manifests, origin_slug
from .nested_cargo_gates import read_gates
from .nested_cargo_makefile_passes import PassFact, makefile_passes
from .nested_cargo_makefile_preparation import makefile_prepares
from .nested_cargo_preparation import (
    BuildFact,
    Preparation,
    collect_builds,
    crate_directories,
    declared_tests,
    named_test_crates,
)
from .nested_cargo_setup_scripts import read_setup_rules
from .nested_cargo_source import read_sources
from .nested_cargo_spawns import (
    SpawnFact,
    collect_rust_evidence,
    fixture_directories,
)
from .rust_sources import has_rust_sources

if typ.TYPE_CHECKING:
    import pathlib

    from .codescene_coverage_envelope import Repository

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/nested-cargo-cold-build"


class NestedCargoColdEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the nested-cargo cold-build rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The checkout's path and, where the origin names one, its GitHub slug,
        which the rule's exemptions are matched against.
    spawns:
        Every command in test scope that builds, with where it builds.
    builds:
        Every test source that compiles in a timed test, with the preparation
        the repository declares for it.
    passes:
        Every test pass of the Makefile's `test` target, with what prepared it
        (NC-010 per pass).
    problems:
        Every fact the builder could not establish, as sentences.
    """

    schema_version: int
    kind: str
    repository: Repository
    spawns: list[SpawnFact]
    builds: list[BuildFact]
    passes: list[PassFact]
    problems: list[str]


def _owns_no_rust(
    checkout: pathlib.Path,
    manifests: typ.Mapping[pathlib.Path, object],
    problems: list[str],
) -> bool:
    """Return whether the checkout has no Cargo manifest and no Rust source.

    A manifest that exists but cannot be read leaves a problem, so an unreadable
    tree never turns into a repository without Rust.
    """
    if manifests or problems:
        return False
    return not has_rust_sources(checkout)


def _empty_envelope(checkout: pathlib.Path) -> NestedCargoColdEnvelope:
    """Return the envelope of a repository with no Rust: no fact, so no finding.

    Its Makefile's `test` recipe (a Python or uv gate) is not this rule's business.
    """
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "spawns": [],
        "builds": [],
        "passes": [],
        "problems": [],
    }


def build_nested_cargo_cold_envelope(
    checkout: pathlib.Path, parameters: typ.Mapping[str, object] | None = None
) -> NestedCargoColdEnvelope:
    """Assemble the cold-build evidence for the nested-cargo rule.

    Parameters
    ----------
    checkout:
        The repository checkout to describe.
    parameters:
        The rule's configured parameters. The preparation and compiling-helper
        names are read here, because they decide which facts exist; exemptions
        and severity stay with the policy.

    Returns
    -------
    NestedCargoColdEnvelope
        The spawn and build facts, and every fact that could not be established.
    """
    problems: list[str] = []
    manifests = load_manifests(checkout, problems)
    if _owns_no_rust(checkout, manifests, problems):
        return _empty_envelope(checkout)
    evidence = collect_rust_evidence(checkout, manifests, problems, cold_build=True)
    fixtures = fixture_directories(checkout, manifests)
    # The sources are read a second time rather than shared with the spawn
    # reader: only the names of preparation functions are wanted from them, and
    # the spawn reader's scope map is no use to that question.
    sources = read_sources(checkout, fixtures, [])
    preparation = Preparation(
        crate_directories(checkout, manifests),
        read_setup_rules(checkout, problems),
        read_gates(parameters),
        makefile_prepares(checkout),
        named_test_crates(checkout, manifests),
        declared_tests(checkout, manifests),
    )
    builds = collect_builds(evidence, sources, preparation)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": ".", "name": origin_slug(checkout)},
        "spawns": evidence.spawns,
        "builds": builds,
        "passes": makefile_passes(checkout),
        "problems": sorted(set(problems)),
    }
