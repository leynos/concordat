"""Audit why tests spend a real compile, through the real policy and reader.

Each case is a small synthetic checkout. The harness-only case reproduces the
shape of the test leynos/rstest-bdd#989 deleted: a function that writes a
scratch crate into a temporary directory, builds it with `cargo`, and feeds the
result to the repository's own counter and writer.
"""

from __future__ import annotations

import typing as typ

import pygit2

from concordat.rules import packages, runner

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID = "nested-cargo-test-builds"
DECLARATIONS = ".concordat/nested-builds.toml"

CARGO_TOML = '[package]\nname = "example"\nversion = "0.1.0"\nedition = "2021"\n'

PLUGIN_TEST = """\
#[test]
fn builds_the_plugin() {
    std::process::Command::new("cargo")
        .arg("build")
        .status()
        .unwrap();
}
"""

HARNESS_TEST = """\
#[test]
fn a_real_build_is_counted() {
    let scratch = tempfile::tempdir().unwrap();
    let manifest = scratch.path().join("Cargo.toml");
    std::fs::write(manifest, "[package]\\nname = \\"c\\"").unwrap();
    let output = std::process::Command::new(env!("CARGO"))
        .arg("build")
        .current_dir(scratch.path())
        .output()
        .unwrap();
    let compiled = nested_cargo::count_compiling(&output.stderr);
    assert!(compiled >= 1);
}
"""

TRYBUILD_TEST = """\
#[test]
fn ui() {
    let t = trybuild::TestCases::new();
    t.compile_fail("tests/ui/*.rs");
}
"""

UNREADABLE_TEST = """\
#[test]
fn runs_a_tool() {
    std::process::Command::new(tool_name()).arg(subcommand()).status().unwrap();
}
"""

PROBE_TEST = """\
#[test]
fn reports_a_version() {
    std::process::Command::new("cargo").arg("--version").status().unwrap();
}
"""

METADATA_TEST = """\
#[test]
fn reads_the_graph() {
    let _ = cargo_metadata::MetadataCommand::new().exec().unwrap();
}
"""

MAKEFILE = """\
test: prepare-nested
\tcargo nextest run

prepare-nested:
\tcargo test --no-run
"""

SETUP_SCRIPT = """\
[scripts.setup.prepare]
command = "make prepare-nested"

[[profile.default.scripts]]
filter = "all()"
setup = "prepare"
"""


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def checkout(root: pathlib.Path, files: dict[str, str] | None = None) -> pathlib.Path:
    """Create a repository at *root* holding a manifest and the named sources.

    Returns
    -------
    pathlib.Path
        *root*, with `origin` set to a `leynos/example` remote.
    """
    repository = pygit2.init_repository(str(root))
    repository.remotes.create("origin", "https://github.com/leynos/example.git")
    put(root, "Cargo.toml", CARGO_TOML)
    put(root, "src/lib.rs", "")
    for relative, text in (files or {}).items():
        put(root, relative, text)
    return root


def declare(root: pathlib.Path, *entries: str) -> None:
    """Write the declaration file from *entries*, one `[[builder]]` body each."""
    put(root, DECLARATIONS, "".join(f"[[builder]]\n{entry}\n" for entry in entries))


def profile(root: pathlib.Path) -> set[tuple[str, str]]:
    """Return the `(rule id, path)` pairs the policy reports for *root*."""
    return {(f.rule_id, f.path) for f in runner.run_rule(RULE_ID, root).findings}


def test_the_package_resolves_to_the_test_builds_envelope(
    tmp_path: pathlib.Path,
) -> None:
    """The builder mapping and the manifest declaration must agree."""
    envelope = packages.default_envelope_builder(RULE_ID, tmp_path)
    assert envelope["kind"] == "policy-input/nested-cargo-test-builds"


def test_an_undeclared_spawn_is_nc011(tmp_path: pathlib.Path) -> None:
    """A real compile with no declaration is reported at its file."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    assert profile(root) == {("NC-011", "tests/cli.rs")}


def test_a_declared_spawn_with_a_resolved_make_warm_up_is_clean(
    tmp_path: pathlib.Path,
) -> None:
    """A declaration by file and function, warmed by a prerequisite of `test`."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST, "Makefile": MAKEFILE})
    declare(
        root,
        'path = "tests/cli.rs"\nfunction = "builds_the_plugin"\n'
        'subject = "product-cli"\ncovered_by = "make:prepare-nested"',
    )
    assert profile(root) == set()


def test_a_declaration_without_a_function_covers_the_file(
    tmp_path: pathlib.Path,
) -> None:
    """Omitting the function declares every builder in the file."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST, "Makefile": MAKEFILE})
    declare(
        root,
        'path = "tests/cli.rs"\nsubject = "product-cli"\n'
        'covered_by = "make:prepare-nested"',
    )
    assert profile(root) == set()


def test_a_make_goal_that_test_does_not_run_is_nc013(tmp_path: pathlib.Path) -> None:
    """A declared goal that exists but is not before `test` does not warm it."""
    root = checkout(
        tmp_path,
        {
            "tests/cli.rs": PLUGIN_TEST,
            "Makefile": MAKEFILE + "\nunrelated:\n\techo hi\n",
        },
    )
    declare(
        root,
        'path = "tests/cli.rs"\nsubject = "product-cli"\ncovered_by = "make:unrelated"',
    )
    assert profile(root) == {("NC-013", "tests/cli.rs")}


def test_a_nextest_setup_script_covers_a_declared_builder(
    tmp_path: pathlib.Path,
) -> None:
    """The `nextest-setup` form resolves when a setup rule selects the binary."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    put(root, ".config/nextest.toml", SETUP_SCRIPT)
    declare(
        root,
        'path = "tests/cli.rs"\nsubject = "product-cli"\ncovered_by = "nextest-setup"',
    )
    assert profile(root) == set()


def test_nextest_setup_without_a_script_is_nc013(tmp_path: pathlib.Path) -> None:
    """Declaring a setup script that the repository does not have is NC-013."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    declare(
        root,
        'path = "tests/cli.rs"\nsubject = "product-cli"\ncovered_by = "nextest-setup"',
    )
    assert profile(root) == {("NC-013", "tests/cli.rs")}


def test_a_named_preparation_test_resolves(tmp_path: pathlib.Path) -> None:
    """The `test:<name>` form resolves to a function the checkout defines."""
    root = checkout(
        tmp_path,
        {
            "tests/cli.rs": PLUGIN_TEST,
            "tests/prepare.rs": "#[test]\nfn nested_fixture_preparation() {}\n",
        },
    )
    declare(
        root,
        'path = "tests/cli.rs"\nsubject = "product-cli"\n'
        'covered_by = "test:nested_fixture_preparation"',
    )
    assert profile(root) == set()


def test_an_unknown_warm_up_form_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """A warm-up the reader has no form for is EN-001, not a pass or a finding."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    declare(
        root,
        'path = "tests/cli.rs"\nsubject = "product-cli"\ncovered_by = "somehow"',
    )
    assert profile(root) == {("EN-001", "tests/cli.rs")}


def test_a_harness_test_that_builds_a_scratch_crate_is_nc012(
    tmp_path: pathlib.Path,
) -> None:
    """The deleted rstest-bdd test's shape is reported although it is declared."""
    root = checkout(tmp_path, {"tests/report.rs": HARNESS_TEST, "Makefile": MAKEFILE})
    declare(
        root,
        'path = "tests/report.rs"\nsubject = "cargo-behaviour"\n'
        'covered_by = "make:prepare-nested"',
    )
    assert profile(root) == {("NC-012", "tests/report.rs")}


def test_a_declaration_of_a_prohibited_subject_is_nc012(
    tmp_path: pathlib.Path,
) -> None:
    """Declaring the infrastructure as the subject is itself the finding."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST, "Makefile": MAKEFILE})
    declare(
        root,
        'path = "tests/cli.rs"\nsubject = "compile-counter"\n'
        'covered_by = "make:prepare-nested"',
    )
    assert profile(root) == {("NC-012", "tests/cli.rs")}


def test_a_trybuild_suite_is_declared_by_file(tmp_path: pathlib.Path) -> None:
    """A trybuild suite has no function: the declaration names the file."""
    root = checkout(tmp_path, {"tests/ui.rs": TRYBUILD_TEST, "Makefile": MAKEFILE})
    assert profile(root) == {("NC-011", "tests/ui.rs")}
    declare(
        root,
        'path = "tests/ui.rs"\nsubject = "compiler-diagnostics"\n'
        'covered_by = "make:prepare-nested"',
    )
    assert profile(root) == set()


def test_a_metadata_query_in_a_test_file_is_inventoried(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo_metadata::MetadataCommand` takes the package-cache lock too."""
    root = checkout(tmp_path, {"tests/graph.rs": METADATA_TEST})
    assert profile(root) == {("NC-011", "tests/graph.rs")}


def test_an_unreadable_spawn_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """A program the reader cannot see is neither accepted nor accused."""
    root = checkout(tmp_path, {"tests/shell.rs": UNREADABLE_TEST})
    assert {rule for rule, _ in profile(root)} <= {"EN-001"}


def test_a_version_probe_is_not_inventoried(tmp_path: pathlib.Path) -> None:
    """`cargo --version` compiles nothing, so it needs no declaration."""
    root = checkout(tmp_path, {"tests/probe.rs": PROBE_TEST})
    assert profile(root) == set()


def test_a_stale_declaration_is_nc011(tmp_path: pathlib.Path) -> None:
    """A declaration with no builder behind it is reported at its file."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    declare(
        root,
        'path = "tests/gone.rs"\nsubject = "product-cli"\ncovered_by = "nextest-setup"',
        'path = "tests/cli.rs"\nsubject = "product-cli"\ncovered_by = "nextest-setup"',
    )
    assert ("NC-011", "tests/gone.rs") in profile(root)


def test_a_declaration_file_that_does_not_parse_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """A broken declaration file never reads as a repository with no builders."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    put(root, DECLARATIONS, "[[builder\n")
    assert ("EN-001", "") in profile(root)


def test_a_repository_without_rust_has_nothing_to_judge(
    tmp_path: pathlib.Path,
) -> None:
    """No manifest and no Rust source means no fact and no finding."""
    repository = pygit2.init_repository(str(tmp_path))
    repository.remotes.create("origin", "https://github.com/leynos/example.git")
    put(tmp_path, "README.md", "hello\n")
    assert profile(tmp_path) == set()
