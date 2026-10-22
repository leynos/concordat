"""Audit fixture manifest coverage through parsed files and the real policy.

The cases mirror the corpus: rstest-bdd's nested fixtures before and after
#895 added their directories to `dependabot.yml`, and the shapes that must stay
quiet (members, path-only crates, workspace roots).
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.nested_cargo_manifest_envelope import (
    build_nested_cargo_manifest_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID = "nested-cargo-fixture-manifests"
ROOT = '[workspace]\nmembers = ["crates/*"]\n'
MEMBER = '[package]\nname = "member"\nversion = "0.1.0"\n[dependencies]\nserde = "1"\n'
FIXTURE = (
    '[package]\nname = "fixture"\nversion = "0.0.0"\n[dependencies]\nserde = "1"\n'
)
PATH_ONLY = (
    '[package]\nname = "fixture"\nversion = "0.0.0"\n'
    '[dependencies]\nmember = { path = "../../crates/member" }\n'
)


def dependabot(*directories: str) -> str:
    """Return a dependabot.yml with one cargo entry over the given directories."""
    listed = "".join(f'      - "{item}"\n' for item in directories)
    entry = (
        '  - package-ecosystem: "cargo"\n    directories:\n' + listed
        if directories
        else '  - package-ecosystem: "github-actions"\n    directory: "/"\n'
    )
    return "version: 2\nupdates:\n" + entry + "    schedule:\n      interval: daily\n"


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rule_ids(root: pathlib.Path) -> set[str]:
    """Return the rule ids the real policy reports for a checkout."""
    return {finding.rule_id for finding in runner.run_rule(RULE_ID, root).findings}


@dataclasses.dataclass(frozen=True, slots=True)
class Case:
    """One layout, its Dependabot directories and the findings it must draw."""

    files: dict[str, str]
    directories: tuple[str, ...]
    expected: set[str]


FIX = "tests/fixtures/a/Cargo.toml"
CASES = {
    "uncovered-fixture": Case({FIX: FIXTURE}, ("/",), {"NC-003"}),
    "exact-directory": Case({FIX: FIXTURE}, ("/", "/tests/fixtures/a"), set()),
    "glob-reaches-it": Case({FIX: FIXTURE}, ("/", "/tests/fixtures/*"), set()),
    "root-entry-does-not-reach-down": Case({FIX: FIXTURE}, ("/",), {"NC-003"}),
    "path-only-has-nothing-to-update": Case({FIX: PATH_ONLY}, ("/",), set()),
    "workspace-member-is-not-a-candidate": Case(
        {"crates/other/Cargo.toml": MEMBER}, ("/",), set()
    ),
    "nested-workspace-with-registry-member": Case(
        {
            "tests/ws/Cargo.toml": '[workspace]\nmembers = ["m"]\n',
            "tests/ws/m/Cargo.toml": MEMBER,
        },
        ("/",),
        {"NC-003"},
    ),
    "nested-workspace-covered-root": Case(
        {
            "tests/ws/Cargo.toml": '[workspace]\nmembers = ["m"]\n',
            "tests/ws/m/Cargo.toml": MEMBER,
        },
        ("/", "/tests/ws"),
        set(),
    ),
    "no-cargo-entry-at-all": Case({FIX: FIXTURE}, (), {"NC-003"}),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_layouts_draw_the_expected_findings(tmp_path: pathlib.Path, case: Case) -> None:
    """The real policy reports each layout's findings and nothing else."""
    put(tmp_path, "Cargo.toml", ROOT)
    put(tmp_path, "crates/member/Cargo.toml", MEMBER)
    put(tmp_path, ".github/dependabot.yml", dependabot(*case.directories))
    for relative, text in case.files.items():
        put(tmp_path, relative, text)
    assert rule_ids(tmp_path) == case.expected


def test_no_dependabot_configuration_leaves_the_fixture_uncovered(
    tmp_path: pathlib.Path,
) -> None:
    """With no configuration at all nothing updates the fixture."""
    put(tmp_path, "Cargo.toml", ROOT)
    put(tmp_path, FIX, FIXTURE)
    assert rule_ids(tmp_path) == {"NC-003"}


def test_an_undecodable_configuration_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """A configuration that is not YAML is a fact, never a pass."""
    put(tmp_path, "Cargo.toml", ROOT)
    put(tmp_path, FIX, FIXTURE)
    put(tmp_path, ".github/dependabot.yml", "updates: [unclosed")
    assert runner.run_rule(RULE_ID, tmp_path).verdict == "indeterminate"


def test_an_unreadable_manifest_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """A manifest that is not TOML is a problem fact, never a clean pass."""
    put(tmp_path, "Cargo.toml", ROOT)
    put(tmp_path, ".github/dependabot.yml", dependabot("/"))
    put(tmp_path, FIX, "[package\nname =")
    assert runner.run_rule(RULE_ID, tmp_path).verdict == "indeterminate"


def test_the_envelope_records_coverage_per_manifest(tmp_path: pathlib.Path) -> None:
    """Each candidate carries its directory and whether an entry covers it."""
    put(tmp_path, "Cargo.toml", ROOT)
    put(tmp_path, FIX, FIXTURE)
    put(tmp_path, "tests/fixtures/b/Cargo.toml", FIXTURE)
    put(tmp_path, ".github/dependabot.yml", dependabot("/", "/tests/fixtures/a"))
    envelope = build_nested_cargo_manifest_envelope(tmp_path)
    assert {m["directory"]: m["covered"] for m in envelope["manifests"]} == {
        "tests/fixtures/a": True,
        "tests/fixtures/b": False,
    }
    assert envelope["dependabot"]["state"] == "present"


@pytest.mark.parametrize(
    ("spec", "candidates"),
    [
        ('serde = "1"', 1),
        ('serde = { version = "1", features = ["derive"] }', 1),
        ('local = { path = "../x" }', 0),
        ('forked = { git = "https://example.invalid/x" }', 0),
        ("shared = { workspace = true }", 0),
    ],
)
def test_only_registry_dependencies_make_a_candidate(
    tmp_path: pathlib.Path, spec: str, candidates: int
) -> None:
    """A path, git or inherited dependency has nothing for Dependabot to update."""
    put(tmp_path, "Cargo.toml", ROOT)
    put(
        tmp_path,
        FIX,
        f'[package]\nname = "f"\nversion = "0.0.0"\n[dependencies]\n{spec}\n',
    )
    put(tmp_path, ".github/dependabot.yml", dependabot("/"))
    envelope = build_nested_cargo_manifest_envelope(tmp_path)
    assert len(envelope["manifests"]) == candidates
