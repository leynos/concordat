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
    build_nested_cargo_nextest_envelope,
)

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
    "    add_build(&mut command, true);\n}\n"
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
    '    if enabled { command.arg("build").status().unwrap() }\n'
    "    let _ = enabled;\n}\n"
)


def test_a_helper_argument_after_a_block_opener_is_retained(
    tmp_path: pathlib.Path,
) -> None:
    """`if enabled { command.arg("build") ... }` still adds the argument."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    put(tmp_path, "tests/common/mod.rs", RECEIVER_AFTER_IF)
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
