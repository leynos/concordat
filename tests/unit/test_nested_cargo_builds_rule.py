"""Audit why tests spend a real compile, through the real policy and reader.

Each case is a small synthetic checkout. The harness-only case reproduces the
shape of the test leynos/rstest-bdd#989 deleted: a function that writes a
scratch crate into a temporary directory, builds it with `cargo`, and feeds the
result to the repository's own counter and writer.
"""

from __future__ import annotations

import typing as typ

import pygit2
import pytest

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
    std::process::Command::new("cargo").arg(subcommand()).status().unwrap();
}
"""

RUSTC_TEST = """\
#[test]
fn compiles_a_snippet() {
    std::process::Command::new("rustc").arg("snippet.rs").status().unwrap();
}
"""

SHELL_TEST = """\
#[test]
fn runs_a_script() {
    std::process::Command::new("sh").args(["-c", "cargo build"]).status().unwrap();
}
"""

ONE_LINE_TEST = """\
pub fn builds() { std::process::Command::new("cargo").arg("build").status().unwrap(); }
"""

COMMENT_ONLY_TEST = """\
#[test]
fn a_test_that_mentions_the_harness() {
    // Cargo.toml would live in a tempdir, and write it, but this only reads one.
    let compiled = nested_cargo::count_compiling(b"");
    std::process::Command::new("cargo").arg("build").status().unwrap();
    assert_eq!(compiled, 0);
}
"""

IMPORT_ONLY_TEST = """\
use cargo_metadata::MetadataCommand;

#[test]
fn reads_the_graph() {
    let _ = MetadataCommand::new().exec().unwrap();
}
"""

RUSTDOC_SHELL_TEST = """\
#[test]
fn documents_through_a_shell() {
    std::process::Command::new("sh")
        .args(["-c", "rustdoc src/lib.rs"])
        .status()
        .unwrap();
}
"""

READ_ONLY_TEST = """\
#[test]
fn reads_a_manifest_and_writes_a_log() {
    let scratch = tempfile::tempdir().unwrap();
    let text = std::fs::read_to_string(scratch.path().join("Cargo.toml")).unwrap();
    std::fs::write(scratch.path().join("log.txt"), text).unwrap();
    std::process::Command::new("cargo").current_dir(scratch.path()).arg("build").status().unwrap();
    let _ = nested_cargo::count_compiling(b"");
}
"""

CONTENT_ONLY_TEST = """\
#[test]
fn logs_a_name_and_builds_another_crate() {
    let scratch = tempfile::tempdir().unwrap();
    std::fs::write(scratch.path().join("log.txt"), "Cargo.toml").unwrap();
    std::process::Command::new("cargo").current_dir("elsewhere").arg("build").status().unwrap();
    let _ = nested_cargo::count_compiling(b"");
}
"""

SUPPORT_USER_ONE = "mod support;\n"

LOCAL_IMPORT_TEST = """\
#[test]
fn only_imports() {
    use cargo_metadata::MetadataCommand;
}
"""

TWO_DIRECTORIES_TEST = """\
#[test]
fn writes_one_crate_and_builds_another() {
    let first = tempfile::tempdir().unwrap();
    let second = tempfile::tempdir().unwrap();
    std::fs::write(first.path().join("Cargo.toml"), "[package]").unwrap();
    std::process::Command::new("cargo").current_dir(second.path()).arg("build").status().unwrap();
    let _ = nested_cargo::count_compiling(b"");
}
"""

GROUPED_IMPORT_TEST = """\
#[test]
fn only_imports() {
    use cargo_metadata::{MetadataCommand};
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


def entry(path: str, covered_by: str, subject: str = "product-cli") -> str:
    """Return a declaration body for *path*, with no function."""
    return f'path = "{path}"\nsubject = "{subject}"\ncovered_by = "{covered_by}"'


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


def test_a_nextest_setup_script_covers_a_declared_builder(
    tmp_path: pathlib.Path,
) -> None:
    """The `nextest-setup` form resolves when a setup rule selects the binary."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    put(root, ".config/nextest.toml", SETUP_SCRIPT)
    declare(
        root,
        entry("tests/cli.rs", "nextest-setup"),
    )
    assert profile(root) == set()


def test_nextest_setup_without_a_script_is_nc013(tmp_path: pathlib.Path) -> None:
    """Declaring a setup script that the repository does not have is NC-013."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    declare(
        root,
        entry("tests/cli.rs", "nextest-setup"),
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
        entry("tests/cli.rs", "somehow"),
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
    assert profile(root) == {("EN-001", "tests/shell.rs")}


def test_a_version_probe_is_not_inventoried(tmp_path: pathlib.Path) -> None:
    """`cargo --version` compiles nothing, so it needs no declaration."""
    root = checkout(tmp_path, {"tests/probe.rs": PROBE_TEST})
    assert profile(root) == set()


def test_a_stale_declaration_is_nc011(tmp_path: pathlib.Path) -> None:
    """A declaration with no builder behind it is reported at its file."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    declare(
        root,
        entry("tests/gone.rs", "nextest-setup"),
        entry("tests/cli.rs", "nextest-setup"),
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


def test_a_direct_rustc_spawn_is_inventoried(tmp_path: pathlib.Path) -> None:
    """`rustc` compiles whether or not the test sets a Cargo target."""
    root = checkout(tmp_path, {"tests/snippet.rs": RUSTC_TEST})
    assert profile(root) == {("NC-011", "tests/snippet.rs")}


def test_a_shell_that_may_run_cargo_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """`sh -c "cargo build"` hides what it builds, so it is EN-001."""
    root = checkout(tmp_path, {"tests/script.rs": SHELL_TEST})
    assert profile(root) == {("EN-001", "tests/script.rs")}


def test_a_declaration_does_not_make_an_unreadable_spawn_readable(
    tmp_path: pathlib.Path,
) -> None:
    """A declared builder with an unreadable spawn is still indeterminate."""
    root = checkout(tmp_path, {"tests/script.rs": SHELL_TEST})
    declare(
        root,
        entry("tests/script.rs", "nextest-setup"),
    )
    put(root, ".config/nextest.toml", SETUP_SCRIPT)
    assert ("EN-001", "tests/script.rs") in profile(root)


def test_a_one_line_function_is_named_by_its_spawn(tmp_path: pathlib.Path) -> None:
    """The function is found from the spawn, not from the start of its line."""
    root = checkout(tmp_path, {"tests/support.rs": ONE_LINE_TEST, "Makefile": MAKEFILE})
    declare(
        root,
        'path = "tests/support.rs"\nfunction = "builds"\nsubject = "product-cli"\n'
        'covered_by = "make:prepare-nested"',
    )
    assert profile(root) == set()


def test_a_manifest_name_in_a_comment_is_not_a_scratch_crate(
    tmp_path: pathlib.Path,
) -> None:
    """NC-012 needs a real write of a manifest, not a mention of one."""
    root = checkout(tmp_path, {"tests/mention.rs": COMMENT_ONLY_TEST})
    assert profile(root) == {("NC-011", "tests/mention.rs")}


def test_a_metadata_import_outside_a_function_is_not_a_builder(
    tmp_path: pathlib.Path,
) -> None:
    """Only the function that uses `MetadataCommand` is declared."""
    root = checkout(tmp_path, {"tests/graph.rs": IMPORT_ONLY_TEST})
    declare(
        root,
        'path = "tests/graph.rs"\nfunction = "reads_the_graph"\n'
        'subject = "dependency-graph"\ncovered_by = "nextest-setup"',
    )
    put(root, ".config/nextest.toml", SETUP_SCRIPT)
    assert profile(root) == set()


def test_a_variable_prerequisite_leaves_an_unfound_goal_unreadable(
    tmp_path: pathlib.Path,
) -> None:
    """A prerequisite the reader cannot expand may be the declared goal."""
    opaque = "test: $(PREPARE)\n\tcargo nextest run\n"
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST, "Makefile": opaque})
    declare(
        root,
        entry("tests/cli.rs", "make:warm"),
    )
    assert profile(root) == {("EN-001", "tests/cli.rs")}


def test_a_package_selector_covers_a_test_binary_of_that_package(
    tmp_path: pathlib.Path,
) -> None:
    """The owning package is passed to nextest's `package(..)` selector."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST})
    put(
        root,
        ".config/nextest.toml",
        SETUP_SCRIPT.replace('filter = "all()"', 'filter = "package(=example)"'),
    )
    declare(
        root,
        entry("tests/cli.rs", "nextest-setup"),
    )
    assert profile(root) == set()


def test_a_shell_wrapped_rustdoc_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """`sh -c "rustdoc ..."` hides a compile as `sh -c "rustc ..."` does."""
    root = checkout(tmp_path, {"tests/doc.rs": RUSTDOC_SHELL_TEST})
    assert profile(root) == {("EN-001", "tests/doc.rs")}


def test_a_test_target_name_selects_the_binary_not_the_file_name(
    tmp_path: pathlib.Path,
) -> None:
    """A `[[test]]` table renames the binary, and nextest selects that name."""
    manifest = CARGO_TOML + '\n[[test]]\nname = "current"\npath = "tests/old.rs"\n'
    setup = SETUP_SCRIPT.replace("all()", "binary(=current)")
    root = checkout(tmp_path, {"tests/old.rs": PLUGIN_TEST})
    put(root, "Cargo.toml", manifest)
    put(root, ".config/nextest.toml", setup)
    declare(root, entry("tests/old.rs", "nextest-setup"))
    assert profile(root) == set()
    put(root, ".config/nextest.toml", SETUP_SCRIPT.replace("all()", "binary(=old)"))
    assert profile(root) == {("NC-013", "tests/old.rs")}


def test_a_function_that_reads_a_manifest_is_not_a_scratch_crate(
    tmp_path: pathlib.Path,
) -> None:
    """NC-012 needs the manifest itself written, not an unrelated write."""
    root = checkout(tmp_path, {"tests/read.rs": READ_ONLY_TEST})
    assert profile(root) == {("NC-011", "tests/read.rs")}


def test_a_manifest_name_in_the_content_of_a_write_is_not_a_scratch_crate(
    tmp_path: pathlib.Path,
) -> None:
    """The write's destination must be the manifest, not its text."""
    root = checkout(tmp_path, {"tests/content.rs": CONTENT_ONLY_TEST})
    assert profile(root) == {("NC-011", "tests/content.rs")}


def test_a_shared_support_file_needs_every_owning_binary_covered(
    tmp_path: pathlib.Path,
) -> None:
    """A builder in a module two binaries include is covered only if both are."""
    root = checkout(
        tmp_path,
        {
            "tests/cli.rs": SUPPORT_USER_ONE,
            "tests/other.rs": SUPPORT_USER_ONE,
            "tests/support/mod.rs": PLUGIN_TEST,
        },
    )
    put(root, ".config/nextest.toml", SETUP_SCRIPT.replace("all()", "binary(=cli)"))
    declare(root, entry("tests/support/mod.rs", "nextest-setup"))
    assert ("NC-013", "tests/support/mod.rs") in profile(root)
    put(
        root,
        ".config/nextest.toml",
        SETUP_SCRIPT.replace("all()", "binary(=cli) | binary(=other)"),
    )
    assert profile(root) == set()


def test_a_function_local_metadata_import_is_not_a_builder(
    tmp_path: pathlib.Path,
) -> None:
    """A `use` of `MetadataCommand` inside a function owns no query."""
    root = checkout(tmp_path, {"tests/graph.rs": LOCAL_IMPORT_TEST})
    assert profile(root) == set()


def test_a_compiler_named_by_path_is_inventoried(tmp_path: pathlib.Path) -> None:
    """`/opt/rust/bin/rustc` is still `rustc`."""
    text = RUSTC_TEST.replace('"rustc"', '"/opt/rust/bin/rustc"')
    root = checkout(tmp_path, {"tests/snippet.rs": text})
    assert profile(root) == {("NC-011", "tests/snippet.rs")}


def test_a_manifest_and_a_build_in_different_directories_is_not_a_scratch_crate(
    tmp_path: pathlib.Path,
) -> None:
    """The written manifest and the build must share one temporary root."""
    root = checkout(tmp_path, {"tests/two.rs": TWO_DIRECTORIES_TEST})
    assert profile(root) == {("NC-011", "tests/two.rs")}


def test_a_grouped_metadata_import_is_not_a_builder(tmp_path: pathlib.Path) -> None:
    """`use cargo_metadata::{MetadataCommand};` owns no query either."""
    root = checkout(tmp_path, {"tests/graph.rs": GROUPED_IMPORT_TEST})
    assert profile(root) == set()


WARM = "warmup:\n\ttrue\n"
NONE = set[tuple[str, str]]()
NC013 = {("NC-013", "tests/cli.rs")}


@pytest.mark.parametrize(
    ("makefile", "expected"),
    [
        pytest.param(
            "test:\n\tcargo nextest run\n\nwarmup:\n\ttrue\n", NC013, id="never-called"
        ),
        pytest.param(
            "test:\n\t$(MAKE) warmup\n\tcargo nextest run\n\n" + WARM,
            NONE,
            id="before-the-tests",
        ),
        pytest.param(
            "test:\n\tcargo nextest run\n\t$(MAKE) warmup\n\n" + WARM,
            NC013,
            id="after-the-tests",
        ),
        pytest.param(
            'test:\n\t@echo "make warmup"\n\tcargo nextest run\n\n' + WARM,
            NC013,
            id="quoted-echo",
        ),
        pytest.param(
            "test:\n\ttrue || $(MAKE) warmup\n\tcargo nextest run\n\n" + WARM,
            NC013,
            id="after-or",
        ),
        pytest.param(
            "test:\n\tfalse && $(MAKE) warmup; cargo nextest run\n\n" + WARM,
            NC013,
            id="behind-failing-and",
        ),
        pytest.param(
            "test:\n\ttrue || ($(MAKE) warmup)\n\tcargo nextest run\n\n" + WARM,
            NC013,
            id="subshell",
        ),
        pytest.param(
            "test:\n\t$(MAKE) one && $(MAKE) warmup && cargo nextest run\n\n"
            "one:\n\ttrue\n\n" + WARM,
            NONE,
            id="chain",
        ),
        pytest.param(
            "test:\n\t$(MAKE) run\n\t$(MAKE) warmup\n\nrun:\n\tcargo nextest run\n\n"
            + WARM,
            NC013,
            id="after-a-goal-that-tests",
        ),
        pytest.param(
            "test:\n\t$(MAKE) warmup\n\t$(MAKE) run\n\nrun:\n\tcargo nextest run\n\n"
            + WARM,
            NONE,
            id="before-a-goal-that-tests",
        ),
        pytest.param(
            "test: run\n\t$(MAKE) warmup\n\nrun:\n\tcargo nextest run\n\n" + WARM,
            NC013,
            id="after-a-prerequisite-that-tests",
        ),
        pytest.param(
            "test: warmup run\n\ttrue\n\nrun:\n\tcargo nextest run\n\n" + WARM,
            NONE,
            id="prerequisite-before-one-that-tests",
        ),
    ],
)
def test_a_make_warm_up_counts_only_when_it_runs_before_the_tests(
    tmp_path: pathlib.Path, makefile: str, expected: set[tuple[str, str]]
) -> None:
    """The Makefile's order decides whether `make:warmup` pays for the build."""
    root = checkout(tmp_path, {"tests/cli.rs": PLUGIN_TEST, "Makefile": makefile})
    declare(root, entry("tests/cli.rs", "make:warmup"))
    assert profile(root) == expected
