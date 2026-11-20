"""Audit the nextest configuration of compiler-spawning tests through real files.

The configurations mirror the corpus: rstest-bdd and netsuke after their fixes
(a serial group, timeouts and filters that name the spawning tests), catnap's
slow-timeout-only file, and the conservative cases that must stay quiet
(`cargo metadata`, Python tests, helpers).
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pygit2
import pytest

from concordat.rules import packages, runner
from concordat.rules.nested_cargo_nextest_envelope import (
    _predicate_arguments,
    build_nested_cargo_nextest_envelope,
)
from concordat.rules.nested_cargo_receivers import (
    _statement_end,
    call_chains,
    owns_chain,
)
from concordat.rules.nested_cargo_shadows import file_index
from concordat.rules.nested_cargo_statements import _closure_hiding
from concordat.rules.nested_cargo_syntax import closure_spans

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

RULE_ID = "nested-cargo-nextest"
BUILD_TEST = (
    "use std::process::Command;\n"
    "#[test]\nfn compiles() {\n"
    '    Command::new("cargo").args(["build", "--bin", "x"]).status().unwrap();\n'
    "}\n"
)
RUSTC_TEST = (
    "use std::process::Command;\n"
    "#[test]\nfn direct() {\n"
    '    Command::new("rustc").arg("fixture.rs").status().unwrap();\n}\n'
)
RUSTC_VERSION_TEST = RUSTC_TEST.replace('"fixture.rs"', '"--version"')
RUSTC_VERBOSE_VERSION_TEST = RUSTC_TEST.replace('"fixture.rs"', '"-vV"')
RUSTC_SHORT_VERSION_TEST = RUSTC_TEST.replace('.arg("fixture.rs")', '.args(["-V"])')
RUSTC_PRINT_TEST = RUSTC_TEST.replace('"fixture.rs"', '"--print=sysroot"')
RUSTC_VERSION_THEN_COMPILE_TEST = (
    "use std::process::Command;\n"
    "#[test]\nfn probe_then_compile() {\n"
    '    Command::new("rustc").arg("--version").status().unwrap();\n'
    '    Command::new("rustc").arg("fixture.rs").status().unwrap();\n}\n'
)
METADATA_TEST = (
    "use std::process::Command;\n"
    "#[test]\nfn reads() {\n"
    '    Command::new("cargo").args(["metadata"]).output().unwrap();\n}\n'
)
CLEAN = (
    "[test-groups.cargo-spawning]\nmax-threads = 1\n"
    '[profile.default]\nslow-timeout = { period = "60s", terminate-after = 5 }\n'
    'global-timeout = "30m"\n'
    '[[profile.default.overrides]]\nfilter = "binary(ui)"\n'
    'test-group = "cargo-spawning"\n'
)


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ids(root: pathlib.Path) -> set[str]:
    """Return the rule ids the real policy reports for a checkout."""
    return {finding.rule_id for finding in runner.run_rule(RULE_ID, root).findings}


@dataclasses.dataclass(frozen=True, slots=True)
class Case:
    """One source, one configuration and the findings they must draw."""

    source: str
    config: str | None
    expected: set[str]
    path: str = "tests/ui.rs"


NO_GROUP = CLEAN.replace("max-threads = 1", "max-threads = 4")
NO_SLOW = CLEAN.replace('slow-timeout = { period = "60s", terminate-after = 5 }\n', "")
NO_GLOBAL = CLEAN.replace('global-timeout = "30m"\n', "")
NOT_NAMED = CLEAN.replace("binary(ui)", "binary(other)")
CASES = {
    "bounded": Case(BUILD_TEST, CLEAN, set()),
    "no-configuration": Case(BUILD_TEST, None, {"NC-004"}),
    "no-serial-group": Case(BUILD_TEST, NO_GROUP, {"NC-004"}),
    "no-slow-timeout": Case(BUILD_TEST, NO_SLOW, {"NC-004"}),
    "no-global-timeout": Case(BUILD_TEST, NO_GLOBAL, {"NC-004"}),
    "binary-not-named": Case(BUILD_TEST, NOT_NAMED, {"NC-005"}),
    "test-named-instead-of-binary": Case(
        BUILD_TEST, CLEAN.replace("binary(ui)", "test(compiles)"), set()
    ),
    "direct-rustc-counts": Case(RUSTC_TEST, None, {"NC-004"}),
    "rustc-version-probe-does-not": Case(RUSTC_VERSION_TEST, None, set()),
    "rustc-verbose-version-probe-does-not": Case(
        RUSTC_VERBOSE_VERSION_TEST, None, set()
    ),
    "rustc-short-version-probe-does-not": Case(RUSTC_SHORT_VERSION_TEST, None, set()),
    "rustc-print-query-does-not": Case(RUSTC_PRINT_TEST, None, set()),
    "rustc-print-link-args-compiles": Case(
        RUSTC_TEST.replace(
            '.arg("fixture.rs")', '.args(["fixture.rs", "--print=link-args"])'
        ),
        None,
        {"NC-004"},
    ),
    "rustc-print-native-static-libs-compiles": Case(
        RUSTC_TEST.replace(
            '.arg("fixture.rs")', '.args(["fixture.rs", "--print=native-static-libs"])'
        ),
        None,
        {"NC-004"},
    ),
    "rustc-print-sysroot-split-args-is-a-query": Case(
        RUSTC_TEST.replace('.arg("fixture.rs")', '.args(["--print", "sysroot"])'),
        None,
        set(),
    ),
    "a-version-argument-of-another-command-does-not-excuse-the-compile": Case(
        "use std::process::Command;\n#[test]\nfn direct() {\n"
        '    let mut other = Command::new("echo");\n'
        '    Command::new("rustc").arg("fixture.rs").status().unwrap();\n'
        '    other.arg("--version");\n}\n',
        None,
        {"NC-004"},
    ),
    "a-flag-chosen-by-a-condition-is-not-a-known-probe": Case(
        "use std::process::Command;\n#[test]\nfn chosen() {\n"
        '    Command::new("rustc")\n'
        '        .arg(if false { "--version" } else { "fixture.rs" })\n'
        "        .status()\n        .unwrap();\n}\n",
        None,
        {"NC-004"},
    ),
    "a-probe-flag-after-another-statement-in-a-block-is-not-proof": Case(
        "use std::process::Command;\n#[test]\nfn maybe() {\n"
        '    let mut cmd = Command::new("rustc");\n'
        "    if query_only {\n"
        '        cmd.env("RUST_LOG", "info");\n'
        '        cmd.arg("--print=cfg");\n'
        "    }\n"
        '    cmd.arg("fixture.rs");\n'
        "    cmd.status().unwrap();\n}\n",
        None,
        {"NC-004"},
    ),
    "a-conditionally-added-probe-flag-is-not-proof": Case(
        "use std::process::Command;\n#[test]\nfn maybe() {\n"
        '    let mut cmd = Command::new("rustc");\n'
        '    if query_only { cmd.arg("--print=cfg"); }\n'
        '    cmd.arg("fixture.rs");\n'
        "    cmd.status().unwrap();\n}\n",
        None,
        {"NC-004"},
    ),
    "a-launch-inside-control-flow-ends-the-arguments": Case(
        "use std::process::Command;\n#[test]\nfn guarded() {\n"
        '    let mut cmd = Command::new("rustc");\n'
        '    cmd.arg("fixture.rs");\n'
        "    if true { cmd.status().unwrap(); }\n"
        '    cmd.arg("--version");\n}\n',
        None,
        {"NC-004"},
    ),
    "an-argument-added-after-the-launch-does-not-excuse-the-compile": Case(
        "use std::process::Command;\n#[test]\nfn reuse() {\n"
        '    let mut command = Command::new("rustc");\n'
        '    command.arg("fixture.rs");\n'
        "    command.status().unwrap();\n"
        '    command.arg("--version");\n}\n',
        None,
        {"NC-004"},
    ),
    "a-bound-command-keeps-its-own-version-argument": Case(
        "use std::process::Command;\n#[test]\nfn probe() {\n"
        '    let mut command = Command::new("rustc");\n'
        '    command.arg("--version");\n'
        "    command.status().unwrap();\n}\n",
        None,
        set(),
    ),
    "a-compile-followed-by-a-version-assertion-counts": Case(
        RUSTC_TEST.replace("}\n", '    assert_eq!(actual, "--version");\n}\n'),
        None,
        {"NC-004"},
    ),
    "a-compile-followed-by-a-version-comment-counts": Case(
        RUSTC_TEST.replace(
            "}\n", '    // Unlike "--version", this compiles a fixture.\n}\n'
        ),
        None,
        {"NC-004"},
    ),
    "rustc-version-probe-then-compile-counts": Case(
        RUSTC_VERSION_THEN_COMPILE_TEST, None, {"NC-004"}
    ),
    "cargo-metadata-does-not": Case(METADATA_TEST, None, set()),
    "helper-judged-at-repository-level": Case(
        BUILD_TEST, CLEAN.replace("binary(ui)", "binary(other)"), set(), "src/helper.rs"
    ),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_layouts_draw_the_expected_findings(tmp_path: pathlib.Path, case: Case) -> None:
    """The real policy reports each layout's findings and nothing else."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    source = case.source
    if case.path.startswith("src/"):
        source = "#[cfg(test)]\nmod tests {\n" + source + "}\n"
    put(tmp_path, case.path, source)
    if case.config is not None:
        put(tmp_path, ".config/nextest.toml", case.config)
    assert ids(tmp_path) == case.expected


def test_no_spawner_needs_no_configuration(tmp_path: pathlib.Path) -> None:
    """A repository whose tests start no compiler is compliant."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/plain.rs", "#[test]\nfn adds() { assert_eq!(1 + 1, 2); }\n")
    assert ids(tmp_path) == set()


def test_a_spawn_outside_test_code_is_not_counted(tmp_path: pathlib.Path) -> None:
    """A build script that runs cargo is not a test."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "build.rs", BUILD_TEST)
    put(tmp_path, "src/lib.rs", BUILD_TEST.replace("#[test]", ""))
    assert ids(tmp_path) == set()


def test_a_python_test_is_left_to_its_own_runner(tmp_path: pathlib.Path) -> None:
    """Pytest bounds a Python test; nextest configuration is not demanded for it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/test_build.py", 'subprocess.run(["cargo", "build"])\n')
    assert ids(tmp_path) == set()


def test_an_unreadable_configuration_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """A configuration that is not TOML is a fact, never a pass."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", "[profile\nbroken")
    assert runner.run_rule(RULE_ID, tmp_path).verdict == "indeterminate"


def test_the_envelope_records_what_the_configuration_provides(
    tmp_path: pathlib.Path,
) -> None:
    """The facts name the serial groups, the overrides and both timeouts."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", CLEAN)
    envelope = build_nested_cargo_nextest_envelope(tmp_path)
    assert envelope["nextest"] == {
        "state": "present",
        "error": None,
        "serial_groups": ["cargo-spawning"],
        "grouped_override": True,
        "slow_timeout": True,
        "global_timeout": True,
    }
    (spawner,) = envelope["spawners"]
    assert (spawner["path"], spawner["kind"], spawner["binary"]) == (
        "tests/ui.rs",
        "cargo",
        "ui",
    )
    assert spawner["named"] is True


def test_a_slow_timeout_in_an_override_counts(tmp_path: pathlib.Path) -> None:
    """Netsuke sets slow-timeout on overrides, not on the profile."""
    config = NO_SLOW + (
        '[[profile.default.overrides]]\nfilter = "binary(ui)"\n'
        'slow-timeout = { period = "60s", terminate-after = 15 }\n'
    )
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", config)
    assert ids(tmp_path) == set()


def test_a_global_timeout_in_another_profile_counts(tmp_path: pathlib.Path) -> None:
    """Netsuke sets global-timeout in profile.ci only."""
    config = NO_GLOBAL + '[profile.ci]\nglobal-timeout = "16m"\n'
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", config)
    assert ids(tmp_path) == set()


def result_with(
    monkeypatch: pytest.MonkeyPatch,
    root: pathlib.Path,
    parameters: cabc.Mapping[str, object],
) -> runner.RuleRunResult:
    """Run the rule as if its manifest declared *parameters*.

    Returns
    -------
    runner.RuleRunResult
        The result the package reports under *parameters*.
    """
    monkeypatch.setattr(runner, "_rule_parameters", lambda _rule_dir: dict(parameters))
    return runner.run_rule(RULE_ID, root)


def unbounded_checkout(root: pathlib.Path) -> None:
    """Write a repository whose cargo-spawning test has no nextest configuration."""
    pygit2.init_repository(str(root)).remotes.create(
        "origin", "https://github.com/leynos/example.git"
    )
    put(root, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(root, "tests/ui.rs", BUILD_TEST)


def exemption(**overrides: str) -> dict[str, str]:
    """Return a complete exemption for the spawner, with overrides."""
    return {
        "repository": "leynos/example",
        "path": "tests/ui.rs",
        "rationale": "runs a prebuilt compiler fixture bounded by the job",
        "review_trigger": "review when the fixture starts compiling",
    } | overrides


def test_a_finding_is_reported_but_does_not_block(tmp_path: pathlib.Path) -> None:
    """The package ships at warning, so the estate can be surveyed first."""
    unbounded_checkout(tmp_path)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {f.severity for f in result.findings} == {"warning"}
    assert result.verdict == "noncompliant"
    assert result.exit_code == 0
    assert result.blocking is False


def test_the_severity_parameter_restores_blocking(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Promotion is a parameter change, not a policy edit."""
    unbounded_checkout(tmp_path)
    result = result_with(monkeypatch, tmp_path, {"severity": "error", "exemptions": []})
    assert {f.severity for f in result.findings} == {"error"}
    assert result.exit_code == 1


def test_a_complete_exemption_takes_the_spawner_out(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repository, source, rationale and review trigger suppress both checks."""
    unbounded_checkout(tmp_path)
    result = result_with(monkeypatch, tmp_path, {"exemptions": [exemption()]})
    assert not result.findings


@pytest.mark.parametrize(
    "blank", [{"rationale": " "}, {"review_trigger": ""}], ids=["rationale", "trigger"]
)
def test_an_exemption_without_a_reviewed_record_suppresses_nothing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, blank: dict[str, str]
) -> None:
    """A blank rationale or review trigger is not a record."""
    unbounded_checkout(tmp_path)
    result = result_with(monkeypatch, tmp_path, {"exemptions": [exemption(**blank)]})
    assert {f.rule_id for f in result.findings} == {"NC-004"}


def test_an_exemption_does_not_travel_to_another_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same source under another repository's name is audited."""
    unbounded_checkout(tmp_path)
    result = result_with(
        monkeypatch, tmp_path, {"exemptions": [exemption(repository="leynos/other")]}
    )
    assert {f.rule_id for f in result.findings} == {"NC-004"}


@pytest.mark.parametrize("key", ["severity", "exemptions"])
def test_the_manifest_declares_each_parameter_default(key: str) -> None:
    """A parameter the schema declares must have a default, or it is inert."""
    defaults = packages.rule_parameters(packages.rule_package_dir(RULE_ID))
    assert key in defaults


@pytest.mark.parametrize(
    "path",
    [
        "tests/kani_cfg_ui_tests.rs",
        "tests/ninja_gen_escape_ui_tests.rs",
        "tests/packaging_smoke_tests.rs",
    ],
)
def test_the_shipped_exemptions_apply_to_netsuke_only(
    tmp_path: pathlib.Path, path: str
) -> None:
    """Netsuke's single-file compile tests are exempt by default, but only at home."""
    for name, expected in (("netsuke", set()), ("other", {"NC-004"})):
        root = tmp_path / name
        pygit2.init_repository(str(root)).remotes.create(
            "origin", f"https://github.com/leynos/{name}.git"
        )
        put(root, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
        put(root, path, RUSTC_TEST)
        assert ids(root) == expected, name


def test_the_shared_actions_toy_app_is_exempt_at_home_only(
    tmp_path: pathlib.Path,
) -> None:
    """The fixture crate's own build test is exempt by default, but only there."""
    path = "rust-toy-app/tests/manpage_generation.rs"
    for name, expected in (("shared-actions", set()), ("other", {"NC-004"})):
        root = tmp_path / name
        pygit2.init_repository(str(root)).remotes.create(
            "origin", f"https://github.com/leynos/{name}.git"
        )
        put(root, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
        put(root, path, BUILD_TEST)
        assert ids(root) == expected, name


MIXED_TEST = (
    "use std::process::Command;\n"
    "pub fn build() {\n"
    '    Command::new("cargo").arg("build").status().unwrap();\n'
    "}\n"
    "#[cfg(test)]\nmod tests {\n    use super::*;\n"
    "    #[test]\n    fn metadata() {\n"
    '        Command::new("cargo").arg("metadata").output().unwrap();\n'
    "    }\n}\n"
)
TWO_COMMANDS_TEST = (
    "use std::process::Command;\n"
    "#[test]\nfn reads() {\n"
    '    Command::new("cargo").args(["metadata"]).output().unwrap();\n'
    '    Command::new("git").args(["build"]).output().unwrap();\n}\n'
)


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("src/lib.rs", MIXED_TEST),
        ("tests/reads.rs", TWO_COMMANDS_TEST),
    ],
    ids=["production-build-beside-test-metadata", "build-argument-of-another-command"],
)
def test_a_compiling_argument_elsewhere_does_not_make_metadata_a_spawner(
    tmp_path: pathlib.Path, path: str, source: str
) -> None:
    """The compiling argument must belong to the same command as the cargo spawn."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, path, source)
    assert ids(tmp_path) == set()


HANDED_ON = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add_build_request(&mut command);\n}\n"
    "fn add_build_request(command: &mut Command) {\n"
    '    command.arg("build").status().unwrap();\n}\n'
)
HANDED_ON_QUERY = HANDED_ON.replace('"build"', '"metadata"')


def test_a_bound_command_handed_to_a_helper_is_read_with_the_helper(
    tmp_path: pathlib.Path,
) -> None:
    """The callee that adds `build` still makes the spawn compiler work."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HANDED_ON)
    assert ids(tmp_path) == {"NC-004"}


def test_a_bound_command_handed_to_a_query_helper_is_not_compiler_work(
    tmp_path: pathlib.Path,
) -> None:
    """The same shape with `metadata` compiles nothing."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HANDED_ON_QUERY)
    assert ids(tmp_path) == set()


HANDED_ON_WRAPPED = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add_build_request(\n        &mut command,\n    );\n}\n"
    "fn add_build_request(command: &mut Command) {\n"
    '    command.arg("build").status().unwrap();\n}\n'
)


def test_a_helper_call_that_spans_lines_is_still_read_with_the_helper(
    tmp_path: pathlib.Path,
) -> None:
    """A wrapped call with a trailing comma still hands on the bound command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HANDED_ON_WRAPPED)
    assert ids(tmp_path) == {"NC-004"}


DUPLICATE_HELPER = (
    "use std::process::Command;\n"
    "mod b {\n"
    "    use super::Command;\n"
    "    pub fn add_build(command: &mut Command) {\n"
    '        command.arg("build").status().unwrap();\n'
    "    }\n}\n"
    "mod a {\n"
    "    use super::Command;\n"
    "    fn add_build(command: &mut Command) {\n"
    '        command.arg("metadata").output().unwrap();\n'
    "    }\n"
    "    #[test]\n"
    "    fn reads() {\n"
    '        let mut command = Command::new(env!("CARGO"));\n'
    "        add_build(&mut command);\n"
    "        command.status().unwrap();\n"
    "    }\n}\n"
)


def test_a_helper_name_defined_twice_is_not_read_from_another_module(
    tmp_path: pathlib.Path,
) -> None:
    """A duplicated helper name declines rather than borrowing the other body."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", DUPLICATE_HELPER)
    assert ids(tmp_path) == set()


RECEIVER_BLIND_GIT = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add_metadata_request(&mut command);\n}\n"
    "fn add_metadata_request(command: &mut Command) {\n"
    '    Command::new("git").arg("build").status().unwrap();\n'
    '    command.arg("metadata").output().unwrap();\n}\n'
)


def test_an_unrelated_command_in_a_helper_is_not_the_bound_command(
    tmp_path: pathlib.Path,
) -> None:
    """A git command that also takes `build` is not the cargo command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", RECEIVER_BLIND_GIT)
    assert ids(tmp_path) == set()


SECOND_COMMAND_PARAMETER = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut other = Command::new("git");\n'
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add(&mut other, &mut command);\n}\n"
    "fn add(first: &mut Command, second: &mut Command) {\n"
    '    first.arg("build").status().unwrap();\n'
    '    second.arg("metadata").output().unwrap();\n}\n'
)


def test_a_helper_argument_is_read_from_the_parameter_at_the_bound_position(
    tmp_path: pathlib.Path,
) -> None:
    """A second command parameter's `build` is not the bound command's argument."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SECOND_COMMAND_PARAMETER)
    assert ids(tmp_path) == set()


RENAMED_HELPER_PARAMETER = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut cmd = Command::new(env!("CARGO"));\n'
    "    add_build(&mut cmd);\n}\n"
    "fn add_build(command: &mut Command) {\n"
    '    command.arg("build").status().unwrap();\n}\n'
)


def test_a_helper_parameter_named_differently_still_reads_its_arguments(
    tmp_path: pathlib.Path,
) -> None:
    """The formal parameter name, not the caller's binding, selects statements."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", RENAMED_HELPER_PARAMETER)
    assert ids(tmp_path) == {"NC-004"}


PROBE_AFTER_LAUNCH = (
    "use std::process::Command;\n"
    "fn compile_then_probe() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    add_build(&mut command);\n}\n"
    "fn add_build(command: &mut Command) {\n"
    '    command.arg("input.rs").status().unwrap();\n'
    '    command.arg("--version").status().unwrap();\n}\n'
)


def test_a_probe_after_a_helper_launch_does_not_hide_the_compile(
    tmp_path: pathlib.Path,
) -> None:
    """A version probe in a later helper statement is not the first launch."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PROBE_AFTER_LAUNCH)
    assert ids(tmp_path) == {"NC-004"}


CONDITIONAL_PROBE = (
    "use std::process::Command;\n"
    "fn compile_after_check() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    add_build(&mut command, false);\n}\n"
    "fn add_build(command: &mut Command, query: bool) {\n"
    "    if query {\n"
    '        command.arg("--version");\n'
    "    }\n"
    '    command.arg("input.rs").status().unwrap();\n}\n'
)


def test_a_probe_inside_a_helper_branch_does_not_hide_the_compile(
    tmp_path: pathlib.Path,
) -> None:
    """A flag added under an `if` is not an unconditional probe."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITIONAL_PROBE)
    assert ids(tmp_path) == {"NC-004"}


LAUNCH_AFTER_BLOCK = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add_build(&mut command);\n}\n"
    "fn add_build(command: &mut Command) {\n"
    "    if true {\n"
    "        let marker = ();\n"
    "    }\n"
    '    command.arg("build").status().unwrap();\n}\n'
)


def test_a_launch_after_a_helper_block_is_not_lost(
    tmp_path: pathlib.Path,
) -> None:
    """A statement that follows a closing brace still adds `build`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", LAUNCH_AFTER_BLOCK)
    assert ids(tmp_path) == {"NC-004"}


SHADOWED_PARAMETER = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add_metadata(&mut command);\n}\n"
    "fn add_metadata(command: &mut Command) {\n"
    '    command.arg("metadata").output().unwrap();\n'
    "    let mut command = clone_command();\n"
    '    command.arg("build").status().unwrap();\n}\n'
)


def test_a_local_declaration_that_shadows_the_parameter_is_not_the_command(
    tmp_path: pathlib.Path,
) -> None:
    """A `build` added after the parameter is rebound belongs to the new value."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SHADOWED_PARAMETER)
    assert ids(tmp_path) == set()


GENERIC_ARROW_PARAMETERS = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut git = Command::new("git");\n'
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add(noop, &mut git, &mut command);\n}\n"
    "fn add(hook: Result<fn() -> (), ()>, first: &mut Command, "
    "second: &mut Command) {\n"
    '    first.arg("build").status().unwrap();\n'
    '    second.arg("metadata").output().unwrap();\n'
    "    let _ = hook;\n}\n"
)


def test_a_comma_after_a_function_pointer_return_keeps_parameter_positions(
    tmp_path: pathlib.Path,
) -> None:
    """The arrow's `>` does not close `Result`, so index two is still `second`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", GENERIC_ARROW_PARAMETERS)
    assert ids(tmp_path) == set()


GENERIC_ARROW_SIGNATURE = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add(&mut command, noop);\n}\n"
    "fn add<F: Fn() -> ()>(command: &mut Command, hook: F) {\n"
    '    command.arg("build").status().unwrap();\n'
    "    let _ = hook;\n}\n"
)


def test_a_generic_return_arrow_does_not_hide_the_parameter_list(
    tmp_path: pathlib.Path,
) -> None:
    """The `()` of `Fn() -> ()` is a return type, not the parameter list."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", GENERIC_ARROW_SIGNATURE)
    assert ids(tmp_path) == {"NC-004"}


HELPER_PROBE_BEFORE_LAUNCH = (
    "use std::process::Command;\n"
    "fn probe_then_compile() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    add_version(&mut command);\n"
    '    command.arg("input.rs").status().unwrap();\n}\n'
    "fn add_version(command: &mut Command) {\n"
    '    command.arg("--version");\n}\n'
)


def test_a_helper_probe_is_applied_before_the_caller_launch(
    tmp_path: pathlib.Path,
) -> None:
    """A version flag added by the helper is still the command's argument."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELPER_PROBE_BEFORE_LAUNCH)
    assert ids(tmp_path) == set()


HELPER_COMPILE_BEFORE_CALLER_PROBE = (
    "use std::process::Command;\n"
    "fn compile_then_probe() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    add_input(&mut command);\n"
    '    command.arg("--version").status().unwrap();\n}\n'
    "fn add_input(command: &mut Command) {\n"
    '    command.arg("input.rs").status().unwrap();\n}\n'
)


def test_a_helper_launch_still_ends_arguments_before_a_later_probe(
    tmp_path: pathlib.Path,
) -> None:
    """The caller's probe follows the helper's launch, so it does not hide it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELPER_COMPILE_BEFORE_CALLER_PROBE)
    assert ids(tmp_path) == {"NC-004"}


SHADOWED_CALLEE = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    '    let configure = |cmd: &mut Command| { cmd.arg("metadata"); };\n'
    "    configure(&mut command);\n"
    "    command.status().unwrap();\n}\n"
    "fn configure(command: &mut Command) {\n"
    '    command.arg("build").status().unwrap();\n}\n'
)


def test_a_closure_that_shadows_the_helper_name_is_not_the_free_function(
    tmp_path: pathlib.Path,
) -> None:
    """A local closure named `configure` is not the `fn configure` beside it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SHADOWED_CALLEE)
    assert ids(tmp_path) == set()


PROBE_THROUGH_RENAMED_PARAMETER = (
    "use std::process::Command;\n"
    "fn probe_toolchain() {\n"
    '    let mut cmd = Command::new(env!("RUSTC"));\n'
    "    add_version(&mut cmd);\n"
    "    cmd.status().unwrap();\n}\n"
    "fn add_version(command: &mut Command) {\n"
    '    command.arg("--version");\n}\n'
)


def test_a_helper_probe_counts_against_the_callers_binding(
    tmp_path: pathlib.Path,
) -> None:
    """The helper's `command` and the caller's `cmd` are the same command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PROBE_THROUGH_RENAMED_PARAMETER)
    assert ids(tmp_path) == set()


RECEIVER_AFTER_IF = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add_build(&mut command, true);\n}\n"
    "fn add_build(command: &mut Command, enabled: bool) {\n"
    '    if enabled { command.arg("build").status().unwrap(); }\n'
    "    let _ = enabled;\n}\n"
)


def test_a_helper_argument_after_a_block_opener_is_retained(
    tmp_path: pathlib.Path,
) -> None:
    """`if enabled { command.arg("build") ... }` still adds the argument."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", RECEIVER_AFTER_IF)
    assert ids(tmp_path) == {"NC-004"}


CARGO_LAUNCH_BEFORE_LATER_ARGUMENT = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    probe(&mut command);\n"
    "    add_build(&mut command);\n}\n"
    "fn probe(command: &mut Command) {\n"
    '    command.arg("--version").status().unwrap();\n}\n'
    "fn add_build(command: &mut Command) {\n"
    '    command.arg("build");\n}\n'
)


def test_a_compile_argument_after_a_helper_launch_is_not_the_command(
    tmp_path: pathlib.Path,
) -> None:
    """`build` is added after `cargo --version` has run, so it compiles nothing."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CARGO_LAUNCH_BEFORE_LATER_ARGUMENT)
    assert ids(tmp_path) == set()


LOOP_SHADOWS_HELPER = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    for add_build in [noop] {\n"
    "        add_build(&mut command);\n"
    "    }\n"
    "    command.status().unwrap();\n}\n"
    "fn add_build(command: &mut Command) {\n"
    '    command.arg("build").status().unwrap();\n}\n'
)


def test_a_loop_binding_that_shadows_the_helper_name_is_not_the_function(
    tmp_path: pathlib.Path,
) -> None:
    """A loop variable named `add_build` is not the `fn add_build` beside it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", LOOP_SHADOWS_HELPER)
    assert ids(tmp_path) == set()


PROBE_AFTER_CLOSED_BLOCK = (
    "use std::process::Command;\n"
    "fn probe_toolchain() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    add_version(&mut command);\n"
    "    command.status().unwrap();\n}\n"
    "fn add_version(command: &mut Command) {\n"
    '    if enabled() { setup(); } command.arg("--version");\n}\n'
)


def test_a_probe_after_a_closed_block_is_unconditional(
    tmp_path: pathlib.Path,
) -> None:
    """A `--version` added after an `if` block closes runs on every path."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PROBE_AFTER_CLOSED_BLOCK)
    assert ids(tmp_path) == set()


CALLER_PARAMETER_SHADOWS_HELPER = (
    "use std::process::Command;\n"
    "fn run(configure: fn(&mut Command)) {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    configure(&mut command);\n"
    '    command.arg("input.rs").status().unwrap();\n}\n'
    "fn configure(command: &mut Command) {\n"
    '    command.arg("--version");\n}\n'
)


def test_a_caller_parameter_that_shadows_the_helper_name_is_not_the_function(
    tmp_path: pathlib.Path,
) -> None:
    """A callback parameter named `configure` is not the `fn configure`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CALLER_PARAMETER_SHADOWS_HELPER)
    assert ids(tmp_path) == {"NC-004"}


FIRST_ITEM_SHADOWS_HELPER = CALLER_PARAMETER_SHADOWS_HELPER.removeprefix(
    "use std::process::Command;\n"
)


def test_a_parameter_shadows_the_helper_before_any_leading_item(
    tmp_path: pathlib.Path,
) -> None:
    """The caller is found even when its `fn` is the file's first item."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", FIRST_ITEM_SHADOWS_HELPER)
    assert ids(tmp_path) == {"NC-004"}


CONST_GENERIC_RETURN = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    configure(&mut command);\n}\n"
    "fn configure(target: &mut Command) -> Tag<{ 1 + 1 }> {\n"
    '    target.arg("build").status().unwrap();\n'
    "    Tag\n}\n"
)


def test_a_const_generic_return_brace_is_not_the_helper_body(
    tmp_path: pathlib.Path,
) -> None:
    """The `{` of `Tag<{ 1 + 1 }>` is not the body that follows it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONST_GENERIC_RETURN)
    assert ids(tmp_path) == {"NC-004"}


QUALIFIED_CALL = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    support::configure(&mut command);\n"
    '    command.arg("input.rs").status().unwrap();\n}\n'
    "fn configure(command: &mut Command) {\n"
    '    command.arg("--version");\n}\n'
)


def test_a_qualified_call_is_not_the_same_file_function(tmp_path: pathlib.Path) -> None:
    """`support::configure` is not the free `fn configure` in this file."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", QUALIFIED_CALL)
    assert ids(tmp_path) == {"NC-004"}


BRACE_IN_LITERAL = (
    "use std::process::Command;\n"
    "fn probe_toolchain() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    add_version(&mut command);\n"
    "    command.status().unwrap();\n}\n"
    "fn add_version(command: &mut Command) {\n"
    '    command.env("KEY", "{");\n'
    '    command.arg("--version");\n}\n'
)


def test_a_brace_in_a_literal_does_not_make_a_probe_conditional(
    tmp_path: pathlib.Path,
) -> None:
    """The `{` in `env("KEY", "{")` opens no block, so the probe still counts."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", BRACE_IN_LITERAL)
    assert ids(tmp_path) == set()


CONST_COMPARISON_PARAMETER = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    "    let flag = Flag;\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    configure(flag, &mut command);\n}\n"
    "fn configure(_: Flag<{ 1 < 2 }>, target: &mut Command) {\n"
    '    target.arg("build").status().unwrap();\n}\n'
)


def test_a_comparison_in_a_const_generic_keeps_parameter_positions(
    tmp_path: pathlib.Path,
) -> None:
    """The `<` of `1 < 2` is not a generic opener, so `target` stays second."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONST_COMPARISON_PARAMETER)
    assert ids(tmp_path) == {"NC-004"}


SPACED_QUALIFIED_CALL = QUALIFIED_CALL.replace(
    "support::configure(", "support:: configure("
)


def test_a_qualified_call_with_a_space_after_the_path_is_not_the_function(
    tmp_path: pathlib.Path,
) -> None:
    """Rust allows whitespace after `::`, so `support:: configure` is still a path."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SPACED_QUALIFIED_CALL)
    assert ids(tmp_path) == {"NC-004"}


SPACED_METHOD_CALL = QUALIFIED_CALL.replace(
    "support::configure(", "harness . configure("
)


def test_a_method_call_with_a_space_before_the_name_is_not_the_function(
    tmp_path: pathlib.Path,
) -> None:
    """A receiver and a `.` name a method, however the call is spaced."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SPACED_METHOD_CALL)
    assert ids(tmp_path) == {"NC-004"}


COMMENT_MARKERS_IN_A_LITERAL = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "add_build(&mut command);\n}\n"
    "fn add_build(command: &mut Command) {\n"
    '    command.env("RUSTDOC_LINK", "https://r/a").arg("build").status().unwrap();\n'
    "}\n"
)


def test_a_comment_marker_in_a_string_is_not_a_comment(
    tmp_path: pathlib.Path,
) -> None:
    """A `//` inside a string does not blank the argument that follows it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", COMMENT_MARKERS_IN_A_LITERAL)
    assert ids(tmp_path) == {"NC-004"}


SHORT_CIRCUITED_PROBE = (
    "use std::process::Command;\n"
    "fn compile_after_check() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    add_version(&mut command, query_only);\n"
    '    command.arg("input.rs").status().unwrap();\n}\n'
    "fn add_version(command: &mut Command, query: bool) {\n"
    '    let _ = query && command.arg("--version").status().is_ok();\n'
    '    command.arg("input.rs");\n'
    "    let _ = query;\n}\n"
)
SHORT_CIRCUITED_PROBE_IF = SHORT_CIRCUITED_PROBE.replace(
    '    let _ = query && command.arg("--version").status().is_ok();\n',
    '    if query && command.arg("--version").status().is_ok() {}\n',
)


@pytest.mark.parametrize(
    "source",
    [SHORT_CIRCUITED_PROBE, SHORT_CIRCUITED_PROBE_IF],
    ids=["in-a-let", "in-an-if-condition"],
)
def test_a_probe_behind_a_short_circuit_is_not_unconditional(
    tmp_path: pathlib.Path, source: str
) -> None:
    """`query && command.arg("--version")` skips the flag when `query` is false."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", source)
    assert ids(tmp_path) == {"NC-004"}


UNFINISHED_HELPER_BLOCK = (
    "use std::process::Command;\n"
    "fn probe_toolchain() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    add_version(&mut command, query_only);\n"
    '    command.arg("--version");\n'
    "    command.status().unwrap();\n}\n"
    "fn add_version(command: &mut Command, query: bool) {\n"
    "    if query {\n"
    '        command.arg("--version");\n'
    '        let mut other = Command::new("git");\n'
    '        other.arg("status");\n'
    "    }\n"
    "    let _ = query;\n}\n"
)


def test_a_helper_block_that_no_statement_closes_does_not_hide_a_later_probe(
    tmp_path: pathlib.Path,
) -> None:
    """The helper's `if` ends with its body, so the caller's probe is unconditional."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", UNFINISHED_HELPER_BLOCK)
    assert ids(tmp_path) == set()


REASSIGNED_PARAMETER = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut git = Command::new("git");\n'
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add_metadata(&mut command, &mut git);\n}\n"
    "fn add_metadata<'a>(mut command: &'a mut Command, other: &'a mut Command) {\n"
    "    command = other;\n"
    '    command.arg("build").status().unwrap();\n}\n'
)


def test_an_argument_after_the_parameter_is_reassigned_is_not_the_command(
    tmp_path: pathlib.Path,
) -> None:
    """`command = ..` rebinds the parameter, so a later `build` is another command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", REASSIGNED_PARAMETER)
    assert ids(tmp_path) == set()


OTHER_RECEIVER_IN_METHOD_ARGUMENT = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add_metadata(&mut command, &mut other);\n}\n"
    "fn add_metadata(command: &mut Command, other: &mut Command) {\n"
    '    command.env("PROGRAM", other.arg("build").get_program());\n'
    '    command.arg("metadata").output().unwrap();\n}\n'
)


def test_an_argument_of_another_command_is_not_the_commands_argument(
    tmp_path: pathlib.Path,
) -> None:
    """`build` is added to `other`, which is only read, not the cargo command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", OTHER_RECEIVER_IN_METHOD_ARGUMENT)
    assert ids(tmp_path) == set()


SHIFTED_ARRAY_LENGTH = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    "    let mut flag = [0u8; 2];\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add(flag, &mut command);\n}\n"
    "fn add(flag: [u8; 4 >> 1], target: &mut Command) {\n"
    '    target.arg("build").status().unwrap();\n'
    "    let _ = flag;\n}\n"
)


def test_a_right_shift_in_an_array_length_keeps_parameter_positions(
    tmp_path: pathlib.Path,
) -> None:
    """The `>` of `4 >> 1` closes no bracket, so `target` stays second."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SHIFTED_ARRAY_LENGTH)
    assert ids(tmp_path) == {"NC-004"}


MARKED_SHIFTED_ARRAY_LENGTH = (
    "use std::process::Command;\n"
    "#[cfg(test)]\n"
    "fn marked(len: [u8; 4 >> 1]) {\n"
    '    Command::new("cargo").arg("build").status().unwrap();\n'
    "    let _ = len;\n"
    "}\n"
)


def test_a_right_shift_in_a_marked_signature_does_not_end_the_item(
    tmp_path: pathlib.Path,
) -> None:
    """The first `>` of `4 >> 1` does not leave the signature unbalanced."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "src/lib.rs", MARKED_SHIFTED_ARRAY_LENGTH)
    assert ids(tmp_path) == {"NC-004"}


METHOD_CALLBACK_PARAMETER = CALLER_PARAMETER_SHADOWS_HELPER.replace(
    "fn run(configure: fn(&mut Command)) {",
    "fn run(&self, configure: fn(&mut Command)) {",
)


def test_a_methods_callback_parameter_shadows_the_helper_name(
    tmp_path: pathlib.Path,
) -> None:
    """A receiver does not hide the parameters that follow it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", METHOD_CALLBACK_PARAMETER)
    assert ids(tmp_path) == {"NC-004"}


def grouped_config(filter_expression: str) -> str:
    """Return a bounded configuration whose one grouped override uses a filter."""
    return CLEAN.replace("binary(ui)", filter_expression)


@pytest.mark.parametrize(
    ("filter_expression", "expected"),
    [
        ("binary(ui)", set()),
        ("binary(=ui)", set()),
        ("test(/^compiles($|::)/)", set()),
        ("binary(other) | test(compiles)", set()),
        ("binary(ui_extra)", {"NC-005"}),
        ("binary(extra_ui)", {"NC-005"}),
        ("test(/^compiles_more($|::)/)", {"NC-005"}),
        ("package(ui)", {"NC-005"}),
    ],
)
def test_a_name_counts_only_as_a_whole_word_inside_a_predicate(
    tmp_path: pathlib.Path, filter_expression: str, expected: set[str]
) -> None:
    """`binary(ui_extra)` does not name the `ui` binary, nor `package(ui)` a test."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", grouped_config(filter_expression))
    assert ids(tmp_path) == expected


TOOLCHAIN_TEST = BUILD_TEST.replace('["build", "--bin", "x"]', '["+nightly", "build"]')


def test_a_toolchain_selector_before_the_subcommand_is_still_a_compile(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo +nightly build` compiles, so its test needs the configuration."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", TOOLCHAIN_TEST)
    assert ids(tmp_path) == {"NC-004"}


AFTER_TEST_MODULE = (
    "use std::process::Command;\n"
    "#[cfg(test)]\nmod tests {\n    #[test]\n    fn reads() {\n"
    '        Command::new("cargo").arg("metadata").output().unwrap();\n    }\n}\n'
    "pub fn build() {\n"
    '    Command::new("cargo").arg("build").status().unwrap();\n}\n'
)
COMMENTED_MARKER = (
    "// #[cfg(test)]\nuse std::process::Command;\n"
    "pub fn build() {\n"
    '    Command::new("cargo").arg("build").status().unwrap();\n}\n'
)
SECOND_TEST_BLOCK = (
    "use std::process::Command;\n"
    "#[cfg(test)]\nmod helpers {}\n"
    "pub fn run() {}\n"
    "#[cfg(test)]\nmod tests {\n    #[test]\n    fn compiles() {\n"
    '        Command::new("cargo").arg("build").status().unwrap();\n    }\n}\n'
)
HANDED_ON_QUERY_THEN_GIT = HANDED_ON_QUERY + (
    'fn other() {\n    Command::new("git").arg("build").status().unwrap();\n}\n'
)


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        ("src/lib.rs", AFTER_TEST_MODULE, set()),
        ("src/lib.rs", COMMENTED_MARKER, set()),
        ("src/lib.rs", SECOND_TEST_BLOCK, {"NC-004"}),
        ("tests/common/mod.rs", HANDED_ON_QUERY_THEN_GIT, set()),
        ("tests/common/mod.rs", HANDED_ON, {"NC-004"}),
    ],
    ids=[
        "production-build-after-a-closed-test-module",
        "marker-in-a-comment",
        "build-in-a-second-test-block",
        "query-helper-then-an-unrelated-build",
        "build-helper",
    ],
)
def test_test_scope_is_lexical_and_handed_on_commands_read_their_callee(
    tmp_path: pathlib.Path, path: str, source: str, expected: set[str]
) -> None:
    """A `#[cfg(test)]` item scopes its braces, and a callee scopes its arguments."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, path, source)
    assert ids(tmp_path) == expected


MARKED_ARRAY_LENGTH = (
    "use std::process::Command;\n"
    "#[cfg(test)]\n"
    "fn marked(len: [u8; 4]) {\n"
    '    Command::new("cargo").arg("build").status().unwrap();\n'
    "}\n"
)
MARKED_DECLARATION_THEN_PRODUCTION_BUILD = (
    "#[cfg(test)]\n"
    "mod tests;\n"
    "use std::process::Command;\n"
    "pub fn build() {\n"
    '    Command::new("cargo").arg("build").status().unwrap();\n'
    "}\n"
)


def test_a_semicolon_in_a_marked_signature_does_not_end_the_item(
    tmp_path: pathlib.Path,
) -> None:
    """An array length in a marked function's signature is not the item's end."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "src/lib.rs", MARKED_ARRAY_LENGTH)
    assert ids(tmp_path) == {"NC-004"}


def test_a_semicolon_only_marked_item_scopes_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """`#[cfg(test)] mod tests;` is an item with no braces and no test code."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "src/lib.rs", MARKED_DECLARATION_THEN_PRODUCTION_BUILD)
    assert ids(tmp_path) == set()


FIELD_RECEIVER = (
    "use std::process::Command;\n"
    "fn compile_after_reading_a_field(holder: Holder) {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    command.env("PROGRAM", holder.command.arg("--version").get_program());\n'
    '    command.arg("input.rs").status().unwrap();\n}\n'
)


def test_a_field_receiver_is_not_the_local_binding(tmp_path: pathlib.Path) -> None:
    """`holder.command` is another value, so its `--version` is not a probe."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", FIELD_RECEIVER)
    assert ids(tmp_path) == {"NC-004"}


INNER_REBINDING = (
    "use std::process::Command;\n"
    "fn reused() -> Command {\n"
    '    Command::new("echo")\n}\n'
    "fn build_test_binary() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if reuse {\n"
    "        let mut command = reused();\n"
    '        command.arg("build").status().unwrap();\n'
    "    }\n"
    '    command.arg("metadata").status().unwrap();\n}\n'
)


def test_an_inner_rebinding_is_not_the_outer_command(tmp_path: pathlib.Path) -> None:
    """A `let` inside the block moves the name to another command there."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", INNER_REBINDING)
    assert ids(tmp_path) == set()


RESTRICTED_VISIBILITY_CALLBACK = (
    "use std::process::Command;\n"
    "fn add_build(command: &mut Command) {\n"
    '    command.arg("build");\n}\n'
    "pub(crate) fn run(add_build: fn(&mut Command)) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    add_build(&mut command);\n"
    '    command.arg("metadata").status().unwrap();\n}\n'
)


def test_a_restricted_visibility_signature_still_shadows_the_helper(
    tmp_path: pathlib.Path,
) -> None:
    """`pub(crate)` holds no parameter, so the callback before it still shadows."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", RESTRICTED_VISIBILITY_CALLBACK)
    assert ids(tmp_path) == set()


GUARDED_HELPER_CALL = (
    "use std::process::Command;\n"
    "fn probe_toolchain() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    let _ = query && add_version(&mut command);\n"
    '    command.arg("input.rs").status().unwrap();\n}\n'
    "fn add_version(command: &mut Command) -> bool {\n"
    '    command.arg("--version");\n'
    "    true\n}\n"
)


def test_a_short_circuited_helper_call_is_still_conditional(
    tmp_path: pathlib.Path,
) -> None:
    """`query && add_version(..)` skips the helper's flag when `query` is false."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", GUARDED_HELPER_CALL)
    assert ids(tmp_path) == {"NC-004"}


CLOSED_BLOCK_BEFORE_PROBE = (
    "use std::process::Command;\n"
    "fn probe_toolchain() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    let _ = if query { command.env("MODE", "a") } '
    'else { command.env("MODE", "b") };\n'
    '    command.arg("--version").status().unwrap();\n}\n'
)


def test_a_closed_block_does_not_lift_the_next_statement(
    tmp_path: pathlib.Path,
) -> None:
    """Both branches close, so the caller's `--version` is unconditional."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CLOSED_BLOCK_BEFORE_PROBE)
    assert ids(tmp_path) == set()


CONSTRUCTOR_TEXT_IN_A_LITERAL = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    Command::new("cargo")\n'
    '        .env("LABEL", "Command::new(")\n'
    '        .arg("build")\n'
    "        .status()\n        .unwrap();\n}\n"
)


def test_a_constructor_in_a_string_does_not_end_the_window(
    tmp_path: pathlib.Path,
) -> None:
    """The `Command::new(` inside an environment value builds no other command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONSTRUCTOR_TEXT_IN_A_LITERAL)
    assert ids(tmp_path) == {"NC-004"}


HELP_SUBCOMMAND = (
    "use std::process::Command;\n"
    "fn prints_help() {\n"
    '    Command::new("cargo").arg("help").arg("build").status().unwrap();\n}\n'
)


def test_a_help_subcommand_is_not_a_compile(tmp_path: pathlib.Path) -> None:
    """`cargo help build` prints help; `build` is its topic, not the subcommand."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELP_SUBCOMMAND)
    assert ids(tmp_path) == set()


QUALIFIED_CONSTRUCTOR = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = std::process::Command::new("cargo");\n'
    '    command.arg("build").status().unwrap();\n}\n'
)


def test_a_qualified_constructor_still_binds_the_command(
    tmp_path: pathlib.Path,
) -> None:
    """`std::process::Command::new` binds `command` just as the bare form does."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", QUALIFIED_CONSTRUCTOR)
    assert ids(tmp_path) == {"NC-004"}


ARGUMENT_AFTER_A_LAUNCH_IN_THE_SAME_STATEMENT = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new("cargo");\n'
    '    if command.arg("metadata").status().is_ok() {\n'
    '        command.arg("build");\n'
    "    }\n}\n"
)


def test_an_argument_after_the_launch_in_the_same_statement_is_not_run(
    tmp_path: pathlib.Path,
) -> None:
    """The `build` in the `if` body is added after the only run of the command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ARGUMENT_AFTER_A_LAUNCH_IN_THE_SAME_STATEMENT)
    assert ids(tmp_path) == set()


CONDITIONAL_LAUNCH_THAT_RETURNS = (
    "use std::process::Command;\n"
    "fn build_test_binary(metadata: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if metadata {\n"
    '        command.arg("metadata").status().unwrap();\n'
    "        return;\n"
    "    }\n"
    '    command.arg("build").status().unwrap();\n}\n'
)


def test_a_conditional_launch_that_returns_does_not_end_the_arguments(
    tmp_path: pathlib.Path,
) -> None:
    """The later `build` is the first launch on the path that skips the `if`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITIONAL_LAUNCH_THAT_RETURNS)
    assert ids(tmp_path) == {"NC-004"}


SHIFTED_ARRAY_IN_AN_OUTER_GENERIC = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    add(None, &mut command);\n}\n"
    "fn add(_: Option<[u8; 4 >> 1]>, target: &mut Command) {\n"
    '    target.arg("build").status().unwrap();\n}\n'
)


def test_a_shift_in_an_outer_generics_array_keeps_parameter_positions(
    tmp_path: pathlib.Path,
) -> None:
    """The shift's `>` closes neither the array's `[` nor `Option`'s `<`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SHIFTED_ARRAY_IN_AN_OUTER_GENERIC)
    assert ids(tmp_path) == {"NC-004"}


PROBE_FLAG_BEHIND_A_SHORT_CIRCUIT = (
    "use std::process::Command;\n"
    "fn compile_after_check() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    let _ = query && command.arg("--version").get_args().next().is_some();\n'
    '    command.arg("input.rs").status().unwrap();\n}\n'
)


def test_a_probe_flag_behind_a_short_circuit_is_not_a_probe(
    tmp_path: pathlib.Path,
) -> None:
    """The flag is absent when `query` is false, so it proves nothing."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PROBE_FLAG_BEHIND_A_SHORT_CIRCUIT)
    assert ids(tmp_path) == {"NC-004"}


CARGO_HELP_FLAG = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("build").arg("--help").status().unwrap();\n}\n'
)


def test_a_help_flag_compiles_nothing(tmp_path: pathlib.Path) -> None:
    """`cargo build --help` prints help instead of building."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CARGO_HELP_FLAG)
    assert ids(tmp_path) == set()


RUSTC_HELP_FLAG = (
    "use std::process::Command;\n"
    "fn compiler_help() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    command.arg("--help").status().unwrap();\n}\n'
)


def test_a_rustc_help_flag_compiles_nothing(tmp_path: pathlib.Path) -> None:
    """`rustc --help` prints its help and exits, like the version flag does."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", RUSTC_HELP_FLAG)
    assert ids(tmp_path) == set()


SPACED_FIELD_RECEIVER = (
    "use std::process::Command;\n"
    "fn compile_after_reading_a_field(holder: Holder) {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    command.env("PROGRAM", holder . command.arg("--version").get_program());\n'
    '    command.arg("input.rs").status().unwrap();\n}\n'
)


def test_a_spaced_field_receiver_is_not_the_local_binding(
    tmp_path: pathlib.Path,
) -> None:
    """Rust allows whitespace around `.`, so `holder . command` is still a field."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SPACED_FIELD_RECEIVER)
    assert ids(tmp_path) == {"NC-004"}


PROBE_ON_ONLY_ONE_PATH = (
    "use std::process::Command;\n"
    "fn compile_or_probe(compile: bool) {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    if compile {\n"
    '        command.arg("input.rs").status().unwrap();\n'
    "        return;\n"
    "    }\n"
    '    command.arg("--version").status().unwrap();\n}\n'
)


def test_a_probe_on_one_path_does_not_hide_a_compile_on_another(
    tmp_path: pathlib.Path,
) -> None:
    """The `--version` run is not the run that compiles `input.rs`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PROBE_ON_ONLY_ONE_PATH)
    assert ids(tmp_path) == {"NC-004"}


SPACED_LAUNCH = (
    "use std::process::Command;\n"
    "fn probe_after_a_run() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    command.arg("input.rs").status ();\n'
    '    command.arg("--version").status();\n}\n'
)


def test_a_launch_with_a_space_before_its_arguments_ends_the_read(
    tmp_path: pathlib.Path,
) -> None:
    """Rust allows `status ()`, which is the run that compiles the input."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SPACED_LAUNCH)
    assert ids(tmp_path) == {"NC-004"}


HELPER_WITH_TWO_USES_IN_ONE_STATEMENT = (
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("CARGO"));\n'
    "    configure(&mut command);\n}\n"
    "fn configure(target: &mut Command) {\n"
    '    if target.get_program() != "x" {\n'
    '        target.arg("build");\n'
    "    }\n"
    "    target.status().unwrap();\n}\n"
)


def test_a_helper_statement_hands_every_use_of_its_parameter_on(
    tmp_path: pathlib.Path,
) -> None:
    """The `build` after `get_program()` is on the same parameter."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELPER_WITH_TWO_USES_IN_ONE_STATEMENT)
    assert ids(tmp_path) == {"NC-004"}


IMPORTED_HELPER_WITH_A_LOCAL_NAMESAKE = (
    "use support::configure;\n"
    "use std::process::Command;\n"
    "fn build_test_binary() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    "    configure(&mut command);\n"
    '    command.arg("input.rs").status().unwrap();\n}\n'
    "mod helpers {\n"
    "    use std::process::Command;\n"
    "    pub fn configure(target: &mut Command) {\n"
    '        target.arg("--version");\n'
    "    }\n"
    "}\n"
)


def test_an_imported_helper_is_not_read_from_a_local_namesake(
    tmp_path: pathlib.Path,
) -> None:
    """The call reaches the imported `configure`, not the module's `fn`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", IMPORTED_HELPER_WITH_A_LOCAL_NAMESAKE)
    assert ids(tmp_path) == {"NC-004"}


BRANCHING_LAUNCHES = (
    "use std::process::Command;\n"
    "fn build_or_metadata(metadata: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if metadata {\n"
    '        command.arg("metadata").status().unwrap();\n'
    "    } else {\n"
    '        command.arg("build").status().unwrap();\n'
    "    }\n}\n"
)


def test_each_branch_ends_at_its_own_launch(tmp_path: pathlib.Path) -> None:
    """The `else` branch builds, so the metadata run does not end the scan."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", BRANCHING_LAUNCHES)
    assert ids(tmp_path) == {"NC-004"}


HELPER_BRANCH_THAT_RETURNS = (
    "use std::process::Command;\n"
    "fn build_test_binary(metadata: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    prepare(&mut command, metadata);\n}\n"
    "fn prepare(target: &mut Command, metadata: bool) {\n"
    "    if metadata {\n"
    '        target.arg("metadata").status().unwrap();\n'
    "        return;\n"
    "    }\n"
    '    target.arg("build").status().unwrap();\n}\n'
)


def test_a_helper_branch_that_returns_keeps_its_later_run(
    tmp_path: pathlib.Path,
) -> None:
    """The helper's `return` is the caller's too, so `build` is still read."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELPER_BRANCH_THAT_RETURNS)
    assert ids(tmp_path) == {"NC-004"}


ASSIGNED_AWAY = (
    "use std::process::Command;\n"
    "fn git_command() -> Command {\n"
    '    Command::new("git")\n'
    "}\n"
    "fn git_after_cargo() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    command = git_command();\n"
    '    command.arg("build").status().unwrap();\n'
    "}\n"
)


def test_a_reassigned_binding_is_no_longer_the_command(
    tmp_path: pathlib.Path,
) -> None:
    """After `command = ..` the name denotes another command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ASSIGNED_AWAY)
    assert ids(tmp_path) == set()


UNREADABLE_OPTION_VALUE = (
    "use std::process::Command;\n"
    "fn build_with_colour(colour: &str) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.args(["--color", colour, "build"]).status().unwrap();\n}\n'
)


def test_an_unreadable_option_value_does_not_end_the_subcommand_read(
    tmp_path: pathlib.Path,
) -> None:
    """`colour` fills the `--color` slot, so `build` is the subcommand."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", UNREADABLE_OPTION_VALUE)
    assert ids(tmp_path) == {"NC-004"}


SPLIT_PRINT_QUERY = (
    "use std::process::Command;\n"
    "fn query_without_compiling() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    command.arg("--print").arg("cfg").status().unwrap();\n}\n'
)


def test_a_print_query_split_across_two_arguments_is_a_probe(
    tmp_path: pathlib.Path,
) -> None:
    """`--print cfg` in two calls asks the same question as one array."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SPLIT_PRINT_QUERY)
    assert ids(tmp_path) == set()


HELP_TOPIC_AFTER_A_CONDITIONAL_RUN = (
    "use std::process::Command;\n"
    "fn build_test_binary(metadata: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if metadata {\n"
    '        command.arg("metadata").status().unwrap();\n'
    "        return;\n"
    "    }\n"
    '    command.arg("help").arg("build").status().unwrap();\n}\n'
)


def test_each_run_is_asked_for_its_own_subcommand(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo help build` prints help; the earlier run reads metadata."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELP_TOPIC_AFTER_A_CONDITIONAL_RUN)
    assert ids(tmp_path) == set()


INNER_REBINDING_BEFORE_A_BUILD = (
    "use std::process::Command;\n"
    "fn reused() -> Command {\n"
    '    Command::new("echo")\n'
    "}\n"
    "fn build_after_rebinding(reuse: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if reuse {\n"
    "        let mut command = reused();\n"
    '        command.arg("check").status().unwrap();\n'
    "    }\n"
    '    command.arg("build").status().unwrap();\n'
    "}\n"
)


def test_attribution_resumes_on_the_statement_that_closes_the_block(
    tmp_path: pathlib.Path,
) -> None:
    """The outer `build` follows the inner block, so it is still this command's."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", INNER_REBINDING_BEFORE_A_BUILD)
    assert ids(tmp_path) == {"NC-004"}


NAMESAKE_BINDING = (
    "use std::process::Command;\n"
    "fn other_command() -> Command {\n"
    '    Command::new("git")\n'
    "}\n"
    "fn build_after_a_namesake() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    let my_command = other_command();\n"
    '    command.arg("build").status().unwrap();\n'
    "}\n"
)


def test_a_namesake_binding_does_not_move_the_command(tmp_path: pathlib.Path) -> None:
    """`my_command` is another binding, so `command` still denotes the cargo one."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", NAMESAKE_BINDING)
    assert ids(tmp_path) == {"NC-004"}


LAUNCH_AS_A_CONDITION = (
    "use std::process::Command;\n"
    "fn build_after_a_probe(probe: bool) -> bool {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if probe {\n"
    '        command.arg("--version").status().unwrap();\n'
    "        return false;\n"
    "    }\n"
    '    if command.arg("build").status().is_ok() {\n'
    "        return true;\n"
    "    }\n"
    "    false\n"
    "}\n"
)


def test_a_launch_is_read_at_the_depth_it_reaches(
    tmp_path: pathlib.Path,
) -> None:
    """The close brace before the launch reaches the outer depth, so a run starts."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", LAUNCH_AS_A_CONDITION)
    assert ids(tmp_path) == {"NC-004"}


def test_a_qualified_receiver_does_not_own_the_chain() -> None:
    """`support::command.arg(..)` calls a path item, not the local binding."""
    qualified = 'support::command.arg("build").status()'
    qualified_chain = next(iter(call_chains(qualified, 0, len(qualified))), None)
    assert qualified_chain is not None
    assert not owns_chain(qualified_chain, "command")
    plain = 'command.arg("build").status()'
    plain_chain = next(iter(call_chains(plain, 0, len(plain))), None)
    assert plain_chain is not None
    assert owns_chain(plain_chain, "command")


def test_a_statement_without_a_semicolon_ends_at_the_window_edge() -> None:
    """A statement ends after its own text, not one character short of it.

    The chain search stops at that offset, so returning the last character's
    index instead of the offset past it would hide a call written right at the
    window's edge.
    """
    assert _statement_end('command.arg("x");', 0, 17) == 16
    assert _statement_end('command.arg("x")', 0, 16) == 16
    assert _statement_end("", 0, 0) == 0


SPACED_LAUNCH_AFTER_THE_DOT = (
    "use std::process::Command;\n"
    "fn probe_after_a_run() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    command.arg("input.rs"). status().unwrap();\n'
    '    command.arg("--version"). status().unwrap();\n}\n'
)


def test_a_launch_with_a_space_after_its_dot_ends_the_read(
    tmp_path: pathlib.Path,
) -> None:
    """Rust allows `command . status()`, so the spaced run still compiles."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SPACED_LAUNCH_AFTER_THE_DOT)
    assert ids(tmp_path) == {"NC-004"}


HELPER_THAT_RETURNS_BEFORE_A_PROBE = (
    "use std::process::Command;\n"
    "fn compile() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    command.arg("input.rs");\n'
    "    configure(&mut command);\n"
    "    command.status().unwrap();\n}\n"
    "fn configure(target: &mut Command) {\n"
    "    if skip() {\n"
    "        return;\n"
    "    }\n"
    '    target.arg("--version");\n'
    "}\n"
)


def test_a_helper_return_starts_the_callers_next_run(tmp_path: pathlib.Path) -> None:
    """The `--version` is only added when the helper does not return early."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELPER_THAT_RETURNS_BEFORE_A_PROBE)
    assert ids(tmp_path) == {"NC-004"}


GLOB_IMPORTED_HELPER = (
    "use support::*;\n"
    "use std::process::Command;\n"
    "fn compile() {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    command.arg("input.rs");\n'
    "    configure(&mut command);\n"
    "    command.status().unwrap();\n}\n"
    "fn configure(target: &mut Command) {\n"
    '    target.arg("--version");\n'
    "}\n"
)


def test_a_glob_import_hides_the_helper(tmp_path: pathlib.Path) -> None:
    """`use support::*` could bring another `configure` in, so none is read."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", GLOB_IMPORTED_HELPER)
    assert ids(tmp_path) == {"NC-004"}


ARGUMENT_AFTER_A_CLOSED_BLOCK = (
    "use std::process::Command;\n"
    "fn build_when_not_help(help: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if help {\n"
    '        command.arg("--help");\n'
    "        command.status().unwrap();\n"
    "    }\n"
    '    command.arg("build");\n'
    "    command.status().unwrap();\n}\n"
)


def test_an_argument_after_a_closed_block_is_read(tmp_path: pathlib.Path) -> None:
    """The `build` follows the block, so it begins the next run."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ARGUMENT_AFTER_A_CLOSED_BLOCK)
    assert ids(tmp_path) == {"NC-004"}


CONDITIONAL_HELP_FLAG = (
    "use std::process::Command;\n"
    "fn build_unless_help(help: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("build");\n'
    "    if help {\n"
    '        command.arg("--help");\n'
    "    }\n"
    "    command.status().unwrap();\n}\n"
)


def test_a_conditional_help_flag_does_not_excuse_a_build(
    tmp_path: pathlib.Path,
) -> None:
    """`build` is given on every path; the `--help` only on one."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITIONAL_HELP_FLAG)
    assert ids(tmp_path) == {"NC-004"}


ASSIGNED_IN_A_BLOCK = (
    "use std::process::Command;\n"
    "fn git_command() -> Command {\n"
    '    Command::new("git")\n'
    "}\n"
    "fn build_after_assignment() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    {\n"
    "        command = git_command();\n"
    "    }\n"
    '    command.arg("build").status().unwrap();\n'
    "}\n"
)


def test_an_assignment_inside_a_block_also_moves_the_name(
    tmp_path: pathlib.Path,
) -> None:
    """The block may have run, so `command` is no longer certainly the cargo one."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ASSIGNED_IN_A_BLOCK)
    assert ids(tmp_path) == set()


PROBE_SHARED_BEFORE_A_BRANCH = (
    "use std::process::Command;\n"
    "fn probe_either_way(probe: bool) {\n"
    '    let mut command = Command::new(env!("RUSTC"));\n'
    '    command.arg("--version");\n'
    "    if probe {\n"
    "        command.status().unwrap();\n"
    "    } else {\n"
    "        command.status().unwrap();\n"
    "    }\n"
    "}\n"
)


def test_an_else_arm_keeps_the_arguments_given_before_the_branch(
    tmp_path: pathlib.Path,
) -> None:
    """`--version` is on every path, so the `else` run asks a question too."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PROBE_SHARED_BEFORE_A_BRANCH)
    assert ids(tmp_path) == set()


FIELD_INITIALIZER_RECEIVER = (
    "use std::process::Command;\n"
    "fn build_in_a_struct_literal() {\n"
    '    let mut command = Command::new("cargo");\n'
    '    let _ = Settings { status: command.arg("build").status() };\n'
    "}\n"
)


def test_a_field_initializer_receiver_is_still_the_binding(
    tmp_path: pathlib.Path,
) -> None:
    """A single colon initializes a field; the value is still the command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", FIELD_INITIALIZER_RECEIVER)
    assert ids(tmp_path) == {"NC-004"}


NAMESPACE_CALL_NAMED_LIKE_THE_BINDING = (
    "use std::process::Command;\n"
    "fn build_around_a_namespace_call() {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("--verbose")\n'
    '        .env("V", command::factory().output())\n'
    '        .arg("build")\n'
    "        .status()\n"
    "        .unwrap();\n"
    "}\n"
)


def test_a_namespace_call_named_like_the_binding_is_not_its_launch(
    tmp_path: pathlib.Path,
) -> None:
    """`command::factory()` starts at a path root, so its launch is not ours."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", NAMESPACE_CALL_NAMED_LIKE_THE_BINDING)
    assert ids(tmp_path) == {"NC-004"}


UNREADABLE_WORD_INSIDE_A_PRINT_QUERY = (
    "use std::process::Command;\n"
    "fn print_a_kind(print_kind: &str) {\n"
    '    Command::new("rustc")\n'
    '        .args(["--print", print_kind, "cfg"])\n'
    "        .status()\n"
    "        .unwrap();\n"
    "}\n"
)


def test_a_word_between_the_two_halves_of_a_print_query_breaks_the_query(
    tmp_path: pathlib.Path,
) -> None:
    """The unseen word may be `link-args`, so `cfg` is not certainly its answer."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", UNREADABLE_WORD_INSIDE_A_PRINT_QUERY)
    assert ids(tmp_path) == {"NC-004"}


DESTRUCTURED_HELPER = (
    "use std::process::Command;\n"
    "fn configure(target: &mut Command) {\n"
    '    target.arg("build");\n'
    "}\n"
    "fn noop(_: &mut Command) {}\n"
    "fn read_help() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    let (configure,) = (noop as fn(&mut Command),);\n"
    "    configure(&mut command);\n"
    '    command.arg("help").status().unwrap();\n'
    "}\n"
)


def test_a_destructured_binding_hides_the_helper(tmp_path: pathlib.Path) -> None:
    """The local `configure` is a destructured value, not the module's `fn`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", DESTRUCTURED_HELPER)
    assert ids(tmp_path) == set()


CONDITIONAL_HELP_BEFORE_A_BUILD = (
    "use std::process::Command;\n"
    "fn build_after_a_conditional_help(debug: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if debug {\n"
    '        command.arg("help");\n'
    "    }\n"
    '    command.arg("build").status().unwrap();\n'
    "}\n"
)


def test_a_conditional_subcommand_does_not_stop_the_build_being_read(
    tmp_path: pathlib.Path,
) -> None:
    """`build` is the subcommand on every path; the `help` is on one."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITIONAL_HELP_BEFORE_A_BUILD)
    assert ids(tmp_path) == {"NC-004"}


PROBE_INSIDE_ITS_LAUNCHING_BRANCH = (
    "use std::process::Command;\n"
    "fn probe_in_a_branch(only_version: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    if only_version {\n"
    '        command.arg("--version").status().unwrap();\n'
    "    }\n"
    "}\n"
)


def test_a_probe_flag_inside_its_launching_branch_is_a_probe(
    tmp_path: pathlib.Path,
) -> None:
    """The flag and the launch share a branch, so every run asks a question."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PROBE_INSIDE_ITS_LAUNCHING_BRANCH)
    assert ids(tmp_path) == set()


PROBE_IN_BOTH_ARMS = (
    "use std::process::Command;\n"
    "fn probe_either_branch(only_version: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    if only_version {\n"
    '        command.arg("--version").status().unwrap();\n'
    "    } else {\n"
    '        command.arg("--version").status().unwrap();\n'
    "    }\n"
    "}\n"
)


def test_a_probe_flag_in_both_arms_is_a_probe(tmp_path: pathlib.Path) -> None:
    """Neither arm compiles, so the command never compiles on any path."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PROBE_IN_BOTH_ARMS)
    assert ids(tmp_path) == set()


LAUNCH_IN_A_SIBLING_BLOCK = (
    "use std::process::Command;\n"
    "fn build_on_the_other_path(probe: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    if probe { command.arg("--version").status().unwrap(); return; }\n'
    '    if !probe { command.arg("build").status().unwrap(); }\n'
    "}\n"
)


def test_a_launch_in_a_sibling_block_is_another_run(tmp_path: pathlib.Path) -> None:
    """A closed block that sits beside the launch is not the same path."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", LAUNCH_IN_A_SIBLING_BLOCK)
    assert ids(tmp_path) == {"NC-004"}


SPACED_ARGUMENT_CALL = (
    "use std::process::Command;\n"
    "fn build_with_a_spaced_call() {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command . arg("build") . status().unwrap();\n'
    "}\n"
)


def test_an_argument_call_may_be_spaced_after_its_dot(
    tmp_path: pathlib.Path,
) -> None:
    """`command . arg(..)` is the same call as `command.arg(..)`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SPACED_ARGUMENT_CALL)
    assert ids(tmp_path) == {"NC-004"}


LINK_ARGS_ON_ONE_PATH = (
    "use std::process::Command;\n"
    "fn print_with_a_branch(link: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    '    command.arg("--print");\n'
    "    if link {\n"
    '        command.arg("link-args");\n'
    "    }\n"
    '    command.arg("cfg").status().unwrap();\n'
    "}\n"
)


def test_a_conditional_word_between_the_print_halves_breaks_the_query(
    tmp_path: pathlib.Path,
) -> None:
    """On the path that runs the block the query is `link-args`, not `cfg`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", LINK_ARGS_ON_ONE_PATH)
    assert ids(tmp_path) == {"NC-004"}


PROBES_IN_BOTH_ARMS_BEFORE_A_LAUNCH = (
    "use std::process::Command;\n"
    "fn probe_both_ways(choice: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    if choice {\n"
    '        command.arg("-vV");\n'
    "    } else {\n"
    '        command.arg("-V");\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_probe_in_every_arm_probes_the_shared_launch(
    tmp_path: pathlib.Path,
) -> None:
    """Neither arm reaches the launch without a version flag."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PROBES_IN_BOTH_ARMS_BEFORE_A_LAUNCH)
    assert ids(tmp_path) == set()


TUPLE_WITH_ANOTHER_RECEIVER = (
    "use std::process::Command;\n"
    "fn build_in_a_tuple(other: &mut Command) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    let _ = (other.get_program(), command.arg("build").status());\n'
    "}\n"
)


def test_another_receiver_does_not_hide_the_command_in_the_statement(
    tmp_path: pathlib.Path,
) -> None:
    """The second tuple element still runs, however the statement starts."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", TUPLE_WITH_ANOTHER_RECEIVER)
    assert ids(tmp_path) == {"NC-004"}


FIELD_ASSIGNMENT_BEFORE_A_BUILD = (
    "use std::process::Command;\n"
    "struct Holder {\n"
    "    command: Command,\n"
    "}\n"
    "fn build_after_a_field_assignment(holder: &mut Holder, replacement: Command) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    holder . command = replacement;\n"
    '    command.arg("build").status().unwrap();\n'
    "}\n"
)


def test_a_field_assignment_does_not_move_the_binding(
    tmp_path: pathlib.Path,
) -> None:
    """`holder . command = ..` moves the field, however it is spaced."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", FIELD_ASSIGNMENT_BEFORE_A_BUILD)
    assert ids(tmp_path) == {"NC-004"}


MODE_BEFORE_A_BUILD = (
    "use std::process::Command;\n"
    "fn build_after_a_mode(mode: &str) {\n"
    '    Command::new("cargo")\n'
    '        .args([mode, "build"])\n'
    "        .status()\n"
    "        .unwrap();\n"
    "}\n"
)


def test_a_compiling_word_after_an_unreadable_one_is_still_read(
    tmp_path: pathlib.Path,
) -> None:
    """`mode` may be an option, so `build` is still the subcommand."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", MODE_BEFORE_A_BUILD)
    assert ids(tmp_path) == {"NC-004"}


REF_SHADOWED_HELPER = (
    "use std::process::Command;\n"
    "type Configurer = fn(&mut Command);\n"
    "fn configure(target: &mut Command) {\n"
    '    target.arg("build");\n'
    "}\n"
    "fn read_help(callback: Configurer) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    let ref configure = callback;\n"
    "    configure(&mut command);\n"
    '    command.arg("help").status().unwrap();\n'
    "}\n"
)


def test_a_ref_binding_hides_the_helper(tmp_path: pathlib.Path) -> None:
    """`let ref configure = ..` binds the name to the callback, not the `fn`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", REF_SHADOWED_HELPER)
    assert ids(tmp_path) == set()


UNSAFE_FUNCTION_AFTER_A_COMMAND = (
    "use std::process::Command;\n"
    "fn construct() {\n"
    '    let mut command = Command::new("cargo");\n'
    "}\n"
    "unsafe fn later(command: &mut Command) {\n"
    '    command.arg("build");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_an_unsafe_function_ends_the_command_window(tmp_path: pathlib.Path) -> None:
    """The next function item is another scope, however it is qualified.

    The second function launches the command its own parameter names, so a
    window that ran past the item would take that launch for the first
    command's and report it as compiling.
    """
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", UNSAFE_FUNCTION_AFTER_A_COMMAND)
    assert ids(tmp_path) == set()


REF_REBOUND_AFTER_A_RUN = (
    "use std::process::Command;\n"
    "fn run(fast: bool, mut other: &mut Command) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if fast {\n"
    '        command.arg("--version").status().unwrap();\n'
    "    }\n"
    "    let ref mut command: &mut &mut Command = other;\n"
    '    command.arg("test").status().unwrap();\n'
    "}\n"
)


def test_a_ref_rebinding_ends_the_argument_read(tmp_path: pathlib.Path) -> None:
    """`let ref mut command = ..` makes the later launch another value's run."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", REF_REBOUND_AFTER_A_RUN)
    assert ids(tmp_path) == set()


SAME_LINE_FUNCTION_AFTER_A_COMMAND = (
    "use std::process::Command;\n"
    'fn construct() { let mut command = Command::new("cargo"); } '
    'fn later(command: &mut Command) { command.arg("build").status(); }\n'
)


def test_a_function_may_begin_on_the_same_line_as_the_last(
    tmp_path: pathlib.Path,
) -> None:
    """A `fn` item ends the window even when the previous one closed on its line."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SAME_LINE_FUNCTION_AFTER_A_COMMAND)
    assert ids(tmp_path) == set()


def test_the_file_index_sees_a_function_that_shares_a_line() -> None:
    """Two items written on one line are two boundaries, not one."""
    masked = (
        'fn first() { let mut command = Command::new("cargo"); } '
        'fn later(command: &mut Command) { command.arg("build"); }'
    )
    index = file_index(masked)
    assert len(index.boundaries) == 2
    assert masked[index.boundaries[1] :].startswith("fn later")


CONDITION_ARGUMENTS_ARE_ON_EVERY_PATH = (
    "use std::process::Command;\n"
    "fn probe() {\n"
    '    let mut command = Command::new("rustc");\n'
    '    if command.arg("--version").get_args().next().is_some() {\n'
    '        command.arg("input.rs").status().unwrap();\n'
    "    } else {\n"
    '        command.arg("input.rs").status().unwrap();\n'
    "    }\n"
    "}\n"
)


def test_a_condition_gives_its_arguments_to_every_run(tmp_path: pathlib.Path) -> None:
    """The `--version` added by the condition runs before either arm."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITION_ARGUMENTS_ARE_ON_EVERY_PATH)
    assert ids(tmp_path) == set()


INLINE_ARMS_WITH_A_PROBE = (
    "use std::process::Command;\n"
    "fn probe(choice: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    '    let _ = if choice { command.arg("-V") } else { command.arg("-vV") };\n'
    '    command.arg("input.rs").status().unwrap();\n'
    "}\n"
)


def test_an_inline_choice_pairs_its_arms(tmp_path: pathlib.Path) -> None:
    """Both arms of `let _ = if c { .. } else { .. }` cover the shared launch."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", INLINE_ARMS_WITH_A_PROBE)
    assert ids(tmp_path) == set()


GUARDED_PROBE_IN_ONE_ARM = (
    "use std::process::Command;\n"
    "fn probe(query: bool, choice: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    if choice {\n"
    '        query && command.arg("-V").get_args().next().is_some();\n'
    "    } else {\n"
    '        command.arg("-vV");\n'
    "    }\n"
    '    command.arg("input.rs").status().unwrap();\n'
    "}\n"
)


def test_a_probe_behind_a_short_circuit_does_not_cover_its_arm(
    tmp_path: pathlib.Path,
) -> None:
    """`query && ..` may skip the `-V`, so the if arm is not a probe."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", GUARDED_PROBE_IN_ONE_ARM)
    assert ids(tmp_path) == {"NC-004"}


SUBCOMMAND_IN_EACH_ARM = (
    "use std::process::Command;\n"
    "fn run(choice: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if choice {\n"
    '        command.arg("metadata");\n'
    "    } else {\n"
    '        command.arg("build");\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_each_arm_is_read_for_its_own_subcommand(tmp_path: pathlib.Path) -> None:
    """`metadata` in the if arm does not hide the `build` in the else arm."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SUBCOMMAND_IN_EACH_ARM)
    assert ids(tmp_path) == {"NC-004"}


COMMAND_NAMED_PARAMETER = (
    "use std::process::Command;\n"
    "fn probe(Command: &mut Command) {\n"
    '    let mut command = Command::new("rustc");\n'
    '    command.env("P", Command.arg("--version").get_program());\n'
    '    command.arg("input.rs").status().unwrap();\n'
    "}\n"
)


def test_a_bare_command_root_is_not_the_constructor(tmp_path: pathlib.Path) -> None:
    """`Command.arg(..)` names the parameter, not the command being built."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", COMMAND_NAMED_PARAMETER)
    assert ids(tmp_path) == {"NC-004"}


HELPER_THAT_NAMES_THE_CALLER = (
    "use std::process::Command;\n"
    "fn configure(target: &mut Command, cmd: &mut Command) {\n"
    '    target.env("P", cmd.arg("--version").get_program());\n'
    "}\n"
    "fn probe(other: &mut Command) {\n"
    '    let mut cmd = Command::new("rustc");\n'
    "    configure(&mut cmd, other);\n"
    '    cmd.arg("input.rs").status().unwrap();\n'
    "}\n"
)


def test_a_helper_that_names_the_caller_is_not_spliced(
    tmp_path: pathlib.Path,
) -> None:
    """The helper's own `cmd` would read as the caller's after the splice."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELPER_THAT_NAMES_THE_CALLER)
    assert ids(tmp_path) == {"NC-004"}


INNER_ELSE_AFTER_A_RUN = (
    "use std::process::Command;\n"
    "fn probe(ok: bool, inner: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    if ok {\n"
    '        command.arg("--version").status().unwrap();\n'
    "        if inner {\n"
    '            command.arg("-Zunstable");\n'
    "        } else {\n"
    '            command.arg("-Zother");\n'
    "        }\n"
    "    }\n"
    "}\n"
)


def test_an_else_inside_the_launched_block_is_not_another_run(
    tmp_path: pathlib.Path,
) -> None:
    """The inner `else` closes a block the launch is not in, so it is a reuse."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", INNER_ELSE_AFTER_A_RUN)
    assert ids(tmp_path) == set()


GROUPED_GLOB_IMPORT = (
    "use std::process::Command;\n"
    "use support::{*};\n"
    "fn configure(target: &mut Command) {\n"
    '    target.arg("--version");\n'
    "}\n"
    "fn probe() {\n"
    '    let mut command = Command::new("rustc");\n'
    "    configure(&mut command);\n"
    '    command.arg("input.rs").status().unwrap();\n'
    "}\n"
)


def test_a_grouped_glob_is_still_a_glob(tmp_path: pathlib.Path) -> None:
    """`use support::{*}` binds names without spelling them, like `use x::*`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", GROUPED_GLOB_IMPORT)
    assert ids(tmp_path) == {"NC-004"}


HELP_IN_EVERY_ARM = (
    "use std::process::Command;\n"
    "fn run(choice: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("build");\n'
    "    if choice {\n"
    '        command.arg("-h");\n'
    "    } else {\n"
    '        command.arg("--help");\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_help_given_in_every_arm_compiles_nothing(tmp_path: pathlib.Path) -> None:
    """Whichever arm runs, cargo prints help instead of building."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELP_IN_EVERY_ARM)
    assert ids(tmp_path) == set()


@pytest.mark.parametrize(
    "filter_expression",
    [
        "not(binary(ui))",
        "!binary(ui)",
        "not (test(/^compiles($|::)/))",
        "!test(compiles)",
        "not(binary(ui)) | binary(other)",
        "!(binary(other) | binary(ui))",
        "not(binary(other) | binary(ui))",
        "!(test(/foo[)]/) | binary(ui))",
        "all() - binary(ui)",
        "all() - (binary(ui) | binary(other))",
    ],
)
def test_a_negated_predicate_does_not_name_the_spawner(
    tmp_path: pathlib.Path, filter_expression: str
) -> None:
    """A filter that excludes `ui` does not place it in the serial group."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", grouped_config(filter_expression))
    assert ids(tmp_path) == {"NC-005"}


def test_a_selected_predicate_beside_a_negated_one_still_names_the_spawner(
    tmp_path: pathlib.Path,
) -> None:
    """`not(binary(other)) | binary(ui)` selects `ui` however it also excludes."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(
        tmp_path,
        ".config/nextest.toml",
        grouped_config("not(binary(other)) | binary(ui)"),
    )
    assert ids(tmp_path) == set()


@pytest.mark.parametrize(
    "filter_expression",
    [
        "all() & binary(ui)",
        "!(!binary(ui))",
        "!(!(binary(ui)))",
        "!!binary(ui)",
    ],
)
def test_a_predicate_selected_after_cancelling_exclusions_names_the_spawner(
    tmp_path: pathlib.Path, filter_expression: str
) -> None:
    """An even number of exclusions around `ui` selects it again."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILD_TEST)
    put(tmp_path, ".config/nextest.toml", grouped_config(filter_expression))
    assert ids(tmp_path) == set()


def test_an_escaped_parenthesis_does_not_end_a_negated_group() -> None:
    r"""`\\)` is matcher data, so the rest of the negated group stays excluded."""
    assert _predicate_arguments("!(test(=other\\)) | binary(ui))") == []


ESCAPED_BUILD = (
    "use std::process::Command;\n"
    "#[test]\nfn compiles() {\n"
    '    Command::new("cargo").arg("buil\\x64").status().unwrap();\n'
    "}\n"
)


def test_an_escape_in_a_subcommand_is_decoded(tmp_path: pathlib.Path) -> None:
    """A hexadecimal escape spells `build`, so the test compiles the crate."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", ESCAPED_BUILD)
    assert ids(tmp_path) == {"NC-004"}


UNICODE_ESCAPED_BUILD = ESCAPED_BUILD.replace("buil\\x64", "buil\\u{64}")


def test_a_unicode_escape_in_a_subcommand_is_decoded(tmp_path: pathlib.Path) -> None:
    """A Unicode escape spells `build` too, so it cannot hide the compile."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", UNICODE_ESCAPED_BUILD)
    assert ids(tmp_path) == {"NC-004"}


ESCAPED_VERSION_PROBE = (
    "use std::process::Command;\n"
    "#[test]\nfn probes() {\n"
    '    Command::new("rustc").arg("--ver\\x73ion").status().unwrap();\n'
    "}\n"
)


def test_an_escape_in_a_probe_flag_is_decoded(tmp_path: pathlib.Path) -> None:
    """A hexadecimal escape spells `--version`, so the test compiles nothing."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ESCAPED_VERSION_PROBE)
    assert ids(tmp_path) == set()


ESCAPED_QUOTE_BEFORE_A_BUILD = (
    "use std::process::Command;\n"
    "#[test]\nfn compiles() {\n"
    '    Command::new("cargo").args(["a\\"b", "build"]).status().unwrap();\n'
    "}\n"
)


def test_an_escaped_quote_does_not_split_the_literal(tmp_path: pathlib.Path) -> None:
    """The first word is `a"b`, so the later `build` is an argument of it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", ESCAPED_QUOTE_BEFORE_A_BUILD)
    assert ids(tmp_path) == set()


PRINT_IN_EACH_ARM = (
    "use std::process::Command;\n"
    "fn probe(choice: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    if choice {\n"
    '        command.arg("--print");\n'
    "    } else {\n"
    '        command.arg("--print");\n'
    "    }\n"
    '    command.arg("cfg");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_each_arm_is_read_with_the_shared_words_before_the_branch(
    tmp_path: pathlib.Path,
) -> None:
    """`--print` in either arm is completed by the `cfg` given after the choice."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PRINT_IN_EACH_ARM)
    assert ids(tmp_path) == set()


HELP_ON_ONE_PATH_VERSION_ON_THE_OTHER = (
    "use std::process::Command;\n"
    "fn run(choice: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if choice {\n"
    '        command.arg("build").arg("--help");\n'
    "    } else {\n"
    '        command.arg("--version");\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_help_on_one_path_excuses_that_path_alone(tmp_path: pathlib.Path) -> None:
    """The if arm prints help and the else arm its version, so nothing builds."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELP_ON_ONE_PATH_VERSION_ON_THE_OTHER)
    assert ids(tmp_path) == set()


HELP_ON_ONE_PATH_BUILD_ON_THE_OTHER = HELP_ON_ONE_PATH_VERSION_ON_THE_OTHER.replace(
    'command.arg("--version")', 'command.arg("check")'
)


def test_help_on_one_path_does_not_excuse_the_other(tmp_path: pathlib.Path) -> None:
    """The else arm still type-checks the crate without a help flag."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELP_ON_ONE_PATH_BUILD_ON_THE_OTHER)
    assert ids(tmp_path) == {"NC-004"}


CLOSURE_PARAMETER_SHADOWS_THE_HELPER = (
    "use std::process::Command;\n"
    "fn configure(target: &mut Command) {\n"
    '    let inspect = |_: (), target: &mut Command| target.arg("--version");\n'
    "}\n"
    "fn probe() {\n"
    '    let mut cmd = Command::new("rustc");\n'
    '    cmd.arg("src/lib.rs");\n'
    "    configure(&mut cmd);\n"
    "    cmd.status().unwrap();\n"
    "}\n"
)


def test_a_closure_parameter_hides_the_helpers_parameter(
    tmp_path: pathlib.Path,
) -> None:
    """The closure's own `target` is not the command the helper was given."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CLOSURE_PARAMETER_SHADOWS_THE_HELPER)
    assert ids(tmp_path) == {"NC-004"}


PATTERN_BOUND_PARAMETER = (
    "use std::process::Command;\n"
    "fn configure(cmd: &mut Command) {\n"
    '    cmd.arg("--version");\n'
    "}\n"
    "fn run((configure,): (fn(&mut Command),)) {\n"
    '    let mut cmd = Command::new("rustc");\n'
    '    cmd.arg("input.rs");\n'
    "    configure(&mut cmd);\n"
    "    cmd.status().unwrap();\n"
    "}\n"
)


def test_a_pattern_bound_parameter_hides_the_helper(tmp_path: pathlib.Path) -> None:
    """A tuple pattern binds `configure`, so the call is not the free function."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PATTERN_BOUND_PARAMETER)
    assert ids(tmp_path) == {"NC-004"}


CONDITIONAL_BUILD_ONLY = (
    "use std::process::Command;\n"
    "fn run(flag: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if flag {\n"
    '        command.arg("build");\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_conditional_build_is_still_read_as_compiling(tmp_path: pathlib.Path) -> None:
    """`build` is given on the path through the block, so the run may compile."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITIONAL_BUILD_ONLY)
    assert ids(tmp_path) == {"NC-004"}


STRUCT_PATTERN_BINDS_THE_HELPER = (
    "use std::process::Command;\n"
    "struct Callbacks { setup: fn(&mut Command) }\n"
    "fn configure(command: &mut Command) {\n"
    '    command.arg("--version");\n'
    "}\n"
    "fn run(Callbacks { setup: configure }: Callbacks) {\n"
    '    let mut command = Command::new("rustc");\n'
    '    command.arg("input.rs");\n'
    "    configure(&mut command);\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_struct_pattern_binds_the_helper_name(tmp_path: pathlib.Path) -> None:
    """The pattern's `setup` field binds `configure`, so the call is not the fn."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", STRUCT_PATTERN_BINDS_THE_HELPER)
    assert ids(tmp_path) == {"NC-004"}


COMMAND_IS_THE_BINDING = (
    "use std::process::Command;\n"
    "#[test]\nfn compiles() {\n"
    '    let mut Command = Command::new("cargo");\n'
    '    Command.arg("build").status().unwrap();\n'
    "}\n"
)


def test_a_binding_named_command_is_still_the_command(tmp_path: pathlib.Path) -> None:
    """A parameter named `Command` is another value, but this binding is ours."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", COMMAND_IS_THE_BINDING)
    assert ids(tmp_path) == {"NC-004"}


CLOSURE_PARAMETER_AFTER_ITS_NEIGHBOUR = (
    "use std::process::Command;\n"
    "fn configure(command: &mut Command) {\n"
    '    command.arg("build");\n'
    "    command.status().unwrap();\n"
    "}\n"
    "fn runs(choice: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    let callback = |_: (), configure: fn(&mut Command)| {\n"
    "        configure(&mut command);\n"
    "    };\n"
    "    callback((), |_| {});\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_closure_parameter_after_the_first_hides_the_helper(
    tmp_path: pathlib.Path,
) -> None:
    """The closure's second parameter is still its own, not the free function."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CLOSURE_PARAMETER_AFTER_ITS_NEIGHBOUR)
    assert ids(tmp_path) == set()


@pytest.mark.parametrize(
    "arguments",
    [
        '.arg("--print=cfg=out.txt")',
        '.args(["--print=file-names", "--crate-name", "x"]).arg("fixture.rs")',
    ],
)
def test_a_print_query_written_to_a_file_compiles_nothing(
    tmp_path: pathlib.Path, arguments: str
) -> None:
    """An output path after the query kind, and `file-names`, still only print."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(
        tmp_path,
        "tests/common/mod.rs",
        "use std::process::Command;\n"
        "fn probe() {\n"
        '    Command::new("rustc")\n'
        f"        {arguments}\n"
        "        .status()\n"
        "        .unwrap();\n"
        "}\n",
    )
    assert ids(tmp_path) == set()


UNICODE_SEPARATED_BUILD = (
    "use std::process::Command;\n"
    "#[test]\nfn compiles() {\n"
    '    Command::new("cargo").arg("buil\\u{6_4}").status().unwrap();\n'
    "}\n"
)


def test_a_unicode_escape_may_separate_its_digits(tmp_path: pathlib.Path) -> None:
    """A digit separator is not part of the number, so the word is `build`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", UNICODE_SEPARATED_BUILD)
    assert ids(tmp_path) == {"NC-004"}


MULTILINE_BINDING = (
    "use std::process::Command;\n"
    "#[test]\nfn compiles() {\n"
    "    let mut command =\n"
    '        Command::new("cargo");\n'
    '    command.arg("build").status().unwrap();\n'
    "}\n"
)


def test_a_binding_split_after_the_equals_is_read(tmp_path: pathlib.Path) -> None:
    """The constructor on the next line still binds the name before it."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", MULTILINE_BINDING)
    assert ids(tmp_path) == {"NC-004"}


ELSE_ARM_KEPT_IN_A_HELPER = (
    "use std::process::Command;\n"
    "fn setup() {}\n"
    "fn add(command: &mut Command, choice: bool) {\n"
    "    if choice {\n"
    '        command.arg("-V");\n'
    "    } else {\n"
    "        setup();\n"
    '        command.arg("-vV");\n'
    "    }\n"
    "}\n"
    "fn probe(choice: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    add(&mut command, choice);\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_helper_else_arm_survives_the_splice(tmp_path: pathlib.Path) -> None:
    """A statement with no call on the parameter keeps the arm it belongs to."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ELSE_ARM_KEPT_IN_A_HELPER)
    assert ids(tmp_path) == set()


QUALIFIED_CONSTRUCTOR_IN_A_CHAIN = (
    "use std::process::Command;\n"
    "#[test]\nfn compiles() {\n"
    '    std::process::Command::new("cargo")\n'
    '        .arg("build")\n'
    "        .status()\n"
    "        .unwrap();\n"
    "}\n"
)


def test_a_qualified_constructor_launches_its_command(tmp_path: pathlib.Path) -> None:
    """`std::process::Command` is the constructor, so its launch ends the run."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", QUALIFIED_CONSTRUCTOR_IN_A_CHAIN)
    assert ids(tmp_path) == {"NC-004"}


BUILT_BUT_NEVER_LAUNCHED = (
    "use std::process::Command;\n"
    "#[test]\nfn builds_only() {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("build");\n'
    "}\n"
)


def test_a_command_that_never_launches_spawns_nothing(tmp_path: pathlib.Path) -> None:
    """Arguments alone start no child process, so the test asks for no config."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", BUILT_BUT_NEVER_LAUNCHED)
    assert ids(tmp_path) == set()


RUSTC_BUILT_BUT_NEVER_LAUNCHED = (
    "use std::process::Command;\n"
    "#[test]\nfn builds_only() {\n"
    '    let mut command = Command::new("rustc");\n'
    '    command.arg("input.rs");\n'
    "}\n"
)


def test_a_rustc_command_that_never_launches_spawns_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """A `rustc` command starts no compiler until it is launched."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/ui.rs", RUSTC_BUILT_BUT_NEVER_LAUNCHED)
    assert ids(tmp_path) == set()


LATER_CONDITIONAL_OPTION = (
    "use std::process::Command;\n"
    "fn probe(choice: bool, verbose: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if choice {\n"
    '        command.arg("metadata");\n'
    "    } else {\n"
    '        command.arg("build");\n'
    "    }\n"
    "    if verbose {\n"
    '        command.arg("-v");\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_later_conditional_option_keeps_the_arms_readable(
    tmp_path: pathlib.Path,
) -> None:
    """A `-v` in its own block does not merge into the paired choice's arms."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", LATER_CONDITIONAL_OPTION)
    assert ids(tmp_path) == {"NC-004"}


PRINT_KIND_WRITTEN_TO_A_FILE = (
    "use std::process::Command;\n"
    "fn probe() {\n"
    '    Command::new("rustc")\n'
    '        .args(["--print", "cfg=out.txt"])\n'
    "        .status()\n"
    "        .unwrap();\n"
    "}\n"
)


def test_a_two_word_print_query_written_to_a_file_compiles_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """An output path after the kind leaves the query a query in the split form too."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PRINT_KIND_WRITTEN_TO_A_FILE)
    assert ids(tmp_path) == set()


HELPER_RETURN_INSIDE_A_BLOCK = (
    "use std::process::Command;\n"
    "fn configure(command: &mut Command, skip: bool) {\n"
    "    if skip { return }\n"
    "    let quiet = 1;\n"
    '    command.arg("--print").arg("cfg");\n'
    "}\n"
    "fn probe(skip: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    '    command.arg("input.rs");\n'
    "    configure(&mut command, skip);\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_return_inside_a_helper_block_bounds_its_path(tmp_path: pathlib.Path) -> None:
    """The print is skipped on the returning path, so that path compiles."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELPER_RETURN_INSIDE_A_BLOCK)
    assert ids(tmp_path) == {"NC-004"}


STRUCT_PATTERN_COMMA_KEEPS_THE_PARAMETERS = (
    "use std::process::Command;\n"
    "struct Pair { first: u8, command: u8 }\n"
    "fn configure(Pair { first, command: other }: Pair, cmd: &mut Command) {\n"
    '    cmd.arg("build");\n'
    "    cmd.status().unwrap();\n"
    "}\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    let pair = Pair { first: 1, command: 2 };\n"
    "    configure(pair, &mut command);\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_struct_pattern_comma_does_not_split_the_parameters(
    tmp_path: pathlib.Path,
) -> None:
    """The field comma stays inside the pattern, so `cmd` is the second parameter."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", STRUCT_PATTERN_COMMA_KEEPS_THE_PARAMETERS)
    assert ids(tmp_path) == {"NC-004"}


CONST_BLOCK_COMPARISON = (
    "use std::process::Command;\n"
    "fn configure(cmd: &mut Command) {\n"
    '    cmd.arg("--version");\n'
    "}\n"
    "fn run(\n"
    "    flag: Flag<{ 1 < 2 }>,\n"
    "    (configure,): (fn(&mut Command),),\n"
    ") {\n"
    '    let mut cmd = Command::new("rustc");\n'
    '    cmd.arg("input.rs");\n'
    "    configure(&mut cmd);\n"
    "    cmd.status().unwrap();\n"
    "}\n"
)


def test_a_comparison_in_a_const_block_does_not_merge_the_parameters(
    tmp_path: pathlib.Path,
) -> None:
    """The `<` is a comparison, so the tuple pattern's binding still shadows."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONST_BLOCK_COMPARISON)
    assert ids(tmp_path) == {"NC-004"}


SIBLING_BUILD_BESIDE_METADATA = (
    "use std::process::Command;\n"
    "fn run(a: bool, b: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if a {\n"
    '        command.arg("metadata");\n'
    "    }\n"
    "    if b {\n"
    '        command.arg("build");\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_conditional_build_beside_metadata_still_compiles(
    tmp_path: pathlib.Path,
) -> None:
    """With `a` false and `b` true the command runs `cargo build`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SIBLING_BUILD_BESIDE_METADATA)
    assert ids(tmp_path) == {"NC-004"}


UNCONDITIONAL_METADATA_BESIDE_BUILD = (
    "use std::process::Command;\n"
    "fn run(b: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("metadata");\n'
    "    if b {\n"
    '        command.arg("build");\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_an_unconditional_metadata_beside_a_build_runs_metadata(
    tmp_path: pathlib.Path,
) -> None:
    """`metadata` is the subcommand on every path, so no path builds."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", UNCONDITIONAL_METADATA_BESIDE_BUILD)
    assert ids(tmp_path) == set()


CONDITIONAL_SEPARATOR_BEFORE_HELP = (
    "use std::process::Command;\n"
    "fn run(forward: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("test");\n'
    '    if forward { command.arg("--"); }\n'
    '    command.arg("--help");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_help_flag_after_a_conditional_separator_still_compiles(
    tmp_path: pathlib.Path,
) -> None:
    """On the path that writes `--`, cargo passes `--help` to the test binary."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITIONAL_SEPARATOR_BEFORE_HELP)
    assert ids(tmp_path) == {"NC-004"}


CLOSURE_PARAMETER_SCOPE_ENDS_WITH_ITS_STATEMENT = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    let _noop = |command: &mut Command| {};\n"
    '    command.arg("build");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_closure_parameter_shadows_only_its_own_body(tmp_path: pathlib.Path) -> None:
    """`command` denotes the outer binding again once the closure has ended."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(
        tmp_path, "tests/common/mod.rs", CLOSURE_PARAMETER_SCOPE_ENDS_WITH_ITS_STATEMENT
    )
    assert ids(tmp_path) == {"NC-004"}


UNLAUNCHED_RETURN_PATH_IS_NOT_A_RUN = (
    "use std::process::Command;\n"
    "fn probe(skip: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    '    command.arg("input.rs");\n'
    "    if skip { return; }\n"
    '    command.arg("--version");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_returning_path_without_a_launch_is_not_a_run(
    tmp_path: pathlib.Path,
) -> None:
    """The path that returns starts no process, so it asks no probe question."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", UNLAUNCHED_RETURN_PATH_IS_NOT_A_RUN)
    assert ids(tmp_path) == set()


CONDITIONAL_OPTION_VALUE = (
    "use std::process::Command;\n"
    "fn run(verbose: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("--color");\n'
    '    if verbose { command.arg("never"); }\n'
    '    command.arg("always");\n'
    '    command.arg("build");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_conditional_word_does_not_fill_an_option_value_slot(
    tmp_path: pathlib.Path,
) -> None:
    """With the block skipped, `--color always` takes the value and `build` runs."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITIONAL_OPTION_VALUE)
    assert ids(tmp_path) == {"NC-004"}


CLOSURE_PARAMETER_AFTER_THE_FIRST = (
    "use std::process::Command;\n"
    "fn probe() {\n"
    '    let mut command = Command::new("rustc");\n'
    '    command.arg("input.rs");\n'
    "    let _noop = |_: (), command: &mut Command| {\n"
    "        command.status().unwrap()\n"
    "    };\n"
    "}\n"
)


def test_a_later_closure_parameter_also_shadows_the_command(
    tmp_path: pathlib.Path,
) -> None:
    """The closure's launch is its own, so the outer command never runs."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CLOSURE_PARAMETER_AFTER_THE_FIRST)
    assert ids(tmp_path) == set()


ENCLOSING_FUNCTION_OWNS_THE_PARAMETERS = (
    "use std::process::Command;\n"
    "fn configure(command: &mut Command) {\n"
    '    command.arg("--help");\n'
    "}\n"
    "fn run(configure: fn(&mut Command)) {\n"
    "    fn unrelated() {}\n"
    '    let mut command = Command::new("cargo");\n'
    "    configure(&mut command);\n"
    '    command.arg("build");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_the_enclosing_function_owns_the_caller_parameters(
    tmp_path: pathlib.Path,
) -> None:
    """`configure` is `run`'s callback, so the call declines the free function."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ENCLOSING_FUNCTION_OWNS_THE_PARAMETERS)
    assert ids(tmp_path) == {"NC-004"}


TUPLE_STRUCT_PATTERN_BINDS_THE_HELPER = (
    "use std::process::Command;\n"
    "struct Hook(fn(&mut Command));\n"
    "fn configure(command: &mut Command) {\n"
    '    command.arg("--help");\n'
    "}\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    let hook = Hook(noop);\n"
    "    let Hook(configure) = hook;\n"
    "    configure(&mut command);\n"
    '    command.arg("build");\n'
    "    command.status().unwrap();\n"
    "}\n"
    "fn noop(_command: &mut Command) {}\n"
)


def test_a_tuple_struct_pattern_binds_the_helper_name(tmp_path: pathlib.Path) -> None:
    """`configure` is the callback the pattern bound, not the free function."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", TUPLE_STRUCT_PATTERN_BINDS_THE_HELPER)
    assert ids(tmp_path) == {"NC-004"}


CALLER_COMPILE_AFTER_A_BLOCK_LEVEL_RETURN = (
    "use std::process::Command;\n"
    "fn configure(command: &mut Command, skip: bool) {\n"
    "    if skip {\n"
    "        return;\n"
    "    }\n"
    '    command.arg("--version");\n'
    "    command.status().unwrap();\n"
    "}\n"
    "fn run(skip: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    configure(&mut command, skip);\n"
    '    command.arg("input.rs");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_the_callers_compile_after_a_block_level_return_is_read(
    tmp_path: pathlib.Path,
) -> None:
    """The helper's probe does not excuse the caller's compiling launch."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CALLER_COMPILE_AFTER_A_BLOCK_LEVEL_RETURN)
    assert ids(tmp_path) == {"NC-004"}


SURVIVING_ARM_OF_A_RETURN = (
    "use std::process::Command;\n"
    "fn probe(skip: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    '    if skip { return; } else { command.arg("--version"); }\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_the_surviving_arm_of_a_return_is_the_only_path_to_the_launch(
    tmp_path: pathlib.Path,
) -> None:
    """The path that skipped the return is the only one that launches."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SURVIVING_ARM_OF_A_RETURN)
    assert ids(tmp_path) == set()


CLOSURE_PARAMETER_BESIDE_A_LAUNCH = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    '    let _ = (|command: &mut Command| {}, command.arg("build").status());\n'
    "}\n"
)


def test_a_closure_parameter_does_not_hide_a_launch_beside_it(
    tmp_path: pathlib.Path,
) -> None:
    """The tuple's second element is the outer command, not the closure's."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CLOSURE_PARAMETER_BESIDE_A_LAUNCH)
    assert ids(tmp_path) == {"NC-004"}


SEPARATOR_ON_ONE_PATH_BESIDE_A_PAIRED_HELP = (
    "use std::process::Command;\n"
    "fn run(forward: bool, alpha: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("test");\n'
    '    if forward { command.arg("--"); }\n'
    "    if alpha {\n"
    '        command.arg("--help");\n'
    '        command.arg("alpha");\n'
    "    } else {\n"
    '        command.arg("--help");\n'
    '        command.arg("beta");\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_separator_on_one_path_survives_the_paired_help(
    tmp_path: pathlib.Path,
) -> None:
    """With `--` written, cargo passes `--help` to the test binary and compiles."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SEPARATOR_ON_ONE_PATH_BESIDE_A_PAIRED_HELP)
    assert ids(tmp_path) == {"NC-004"}


TWO_CONDITIONAL_WORDS_BEFORE_A_TOPIC = (
    "use std::process::Command;\n"
    "fn run(search: bool, install: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    if search { command.arg("search"); }\n'
    '    if install { command.arg("install"); }\n'
    '    command.arg("ripgrep");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_each_conditional_word_can_be_the_subcommand(tmp_path: pathlib.Path) -> None:
    """With `search` skipped, `install` is the subcommand and compiles."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", TWO_CONDITIONAL_WORDS_BEFORE_A_TOPIC)
    assert ids(tmp_path) == {"NC-004"}


SEMICOLON_INSIDE_A_BLOCK_ARGUMENT = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    command\n"
    '        .arg("build")\n'
    "        .arg({\n"
    '            let flag = "--quiet";\n'
    "            flag\n"
    "        })\n"
    "        .status()\n"
    "        .unwrap();\n"
    "}\n"
)


def test_a_semicolon_inside_a_block_argument_does_not_end_the_chain(
    tmp_path: pathlib.Path,
) -> None:
    """The block's own `;` is not a statement boundary, so the launch is read."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", SEMICOLON_INSIDE_A_BLOCK_ARGUMENT)
    assert ids(tmp_path) == {"NC-004"}


CONST_DECLARED_AFTER_THE_CALL = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("rustc");\n'
    "    configure(&mut command);\n"
    '    command.arg("input.rs");\n'
    "    command.status().unwrap();\n"
    "    const configure: fn(&mut Command) = noop;\n"
    "}\n"
    "fn configure(command: &mut Command) {\n"
    '    command.arg("--version");\n'
    "}\n"
    "fn noop(_command: &mut Command) {}\n"
)


def test_a_const_declared_later_in_the_block_shadows_the_helper(
    tmp_path: pathlib.Path,
) -> None:
    """A block item is in scope throughout its block, before its declaration."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONST_DECLARED_AFTER_THE_CALL)
    assert ids(tmp_path) == {"NC-004"}


DEFERRED_CLOSURE_HANDOFF = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("rustc");\n'
    "    let deferred = |_: ()| configure(&mut command);\n"
    "    drop(deferred);\n"
    '    command.arg("input.rs");\n'
    "    command.status().unwrap();\n"
    "}\n"
    "fn configure(command: &mut Command) {\n"
    '    command.arg("--version");\n'
    "}\n"
)


def test_a_helper_called_only_inside_a_closure_is_not_spliced(
    tmp_path: pathlib.Path,
) -> None:
    """A closure body runs only when the closure is called, so it adds nothing."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", DEFERRED_CLOSURE_HANDOFF)
    assert ids(tmp_path) == {"NC-004"}


@pytest.mark.parametrize(
    "arguments",
    [
        '.args(["--print", "crate-name"]).arg("fixture.rs")',
        '.arg("--print=crate-name").arg("fixture.rs")',
    ],
)
def test_a_crate_name_print_is_a_probe(arguments: str, tmp_path: pathlib.Path) -> None:
    """`--print crate-name` prints the name and exits without compiling."""
    source = RUSTC_TEST.replace('.arg("fixture.rs")', arguments)
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", source)
    assert ids(tmp_path) == set()


ARGUMENT_AFTER_A_LEADING_BRACE = (
    "use std::process::Command;\n"
    "fn run(a: bool, b: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    '    if a { setup(); } command.arg("--version");\n'
    "    if b {\n"
    '        command.arg("input.rs");\n'
    "        command.status().unwrap();\n"
    "    } else {\n"
    '        command.arg("other.rs");\n'
    "        command.status().unwrap();\n"
    "    }\n"
    "}\n"
    "fn setup() {}\n"
)


def test_an_argument_after_a_leading_brace_is_on_every_path(
    tmp_path: pathlib.Path,
) -> None:
    """`} command.arg(..)` closes a block, so its argument is not conditional."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ARGUMENT_AFTER_A_LEADING_BRACE)
    assert ids(tmp_path) == set()


def test_a_closure_return_type_does_not_end_its_span() -> None:
    """A `-> Result<(), ()>` body is part of the closure, commas and all."""
    source = (
        "let f = |command: &mut Command| -> Result<(), ()> "
        '{ command.arg("--version"); Ok(()) };'
    )
    ((start, stop),) = closure_spans(source)
    assert start < source.index("command.arg") < stop


CLOSURE_RUNNING_LATER_IN_ITS_BODY = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("rustc");\n'
    '    command.arg("input.rs");\n'
    "    let _later = |command: &mut Command| {\n"
    '        command.arg("other.rs");\n'
    "        command.status().unwrap();\n"
    "    };\n"
    "}\n"
)


def test_a_closure_body_keeps_hiding_across_its_own_semicolon(
    tmp_path: pathlib.Path,
) -> None:
    """A `;` inside the body splits the statement, not the closure's scope."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CLOSURE_RUNNING_LATER_IN_ITS_BODY)
    assert ids(tmp_path) == set()


def test_closure_hiding_reads_the_text_before_the_statement() -> None:
    """A statement after a body's `;` is hidden by the header before it."""
    source = 'let f = |command: &mut Command| { command.arg("x");\ncommand.status(); };'
    begin = source.index("command.status")
    stop = source.index(";", begin)
    ((start, end),) = _closure_hiding(source[:stop], begin, stop)
    assert (start, end) == (0, stop - begin)


HELP_BESIDE_A_CONDITIONAL_SUBCOMMAND = (
    "use std::process::Command;\n"
    "fn run(show: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    if show { command.args(["build", "--help"]); }\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_help_flag_beside_a_conditional_subcommand_excuses_it(
    tmp_path: pathlib.Path,
) -> None:
    """The block that gives `build` gives `--help` with it, so nothing builds."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELP_BESIDE_A_CONDITIONAL_SUBCOMMAND)
    assert ids(tmp_path) == set()


BITWISE_OR_AROUND_A_LAUNCH = (
    "use std::process::Command;\n"
    "fn run(a: bool, b: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    let ok = a | command.arg("build").status().unwrap().success() | b;\n'
    "    let _ = ok;\n"
    "}\n"
)


def test_a_bitwise_or_is_not_a_closure_header(tmp_path: pathlib.Path) -> None:
    """A `|` between two operands is an operator, not a parameter list."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", BITWISE_OR_AROUND_A_LAUNCH)
    assert ids(tmp_path) == {"NC-004"}


HELP_ONLY_ARMS_BESIDE_AN_OPTION = (
    "use std::process::Command;\n"
    "fn run(quiet: bool, verbose: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    command.arg("build");\n'
    '    if quiet { command.arg("--help"); } else { command.arg("-h"); }\n'
    '    if verbose { command.arg("-v"); }\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_help_only_arms_survive_an_option_only_block_beside_them(
    tmp_path: pathlib.Path,
) -> None:
    """Both arms of the choice give help; the `-v` block beside it is not an arm."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELP_ONLY_ARMS_BESIDE_AN_OPTION)
    assert ids(tmp_path) == set()


CONST_IN_THE_ARM_THAT_CALLS = (
    "use std::process::Command;\n"
    "fn run(flag: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    if flag {\n"
    "        const configure: fn(&mut Command) = noop;\n"
    "        configure(&mut command);\n"
    "    } else {\n"
    "        configure(&mut command);\n"
    "    }\n"
    '    command.arg("input.rs");\n'
    "    command.status().unwrap();\n"
    "}\n"
    'fn configure(command: &mut Command) { command.arg("--version"); }\n'
    "fn noop(_command: &mut Command) {}\n"
)


def test_a_const_in_the_arms_block_shadows_the_helper(tmp_path: pathlib.Path) -> None:
    """An item is in scope throughout its block, so that arm calls the constant."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONST_IN_THE_ARM_THAT_CALLS)
    assert ids(tmp_path) == {"NC-004"}


CONST_IN_A_SIBLING_BLOCK = (
    "use std::process::Command;\n"
    "fn run(flag: bool) {\n"
    '    let mut command = Command::new("rustc");\n'
    "    if flag {\n"
    "        configure(&mut command);\n"
    "    } else {\n"
    "        configure(&mut command);\n"
    "    }\n"
    "    { const configure: fn(&mut Command) = noop; }\n"
    '    command.arg("input.rs");\n'
    "    command.status().unwrap();\n"
    "}\n"
    'fn configure(command: &mut Command) { command.arg("--version"); }\n'
    "fn noop(_command: &mut Command) {}\n"
)


def test_a_const_in_a_sibling_block_does_not_shadow_the_helper(
    tmp_path: pathlib.Path,
) -> None:
    """The constant's block opens and closes apart from the calls it does not hold."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONST_IN_A_SIBLING_BLOCK)
    assert ids(tmp_path) == set()


MATCH_ARM_BINDING = (
    "use std::process::Command;\n"
    "fn run(callback: fn(&mut Command)) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    match callback { configure => configure(&mut command) }\n"
    '    command.arg("metadata").status().unwrap();\n'
    "}\n"
    'fn configure(command: &mut Command) { command.arg("build"); }\n'
)


def test_a_match_arm_binding_hides_the_helper(tmp_path: pathlib.Path) -> None:
    """The arm's pattern binds `configure` to the callback, not the free `fn`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", MATCH_ARM_BINDING)
    assert ids(tmp_path) == set()


ELSE_IF_ARM_IS_NOT_THE_ONLY_PATH = (
    "use std::process::Command;\n"
    "fn run(a: bool, b: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if a { return; }\n"
    '    else if b { command.arg("--help"); }\n'
    '    else { command.arg("build"); }\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_an_else_if_arm_is_not_the_only_path_to_the_launch(
    tmp_path: pathlib.Path,
) -> None:
    """`a` returning leaves `b` or the final `else`, so the help is conditional."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ELSE_IF_ARM_IS_NOT_THE_ONLY_PATH)
    assert ids(tmp_path) == {"NC-004"}


EXPRESSION_CLOSURE_BEFORE_A_LAUNCH = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    '    let noop = |command: &mut Command| command.arg("x");\n'
    '    command.arg("build");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_an_expression_closure_ends_at_its_semicolon(
    tmp_path: pathlib.Path,
) -> None:
    """The closure ends with its expression, so the launch beside it compiles."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", EXPRESSION_CLOSURE_BEFORE_A_LAUNCH)
    assert ids(tmp_path) == {"NC-004"}


CONDITIONAL_HELP_BESIDE_METADATA = (
    "use std::process::Command;\n"
    "fn run(show: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    if show { command.args(["build", "--help"]); }\n'
    '    command.arg("metadata");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_conditional_help_does_not_make_the_metadata_compile(
    tmp_path: pathlib.Path,
) -> None:
    """One path asks for help and the other runs `metadata`; neither compiles."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITIONAL_HELP_BESIDE_METADATA)
    assert ids(tmp_path) == set()


GUARDED_HELP_BESIDE_A_BUILD = (
    "use std::process::Command;\n"
    "fn run(show: bool, query: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    if show {\n"
    '        command.arg("build");\n'
    '        query && command.arg("--help").get_args().next().is_some();\n'
    "    }\n"
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_guarded_help_flag_does_not_excuse_the_build(
    tmp_path: pathlib.Path,
) -> None:
    """`query` false leaves the build without help, so the command compiles."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", GUARDED_HELP_BESIDE_A_BUILD)
    assert ids(tmp_path) == {"NC-004"}


CAPTURING_CLOSURE_DEFERS_ITS_LAUNCH = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    '    let deferred = || command.arg("build").status();\n'
    "    drop(deferred);\n"
    "}\n"
)


def test_a_capturing_closure_defers_its_launch(tmp_path: pathlib.Path) -> None:
    """Building the closure starts no process, so its body is no launch."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CAPTURING_CLOSURE_DEFERS_ITS_LAUNCH)
    assert ids(tmp_path) == set()


CONDITIONAL_SEPARATOR_FORWARDS_HELP = (
    "use std::process::Command;\n"
    "fn run(show: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    '    if show { command.args(["test", "--", "--help"]); }\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_help_after_a_separator_on_a_conditional_path_still_compiles(
    tmp_path: pathlib.Path,
) -> None:
    """The `--` sends `--help` to the test binary, so the run compiles tests."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CONDITIONAL_SEPARATOR_FORWARDS_HELP)
    assert ids(tmp_path) == {"NC-004"}


STATIC_MUT_SHADOWS_THE_HELPER = (
    "use std::process::Command;\n"
    "fn configure(target: &mut Command) {\n"
    '    target.arg("build");\n'
    "}\n"
    "fn run() {\n"
    "    static mut configure: fn(&mut Command) = noop;\n"
    '    let mut command = Command::new("cargo");\n'
    "    unsafe { configure(&mut command); }\n"
    '    command.arg("metadata");\n'
    "    command.status().unwrap();\n"
    "}\n"
    "fn noop(_target: &mut Command) {}\n"
)


def test_a_mutable_static_shadows_the_helper(tmp_path: pathlib.Path) -> None:
    """The static binds `configure` in its block, so the free `fn` is not called."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", STATIC_MUT_SHADOWS_THE_HELPER)
    assert ids(tmp_path) == set()


DEFERRED_PARAMETER_USE_IN_A_HELPER = (
    "use std::process::Command;\n"
    "fn configure(target: &mut Command) {\n"
    '    let _deferred = || { setup(); target.arg("build"); };\n'
    "}\n"
    "fn probe() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    configure(&mut command);\n"
    '    command.arg("metadata").status().unwrap();\n'
    "}\n"
    "fn setup() {}\n"
)


def test_a_parameter_use_inside_a_deferred_closure_is_not_spliced(
    tmp_path: pathlib.Path,
) -> None:
    """The helper's only `build` is deferred, so the caller runs `metadata`."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", DEFERRED_PARAMETER_USE_IN_A_HELPER)
    assert ids(tmp_path) == set()


MATCH_ARM_TARGET_BESIDE_THE_PARAMETER = (
    "use std::process::Command;\n"
    "fn configure(target: &mut Command, other: Option<&mut Command>) {\n"
    "    match other {\n"
    '        Some(mut target) => { target.arg("build"); }\n'
    "        None => {}\n"
    "    }\n"
    "}\n"
    "fn probe() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    configure(&mut command, None);\n"
    '    command.arg("metadata").status().unwrap();\n'
    "}\n"
)


def test_a_match_arm_target_is_not_the_helpers_parameter(
    tmp_path: pathlib.Path,
) -> None:
    """The arm's `target` is the other command, so its `build` is not spliced."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", MATCH_ARM_TARGET_BESIDE_THE_PARAMETER)
    assert ids(tmp_path) == set()


ARGUMENT_RETURNED_WITH_THE_HELP = (
    "use std::process::Command;\n"
    "fn configure(target: &mut Command) -> &mut Command {\n"
    '    return target.arg("--help");\n'
    "}\n"
    "fn probe() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    configure(&mut command);\n"
    '    command.arg("build");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_a_returned_argument_is_not_conditional(tmp_path: pathlib.Path) -> None:
    """The return expression runs before control leaves, so the help is certain."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ARGUMENT_RETURNED_WITH_THE_HELP)
    assert ids(tmp_path) == set()


CLOSURE_AFTER_A_BITWISE_PAIR = (
    "use std::process::Command;\n"
    "fn configure(command: &mut Command) {\n"
    "    let _both = a | b;\n"
    '    let deferred = || { command.arg("--version"); };\n'
    "    drop(deferred);\n"
    '    command.arg("build").status().unwrap();\n'
    "}\n"
    "fn run(a: bool, b: bool) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    configure(&mut command);\n"
    "}\n"
)


def test_a_closure_after_a_bitwise_pair_is_still_a_closure(
    tmp_path: pathlib.Path,
) -> None:
    """The closure ends the helper's read, so its later launch is not spliced."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CLOSURE_AFTER_A_BITWISE_PAIR)
    assert ids(tmp_path) == set()


def test_a_function_pointer_return_does_not_end_the_closure_span() -> None:
    """The arrow inside `fn() -> ()` closes no generic, so the body stays read."""
    source = (
        "let f = |command: &mut Command| -> Result<fn() -> (), ()> "
        '{ command.arg("--version"); Ok(noop) };'
    )
    ((start, stop),) = closure_spans(source)
    assert start < source.index("command.arg") < stop


DESTRUCTURED_LOOP_BINDING = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    for (configure,) in [(noop as fn(&mut Command),)] {\n"
    "        configure(&mut command);\n"
    "    }\n"
    "}\n"
    'fn configure(command: &mut Command) { command.arg("build").status().unwrap(); }\n'
    "fn noop(_command: &mut Command) {}\n"
)


def test_a_destructured_loop_binding_hides_the_helper(tmp_path: pathlib.Path) -> None:
    """The loop binds `configure` to `noop`, so its body never launches cargo."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", DESTRUCTURED_LOOP_BINDING)
    assert ids(tmp_path) == set()


HELPER_CHAIN_THROUGH_A_BLOCK_ARGUMENT = (
    "use std::process::Command;\n"
    "fn configure(target: &mut Command) {\n"
    '    target.arg("build").arg({ let flag = "--quiet"; flag }).status().unwrap();\n'
    "}\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    configure(&mut command);\n"
    "}\n"
)


def test_a_semicolon_inside_a_helpers_block_argument_keeps_the_chain(
    tmp_path: pathlib.Path,
) -> None:
    """The helper's `;` is inside the block argument, so its launch is read."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", HELPER_CHAIN_THROUGH_A_BLOCK_ARGUMENT)
    assert ids(tmp_path) == {"NC-004"}


EXPIRED_LOCAL_BINDING = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut command = Command::new("cargo");\n'
    "    { let configure = noop; }\n"
    "    configure(&mut command);\n"
    '    command.arg("metadata").status().unwrap();\n'
    "}\n"
    'fn configure(command: &mut Command) { command.arg("build"); }\n'
    "fn noop(_command: &mut Command) {}\n"
)


def test_an_expired_local_binding_does_not_hide_the_helper(
    tmp_path: pathlib.Path,
) -> None:
    """A `let` in a block that has closed binds nothing at a later call."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", EXPIRED_LOCAL_BINDING)
    assert ids(tmp_path) == {"NC-004"}


LATER_MATCH_ARM_CALLS_THE_HELPER = (
    "use std::process::Command;\n"
    "fn run(callback: Option<fn(&mut Command)>) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    match callback {\n"
    "        Some(configure) => { drop(configure); },\n"
    "        None => configure(&mut command),\n"
    "    }\n"
    '    command.arg("metadata").status().unwrap();\n'
    "}\n"
    'fn configure(command: &mut Command) { command.arg("build"); }\n'
)


def test_a_later_match_arm_does_not_inherit_an_earlier_binding(
    tmp_path: pathlib.Path,
) -> None:
    """Only the arm holding the call decides what its name denotes."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", LATER_MATCH_ARM_CALLS_THE_HELPER)
    assert ids(tmp_path) == {"NC-004"}


ASYNC_HELPER = (
    "use std::process::Command;\n"
    "async fn configure(command: &mut Command) {\n"
    '    command.arg("--version");\n'
    "}\n"
    "fn run() {\n"
    '    let mut command = Command::new("rustc");\n'
    "    let _future = configure(&mut command);\n"
    '    command.arg("input.rs");\n'
    "    command.status().unwrap();\n"
    "}\n"
)


def test_an_async_helper_is_not_spliced_at_the_call(tmp_path: pathlib.Path) -> None:
    """Dropping the future runs nothing, so the helper's `--version` is not given."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", ASYNC_HELPER)
    assert ids(tmp_path) == {"NC-004"}


COMMENTED_CONSTRUCTOR = (
    "use std::process::Command;\n"
    "fn run(cmd: &mut Command) {\n"
    '    // let mut cmd = Command::new("PROGRAM");\n'
    '    cmd.arg("build");\n'
    "    cmd.status().unwrap();\n"
    "}\n"
)


@pytest.mark.parametrize("program", ["rustc", "cargo"])
def test_a_commented_constructor_is_not_a_spawn(
    program: str, tmp_path: pathlib.Path
) -> None:
    """A constructor in a comment starts nothing, so it is not a candidate."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    source = COMMENTED_CONSTRUCTOR.replace("PROGRAM", program)
    put(tmp_path, "tests/common/mod.rs", source)
    assert ids(tmp_path) == set()


CONTAINS_MATCHERS_AROUND_A_NAMED_BINARY = (
    "!test(~src/foo) | binary(ui) | test(~bar/baz)"
)


def test_a_slash_in_a_contains_matcher_is_not_a_regex() -> None:
    """An unpaired slash stays data, so the group naming `ui` is still selected."""
    assert _predicate_arguments(CONTAINS_MATCHERS_AROUND_A_NAMED_BINARY) == [
        "ui",
        "~bar/baz",
    ]


COMMENT_BEFORE_THE_CONSTRUCTOR = (
    "use std::process::Command;\n"
    "fn run() {\n"
    '    let mut cmd = /* fixture */ Command::new("rustc");\n'
    '    cmd.arg("input.rs").status().unwrap();\n'
    "}\n"
)


def test_a_comment_before_the_constructor_still_binds_the_command(
    tmp_path: pathlib.Path,
) -> None:
    """Rust reads the comment as whitespace, so `cmd` is the bound command."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", COMMENT_BEFORE_THE_CONSTRUCTOR)
    assert ids(tmp_path) == {"NC-004"}


CARGO_LOOKUP_AS_THE_CONSTRUCTOR_ARGUMENT = (
    "use std::process::Command;\n"
    "#[test]\nfn builds() {\n"
    '    Command::new(std::env::var_os("CARGO").unwrap())\n'
    '        .arg("build")\n'
    "        .status()\n"
    "        .unwrap();\n"
    "}\n"
)


def test_a_cargo_lookup_as_the_constructor_argument_is_read(
    tmp_path: pathlib.Path,
) -> None:
    """The window starts at the constructor, so its `build` launch is read."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", CARGO_LOOKUP_AS_THE_CONSTRUCTOR_ARGUMENT)
    assert ids(tmp_path) == {"NC-004"}


PARENTHESISED_ARM_EXPRESSION = (
    "use std::process::Command;\n"
    "fn run(callback: fn(&mut Command)) {\n"
    '    let mut command = Command::new("cargo");\n'
    "    match callback { configure => noop() + configure(&mut command) }\n"
    '    command.arg("metadata").status().unwrap();\n'
    "}\n"
    'fn configure(command: &mut Command) { command.arg("build"); }\n'
    "fn noop() -> usize { 0 }\n"
)


def test_a_closed_call_does_not_end_the_match_arm(tmp_path: pathlib.Path) -> None:
    """The arm's pattern still binds `configure` after `noop()` has closed."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", PARENTHESISED_ARM_EXPRESSION)
    assert ids(tmp_path) == set()


LOOP_PATTERN_REBINDS_THE_PARAMETER = (
    "use std::process::Command;\n"
    "fn configure(target: &mut Command, other: &mut Command) {\n"
    "    for (target,) in [(other,)] {\n"
    '        target.arg("build").status().unwrap();\n'
    "    }\n"
    "}\n"
    "fn run() {\n"
    '    let mut other = Command::new("echo");\n'
    '    let mut command = Command::new("cargo");\n'
    "    configure(&mut command, &mut other);\n"
    "}\n"
)


def test_a_loop_pattern_rebinding_the_parameter_is_not_spliced(
    tmp_path: pathlib.Path,
) -> None:
    """The loop's `target` is `other`, so its `build` launch is not the caller's."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", LOOP_PATTERN_REBINDS_THE_PARAMETER)
    assert ids(tmp_path) == set()


def test_an_async_closure_header_is_a_closure() -> None:
    """`async` qualifies the header, so the call inside it is deferred."""
    source = "let _deferred = async || { configure(&mut command); };"
    ((start, stop),) = closure_spans(source)
    assert start < source.index("configure(&mut command)") < stop
