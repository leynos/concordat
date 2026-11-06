"""A crate that resolves from a local path is not a dependency to judge.

Dependabot never offers a version update for a dependency that resolves from a
`path`, so it cannot drift from its family and the dependency-family collector
leaves it out. A path dependency that also names a registry `version` is a
published crate (a workspace crate other members depend on), so it stays in.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.dependency_family_envelope import build_dependency_family_envelope

if typ.TYPE_CHECKING:
    import pathlib

_CONFIG: typ.Final = """\
version: 2
updates:
  - package-ecosystem: cargo
    directory: /
    schedule:
      interval: daily
    groups:
      minor-and-patch:
        patterns: ['*']
        update-types: [minor, patch]
"""


_WORKSPACE: typ.Final = "\n[workspace]\nmembers = []\n\n[workspace.dependencies]\n"


def _write(root: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *root*."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _packages(root: pathlib.Path) -> set[str]:
    """Return the packages the collector declares for *root*."""
    envelope = build_dependency_family_envelope(root)
    return {declaration["package"] for declaration in envelope["declarations"]}


def _checkout(root: pathlib.Path, dependencies: str, extra: str = "") -> None:
    """Write a one-crate checkout with the given `[dependencies]` text."""
    _write(root, ".github/dependabot.yml", _CONFIG)
    _write(
        root,
        "Cargo.toml",
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n'
        f"[dependencies]\n{dependencies}\n{extra}",
    )


@pytest.fixture(name="repository")
def _repository(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return an empty checkout directory."""
    return tmp_path


def test_a_path_only_dependency_is_not_collected(repository: pathlib.Path) -> None:
    """A local crate with no registry version cannot drift, so it is left out."""
    _checkout(repository, 'local = { path = "../local" }\nserde = "1"')

    assert _packages(repository) == {"serde"}


def test_a_path_dependency_with_a_version_is_still_collected(
    repository: pathlib.Path,
) -> None:
    """A published workspace crate names a version, so it stays in the audit."""
    _checkout(repository, 'local = { path = "../local", version = "0.10.0" }')

    assert _packages(repository) == {"local"}


def test_a_workspace_inherited_path_only_dependency_is_not_collected(
    repository: pathlib.Path,
) -> None:
    """Inheriting a path-only entry from `[workspace.dependencies]` changes nothing."""
    _checkout(
        repository,
        'local.workspace = true\nserde = "1"',
        _WORKSPACE + 'local = { path = "local" }\n',
    )

    assert _packages(repository) == {"serde"}


def test_a_workspace_inherited_versioned_path_dependency_is_collected(
    repository: pathlib.Path,
) -> None:
    """An inherited entry with a registry version is still judged."""
    _checkout(
        repository,
        "local.workspace = true",
        _WORKSPACE + 'local = { path = "local", version = "1" }\n',
    )

    assert _packages(repository) == {"local"}


@pytest.mark.parametrize(
    ("versioned", "path_only"),
    [
        pytest.param("a", "b", id="versioned-workspace-declared-first"),
        pytest.param("b", "a", id="path-only-workspace-declared-first"),
    ],
)
def test_inheritance_is_resolved_from_each_members_own_workspace(
    repository: pathlib.Path, versioned: str, path_only: str
) -> None:
    """A same-named key in a sibling workspace must not decide this one's entry."""
    _write(repository, ".github/dependabot.yml", _CONFIG)
    _write(
        repository,
        ".concordat",
        "language:\n  rust:\n    surfaces:\n"
        "      - path: a/Cargo.toml\n      - path: b/Cargo.toml\n",
    )
    entries = {
        versioned: 'shared = { path = "shared", version = "1" }\n',
        path_only: 'shared = { path = "shared" }\n',
    }
    for name in ("a", "b"):
        _write(
            repository,
            f"{name}/Cargo.toml",
            f'[package]\nname = "{name}"\nversion = "0.1.0"\n\n'
            "[dependencies]\nshared.workspace = true\n" + _WORKSPACE + entries[name],
        )

    envelope = build_dependency_family_envelope(repository)
    manifests = {
        declaration["manifest"]
        for declaration in envelope["declarations"]
        if declaration["package"] == "shared"
    }

    assert manifests == {f"{versioned}/Cargo.toml"}


@pytest.mark.parametrize(
    "order",
    [
        pytest.param(("a", "b", "m"), id="versioned-workspace-first"),
        pytest.param(("b", "m", "a"), id="path-only-workspace-first"),
    ],
)
def test_a_member_naming_its_workspace_inherits_from_that_workspace(
    repository: pathlib.Path, order: tuple[str, ...]
) -> None:
    """`package.workspace` decides the table, wherever the member sits."""
    _write(repository, ".github/dependabot.yml", _CONFIG)
    declared = "".join(f"      - path: {name}/Cargo.toml\n" for name in order)
    _write(repository, ".concordat", f"language:\n  rust:\n    surfaces:\n{declared}")
    entries = {
        "a": 'shared = { path = "shared", version = "1" }\n',
        "b": 'shared = { path = "shared" }\n',
    }
    for name in ("a", "b"):
        _write(
            repository,
            f"{name}/Cargo.toml",
            f'[package]\nname = "{name}"\nversion = "0.1.0"\n\n'
            "[dependencies]\nshared.workspace = true\n" + _WORKSPACE + entries[name],
        )
    _write(
        repository,
        "m/Cargo.toml",
        '[package]\nname = "m"\nversion = "0.1.0"\nworkspace = "../a"\n\n'
        "[dependencies]\nshared.workspace = true\n",
    )

    envelope = build_dependency_family_envelope(repository)
    manifests = {
        declaration["manifest"]
        for declaration in envelope["declarations"]
        if declaration["package"] == "shared"
    }

    assert manifests == {"a/Cargo.toml", "m/Cargo.toml"}


@pytest.mark.parametrize(
    "naming", ['workspace = ".."\n', ""], ids=["named", "ancestor"]
)
def test_a_member_whose_workspace_is_not_governed_still_inherits_from_it(
    repository: pathlib.Path, naming: str
) -> None:
    """Only the member is declared; its workspace root is read from the checkout."""
    _write(repository, ".github/dependabot.yml", _CONFIG)
    _write(
        repository,
        ".concordat",
        "language:\n  rust:\n    surfaces:\n      - path: a/m/Cargo.toml\n",
    )
    _write(
        repository,
        "a/Cargo.toml",
        '[workspace]\nmembers = ["m"]\n\n[workspace.dependencies]\n'
        'shared = { path = "shared" }\n',
    )
    _write(
        repository,
        "a/m/Cargo.toml",
        '[package]\nname = "m"\nversion = "0.1.0"\n'
        + naming
        + '\n[dependencies]\nshared.workspace = true\nserde = "1"\n',
    )

    assert _packages(repository) == {"serde"}


def _member_of_ungoverned_workspace(repository: pathlib.Path, root: str) -> None:
    """Declare only the member `a/m`, whose workspace root is *root*."""
    _write(repository, ".github/dependabot.yml", _CONFIG)
    _write(
        repository,
        ".concordat",
        "language:\n  rust:\n    surfaces:\n      - path: a/m/Cargo.toml\n",
    )
    _write(
        repository,
        "a/m/Cargo.toml",
        '[package]\nname = "m"\nversion = "0.1.0"\n\n'
        '[dependencies]\nshared.workspace = true\nserde = "1"\n',
    )
    (repository / root).parent.mkdir(parents=True, exist_ok=True)


def test_an_unreadable_workspace_root_is_a_problem_not_an_absence(
    repository: pathlib.Path,
) -> None:
    """A workspace manifest that will not decode must not look like no workspace."""
    _member_of_ungoverned_workspace(repository, "a/Cargo.toml")
    _write(repository, "a/Cargo.toml", "[workspace\n")

    problems = build_dependency_family_envelope(repository)["problems"]

    assert any("a/Cargo.toml" in problem for problem in problems), problems


def test_a_workspace_root_outside_the_checkout_is_a_problem(
    repository: pathlib.Path,
) -> None:
    """A symbolic link above the manifest must not lead the read out of the tree."""
    outside = repository / "outside"
    outside.mkdir()
    _member_of_ungoverned_workspace(repository, "a/Cargo.toml")
    _write(
        outside,
        "Cargo.toml",
        "[workspace]\nmembers = []\n\n[workspace.dependencies]\n"
        'shared = { path = "x" }\n',
    )
    (repository / "a" / "Cargo.toml").symlink_to(outside / "Cargo.toml")
    checkout = repository / "checkout"
    checkout.mkdir()
    for entry in (".github", ".concordat", "a"):
        (repository / entry).rename(checkout / entry)
    # `a` is now a real directory inside the checkout holding a link out of it.

    problems = build_dependency_family_envelope(checkout)["problems"]

    assert any("outside the checkout" in problem for problem in problems), problems


def test_a_workspace_manifest_the_probe_refuses_is_a_problem(
    repository: pathlib.Path,
) -> None:
    """A directory where `Cargo.toml` belongs is neither a workspace nor absent."""
    _member_of_ungoverned_workspace(repository, "a/Cargo.toml")
    (repository / "a" / "Cargo.toml").mkdir()

    problems = build_dependency_family_envelope(repository)["problems"]

    assert any("a/Cargo.toml" in problem for problem in problems), problems


def test_a_workspace_root_behind_a_linked_parent_directory_is_a_problem(
    tmp_path: pathlib.Path,
) -> None:
    """A symbolic link on the way to the manifest must not lead the read out."""
    checkout = tmp_path / "checkout"
    outside = tmp_path / "outside"
    _member_of_ungoverned_workspace(checkout, "a/Cargo.toml")
    _write(
        outside,
        "Cargo.toml",
        "[workspace]\nmembers = []\n\n[workspace.dependencies]\n"
        'shared = { path = "x" }\n',
    )
    # `a/m` is the declared member; `a/..` is the checkout, so name a workspace
    # directory that is itself a link out: `linked/` -> outside.
    (checkout / "linked").symlink_to(outside, target_is_directory=True)
    _write(
        checkout,
        "a/m/Cargo.toml",
        '[package]\nname = "m"\nversion = "0.1.0"\nworkspace = "../../linked"\n\n'
        '[dependencies]\nshared.workspace = true\nserde = "1"\n',
    )

    problems = build_dependency_family_envelope(checkout)["problems"]

    assert any("outside the checkout" in problem for problem in problems), problems


def test_a_workspace_named_above_the_checkout_is_a_problem(
    tmp_path: pathlib.Path,
) -> None:
    """`package.workspace` may name a directory the checkout does not contain."""
    checkout = tmp_path / "checkout"
    _member_of_ungoverned_workspace(checkout, "a/Cargo.toml")
    _write(
        tmp_path,
        "Cargo.toml",
        "[workspace]\nmembers = []\n\n[workspace.dependencies]\n"
        'shared = { path = "x" }\n',
    )
    _write(
        checkout,
        "a/m/Cargo.toml",
        '[package]\nname = "m"\nversion = "0.1.0"\nworkspace = "../../.."\n\n'
        '[dependencies]\nshared.workspace = true\nserde = "1"\n',
    )

    problems = build_dependency_family_envelope(checkout)["problems"]

    assert any("outside the checkout" in problem for problem in problems), problems


def test_a_named_workspace_without_a_manifest_is_a_problem(
    repository: pathlib.Path,
) -> None:
    """Naming a workspace that is not there leaves the inheritance unknown."""
    _member_of_ungoverned_workspace(repository, "a/Cargo.toml")
    _write(
        repository,
        "a/m/Cargo.toml",
        '[package]\nname = "m"\nversion = "0.1.0"\nworkspace = "../elsewhere"\n\n'
        '[dependencies]\nshared.workspace = true\nserde = "1"\n',
    )

    problems = build_dependency_family_envelope(repository)["problems"]

    assert any("elsewhere/Cargo.toml" in problem for problem in problems), problems


def test_a_malformed_ancestor_is_not_probed_for_a_member_naming_its_workspace(
    repository: pathlib.Path,
) -> None:
    """The named workspace is the member's own; unrelated ancestors say nothing."""
    _member_of_ungoverned_workspace(repository, "a/Cargo.toml")
    _write(repository, "a/Cargo.toml", "[workspace\n")
    _write(
        repository,
        "w/Cargo.toml",
        "[workspace]\nmembers = []\n\n[workspace.dependencies]\n"
        'shared = { path = "x" }\n',
    )
    _write(
        repository,
        "a/m/Cargo.toml",
        '[package]\nname = "m"\nversion = "0.1.0"\nworkspace = "../../w"\n\n'
        '[dependencies]\nshared.workspace = true\nserde = "1"\n',
    )

    envelope = build_dependency_family_envelope(repository)

    assert not any("a/Cargo.toml" in p for p in envelope["problems"]), envelope[
        "problems"
    ]
    assert {d["package"] for d in envelope["declarations"]} == {"serde"}
