"""Audit nested-build fixture lockfiles through parsed files and the real policy.

Each case is a small synthetic checkout shaped like one the corpus pins: a
fixture lock that resolves packages the workspace lock does not (rstest-bdd
before #895), an unlocked fixture crate with its own workspace (netsuke before
#913), and the fixed shapes that must stay quiet.
"""

from __future__ import annotations

import dataclasses
import pathlib
import tomllib
import typing as typ

import pygit2
import pytest

from concordat.rules import runner
from concordat.rules.nested_cargo import workspace_member_directories
from concordat.rules.nested_cargo_lock_envelope import (
    build_nested_cargo_lock_envelope,
    registry_packages,
)

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
    assert registry_packages(typ.cast("dict[str, object]", document)) == {
        ("b", "2", "s")
    }


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


def test_the_same_package_at_another_version_is_drift(tmp_path: pathlib.Path) -> None:
    """Serde present in both locks, but 1.0.1 in the fixture: version drift counts."""
    workspace(tmp_path, ("serde", "1.0.0"))
    put(tmp_path, "tests/fixtures/a/Cargo.toml", FIXTURE_MANIFEST)
    put(tmp_path, "tests/fixtures/a/Cargo.lock", lock(("serde", "1.0.1")))
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {f.rule_id for f in result.findings} == {"NC-001"}
    assert "serde 1.0.1" in result.findings[0].message


def test_the_same_name_and_version_from_another_source_is_drift(
    tmp_path: pathlib.Path,
) -> None:
    """A git serde 1.0.0 is not the crates.io serde 1.0.0 the workspace compiled."""
    workspace(tmp_path, ("serde", "1.0.0"))
    git = 'source = "git+https://example.invalid/serde#abc"'
    text = (
        'version = 4\n\n[[package]]\nname = "serde"\nversion = "1.0.0"\n' + git + "\n"
    )
    put(tmp_path, "tests/fixtures/a/Cargo.toml", FIXTURE_MANIFEST)
    put(tmp_path, "tests/fixtures/a/Cargo.lock", text)
    (finding,) = runner.run_rule(RULE_ID, tmp_path).findings
    assert finding.rule_id == "NC-001"
    assert "serde 1.0.0 (git+https://example.invalid/serde#abc)" in finding.message


def test_a_fixture_below_an_excluded_directory_is_standalone(
    tmp_path: pathlib.Path,
) -> None:
    """`exclude = ["tests/fixtures"]` frees `tests/fixtures/demo` too, as in Cargo."""
    put(
        tmp_path,
        "Cargo.toml",
        '[workspace]\nmembers = ["crates/*"]\nexclude = ["tests/fixtures"]\n',
    )
    put(tmp_path, "crates/member/Cargo.toml", MEMBER_MANIFEST)
    put(tmp_path, "Cargo.lock", lock(("serde", "1.0.0")))
    put(tmp_path, "tests/fixtures/demo/Cargo.toml", FIXTURE_MANIFEST)
    assert rule_ids(tmp_path) == {"NC-002"}


def test_a_member_glob_does_not_reach_into_an_excluded_directory(
    tmp_path: pathlib.Path,
) -> None:
    """A glob match below an excluded directory is not a member, so its lock counts."""
    put(
        tmp_path,
        "Cargo.toml",
        '[workspace]\nmembers = ["tests/*/*"]\nexclude = ["tests/fixtures"]\n',
    )
    put(tmp_path, "Cargo.lock", lock(("serde", "1.0.0")))
    put(tmp_path, "tests/fixtures/demo/Cargo.toml", FIXTURE_MANIFEST)
    put(tmp_path, "tests/fixtures/demo/Cargo.lock", lock(("anyhow", "1.0.99")))
    assert rule_ids(tmp_path) == {"NC-001"}


@pytest.mark.parametrize(
    "table",
    [
        "[target.'cfg(unix)'.dependencies]\nlibc = \"0.2\"\n",
        "[target.'cfg(unix)'.dev-dependencies]\nlibc = \"0.2\"\n",
        "[target.'cfg(unix)'.build-dependencies]\nlibc = \"0.2\"\n",
    ],
)
def test_target_specific_dependencies_make_an_unlocked_fixture(
    tmp_path: pathlib.Path, table: str
) -> None:
    """A fixture whose only dependencies are target-specific still resolves them."""
    workspace(tmp_path, ("serde", "1.0.0"))
    manifest = '[package]\nname = "fixture"\nversion = "0.0.0"\n' + table
    put(tmp_path, "tests/fixtures/demo/Cargo.toml", "[workspace]\n" + manifest)
    assert rule_ids(tmp_path) == {"NC-002"}


def explicit_member_layout(root: pathlib.Path) -> None:
    """Write a workspace that lists a member inside a directory it excludes."""
    put(
        root,
        "Cargo.toml",
        '[workspace]\nmembers = ["tests/fixtures/demo"]\n'
        'exclude = ["tests/fixtures"]\n',
    )
    put(root, "Cargo.lock", lock(("serde", "1.0.0")))
    put(root, "tests/fixtures/demo/Cargo.toml", FIXTURE_MANIFEST)


def test_an_explicit_member_under_an_excluded_directory_needs_no_lock(
    tmp_path: pathlib.Path,
) -> None:
    """Cargo lets an explicit member override the exclusion, so there is no NC-002."""
    explicit_member_layout(tmp_path)
    assert rule_ids(tmp_path) == set()


def test_an_explicit_members_own_lock_is_never_read(tmp_path: pathlib.Path) -> None:
    """A drifting lock inside an explicit member is ignored, not NC-001."""
    explicit_member_layout(tmp_path)
    put(tmp_path, "tests/fixtures/demo/Cargo.lock", lock(("anyhow", "1.0.99")))
    assert rule_ids(tmp_path) == set()


def path_dependency_layout(root: pathlib.Path, *, exclude: str = "") -> None:
    """Write a workspace whose member depends by path on a fixture inside it."""
    put(
        root,
        "Cargo.toml",
        f'[workspace]\nmembers = ["crates/member"]\n{exclude}',
    )
    put(
        root,
        "crates/member/Cargo.toml",
        MEMBER_MANIFEST + '[dev-dependencies]\ndemo = { path = "../../tests/demo" }\n',
    )
    put(root, "Cargo.lock", lock(("serde", "1.0.0")))
    put(root, "tests/demo/Cargo.toml", FIXTURE_MANIFEST)


def test_a_path_dependency_inside_the_workspace_is_an_implicit_member(
    tmp_path: pathlib.Path,
) -> None:
    """Cargo adds in-workspace path dependencies as members: no lock is read."""
    path_dependency_layout(tmp_path)
    put(tmp_path, "tests/demo/Cargo.lock", lock(("anyhow", "1.0.99")))
    assert rule_ids(tmp_path) == set()


def test_an_excluded_path_dependency_is_not_a_member(tmp_path: pathlib.Path) -> None:
    """An excluded directory stays standalone, so a missing lock is NC-002."""
    path_dependency_layout(tmp_path, exclude='exclude = ["tests/demo"]\n')
    assert rule_ids(tmp_path) == {"NC-002"}


def test_implicit_members_follow_path_dependencies_transitively(
    tmp_path: pathlib.Path,
) -> None:
    """A path dependency of an implicit member is a member as well."""
    path_dependency_layout(tmp_path)
    put(
        tmp_path,
        "tests/demo/Cargo.toml",
        FIXTURE_MANIFEST + '[dev-dependencies]\ndeep = { path = "../deep" }\n',
    )
    put(tmp_path, "tests/deep/Cargo.toml", FIXTURE_MANIFEST)
    assert rule_ids(tmp_path) == set()


def test_a_path_dependency_outside_the_workspace_is_not_a_member(
    tmp_path: pathlib.Path,
) -> None:
    """A path dependency above the workspace root stays outside it."""
    root = tmp_path / "repo"
    put(root, "Cargo.toml", '[workspace]\nmembers = ["crates/member"]\n')
    put(
        root,
        "crates/member/Cargo.toml",
        MEMBER_MANIFEST + '[dependencies]\nother = { path = "../../../other" }\n',
    )
    put(tmp_path, "other/Cargo.toml", FIXTURE_MANIFEST)
    manifests = {
        root / "Cargo.toml": tomllib.loads((root / "Cargo.toml").read_text()),
        root / "crates/member/Cargo.toml": tomllib.loads(
            (root / "crates/member/Cargo.toml").read_text()
        ),
        tmp_path / "other/Cargo.toml": tomllib.loads(FIXTURE_MANIFEST),
    }
    assert workspace_member_directories(root, manifests) == {"crates/member"}


def test_implicit_members_are_found_through_a_relative_checkout_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative checkout path keys the manifests relatively; lookups must resolve."""
    path_dependency_layout(tmp_path / "repo")
    put(tmp_path / "repo", "tests/demo/Cargo.lock", lock(("anyhow", "1.0.99")))
    monkeypatch.chdir(tmp_path)
    result = runner.run_rule(RULE_ID, pathlib.Path("repo"))
    assert {finding.rule_id for finding in result.findings} == set()


def test_implicit_members_are_found_through_a_symlinked_checkout(
    tmp_path: pathlib.Path,
) -> None:
    """A checkout reached through a symlink behaves like the real directory."""
    path_dependency_layout(tmp_path / "real")
    put(tmp_path / "real", "tests/demo/Cargo.lock", lock(("anyhow", "1.0.99")))
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    result = runner.run_rule(RULE_ID, tmp_path / "link")
    assert {finding.rule_id for finding in result.findings} == set()
