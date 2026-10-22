"""Audit nested-build fixture lockfiles through parsed files and the real policy.

Each case is a small synthetic checkout shaped like one the corpus pins: a
fixture lock that resolves packages the workspace lock does not (rstest-bdd
before #895), an unlocked fixture crate with its own workspace (netsuke before
#913), and the fixed shapes that must stay quiet.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pygit2
import pytest

from concordat.rules import runner
from concordat.rules.nested_cargo_lock_envelope import (
    build_nested_cargo_lock_envelope,
    registry_packages,
)

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID = "nested-cargo-fixture-lockfiles"
ROOT_MANIFEST = '[workspace]\nmembers = ["crates/*"]\n'
MEMBER_MANIFEST = '[package]\nname = "member"\nversion = "0.1.0"\n'
FIXTURE_MANIFEST = (
    '[package]\nname = "fixture"\nversion = "0.0.0"\n[dependencies]\nserde = "1"\n'
)
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"


def lock(*registry: tuple[str, str], own: tuple[str, str] | None = None) -> str:
    """Return a Cargo.lock holding the given registry packages.

    `own` adds the fixture's own path package, which has no source.

    Returns
    -------
    str
        The lock file's text.
    """
    blocks = [
        f'[[package]]\nname = "{name}"\nversion = "{version}"\nsource = "{REGISTRY}"\n'
        for name, version in registry
    ]
    if own is not None:
        blocks.append(f'[[package]]\nname = "{own[0]}"\nversion = "{own[1]}"\n')
    return "version = 4\n\n" + "\n".join(blocks)


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def workspace(root: pathlib.Path, *packages: tuple[str, str]) -> None:
    """Write a workspace root with one member and a root lock."""
    put(root, "Cargo.toml", ROOT_MANIFEST)
    put(root, "crates/member/Cargo.toml", MEMBER_MANIFEST)
    put(root, "Cargo.lock", lock(*packages))


def rule_ids(root: pathlib.Path) -> set[str]:
    """Return the rule ids the real policy reports for a checkout."""
    result = runner.run_rule(RULE_ID, root)
    return {finding.rule_id for finding in result.findings}


@dataclasses.dataclass(frozen=True, slots=True)
class Case:
    """One fixture layout and the findings it must draw."""

    files: dict[str, str]
    expected: set[str]


SUBSET = lock(("serde", "1.0.0"), own=("fixture", "0.0.0"))
DRIFTED = lock(("serde", "1.0.0"), ("anyhow", "1.0.99"), ("itoa", "1.0.15"))
CASES = {
    "subset-lock": Case(
        {
            "crates/member/tests/fixtures/a/Cargo.toml": FIXTURE_MANIFEST,
            "crates/member/tests/fixtures/a/Cargo.lock": SUBSET,
        },
        set(),
    ),
    "drifted-lock": Case(
        {
            "crates/member/tests/fixtures/a/Cargo.toml": FIXTURE_MANIFEST,
            "crates/member/tests/fixtures/a/Cargo.lock": DRIFTED,
        },
        {"NC-001"},
    ),
    "unlocked-fixture": Case(
        {"tests/ui/pass/Cargo.toml": "[workspace]\n" + FIXTURE_MANIFEST},
        {"NC-002"},
    ),
    "path-only-lock": Case(
        {
            "tests/fixtures/a/Cargo.toml": FIXTURE_MANIFEST,
            "tests/fixtures/a/Cargo.lock": lock(own=("fixture", "0.0.0")),
        },
        set(),
    ),
    "no-dependency-fixture": Case(
        {"tests/fixtures/a/Cargo.toml": MEMBER_MANIFEST}, set()
    ),
    "product-outside-fixture-paths": Case(
        {"tools/probe/Cargo.toml": FIXTURE_MANIFEST, "tools/probe/Cargo.lock": DRIFTED},
        set(),
    ),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_fixture_layouts_draw_the_expected_findings(
    tmp_path: pathlib.Path, case: Case
) -> None:
    """The real policy reports each layout's findings and nothing else."""
    workspace(tmp_path, ("serde", "1.0.0"))
    for relative, text in case.files.items():
        put(tmp_path, relative, text)
    assert rule_ids(tmp_path) == case.expected


def test_a_workspace_members_stray_lock_is_never_read(tmp_path: pathlib.Path) -> None:
    """Netsuke's test_support/Cargo.lock sits in a member; Cargo ignores it."""
    put(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["test_support"]\n')
    put(tmp_path, "test_support/Cargo.toml", MEMBER_MANIFEST)
    put(tmp_path, "test_support/Cargo.lock", DRIFTED)
    put(tmp_path, "Cargo.lock", lock(("serde", "1.0.0")))
    assert rule_ids(tmp_path) == set()


def test_no_root_lock_leaves_nothing_to_align_to(tmp_path: pathlib.Path) -> None:
    """A repository that commits no workspace lock is compliant."""
    put(tmp_path, "Cargo.toml", ROOT_MANIFEST)
    put(tmp_path, "tests/fixtures/a/Cargo.toml", FIXTURE_MANIFEST)
    put(tmp_path, "tests/fixtures/a/Cargo.lock", DRIFTED)
    assert rule_ids(tmp_path) == set()


def test_an_unreadable_fixture_lock_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """A lock that is not TOML is a problem fact, never a clean pass."""
    workspace(tmp_path, ("serde", "1.0.0"))
    put(tmp_path, "tests/fixtures/a/Cargo.toml", FIXTURE_MANIFEST)
    put(tmp_path, "tests/fixtures/a/Cargo.lock", "not = [toml")
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "indeterminate"


def test_the_envelope_reports_the_foreign_packages_sorted(
    tmp_path: pathlib.Path,
) -> None:
    """The fact names the packages and counts them."""
    workspace(tmp_path, ("serde", "1.0.0"))
    put(tmp_path, "tests/fixtures/a/Cargo.toml", FIXTURE_MANIFEST)
    put(tmp_path, "tests/fixtures/a/Cargo.lock", DRIFTED)
    envelope = build_nested_cargo_lock_envelope(tmp_path)
    (fact,) = envelope["nested_locks"]
    assert fact["foreign_count"] == 2
    assert fact["foreign_packages"] == ["anyhow 1.0.99", "itoa 1.0.15"]
    assert envelope["root_lock"]["state"] == "present"


def test_registry_packages_ignore_path_packages() -> None:
    """Only packages with a source take part in the comparison."""
    document = {
        "package": [
            {"name": "a", "version": "1"},
            {"name": "b", "version": "2", "source": "s"},
        ]
    }
    assert registry_packages(typ.cast("dict[str, object]", document)) == {("b", "2")}


def _origin(root: pathlib.Path, url: str) -> None:
    """Initialise a repository at *root* whose origin is *url*."""
    repository = pygit2.init_repository(str(root))
    repository.remotes.create("origin", url)


def test_a_named_exemption_applies_through_the_real_runner(
    tmp_path: pathlib.Path,
) -> None:
    """rstest-bdd's published-gpui fixtures are exempt by the rule's defaults."""
    _origin(tmp_path, "https://github.com/leynos/rstest-bdd.git")
    workspace(tmp_path, ("serde", "1.0.0"))
    put(tmp_path, "tests/fixtures/published-gpui-0-2-2/Cargo.toml", FIXTURE_MANIFEST)
    put(tmp_path, "tests/fixtures/published-gpui-0-2-2/Cargo.lock", DRIFTED)
    assert rule_ids(tmp_path) == set()


def test_an_exemption_does_not_travel_to_another_repository(
    tmp_path: pathlib.Path,
) -> None:
    """The same directory under another repository's name is audited."""
    _origin(tmp_path, "https://github.com/leynos/other.git")
    workspace(tmp_path, ("serde", "1.0.0"))
    put(tmp_path, "tests/fixtures/published-gpui-0-2-2/Cargo.toml", FIXTURE_MANIFEST)
    put(tmp_path, "tests/fixtures/published-gpui-0-2-2/Cargo.lock", DRIFTED)
    assert rule_ids(tmp_path) == {"NC-001"}


def test_a_manifest_below_a_workspace_that_cargo_refuses_is_not_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """Without its own workspace or an exclude, a nested package cannot be built."""
    workspace(tmp_path, ("serde", "1.0.0"))
    put(tmp_path, "crates/member/tests/ui_macros/Cargo.toml", FIXTURE_MANIFEST)
    assert rule_ids(tmp_path) == set()


def test_an_excluded_directory_is_a_standalone_fixture(tmp_path: pathlib.Path) -> None:
    """A root `exclude` makes the directory buildable on its own, so it needs a lock."""
    put(
        tmp_path,
        "Cargo.toml",
        '[workspace]\nmembers = ["crates/*"]\nexclude = ["tests/fixtures/a"]\n',
    )
    put(tmp_path, "crates/member/Cargo.toml", MEMBER_MANIFEST)
    put(tmp_path, "Cargo.lock", lock(("serde", "1.0.0")))
    put(tmp_path, "tests/fixtures/a/Cargo.toml", FIXTURE_MANIFEST)
    assert rule_ids(tmp_path) == {"NC-002"}


def test_a_package_with_no_workspace_above_it_is_standalone(
    tmp_path: pathlib.Path,
) -> None:
    """A plain root package does not adopt a fixture below it."""
    put(tmp_path, "Cargo.toml", MEMBER_MANIFEST)
    put(tmp_path, "Cargo.lock", lock(("serde", "1.0.0")))
    put(tmp_path, "tests/fixtures/a/Cargo.toml", FIXTURE_MANIFEST)
    assert rule_ids(tmp_path) == {"NC-002"}


@pytest.mark.parametrize("member", [".", "..", "/nonexistent/elsewhere", "missing"])
def test_unusual_workspace_member_entries_do_not_break_the_audit(
    tmp_path: pathlib.Path, member: str
) -> None:
    """A `.` member, a workspace listing its own root, came from the estate run."""
    put(tmp_path, "Cargo.toml", f'[workspace]\nmembers = ["{member}", "crates/*"]\n')
    put(tmp_path, "crates/member/Cargo.toml", MEMBER_MANIFEST)
    put(tmp_path, "Cargo.lock", lock(("serde", "1.0.0")))
    assert rule_ids(tmp_path) == set()
