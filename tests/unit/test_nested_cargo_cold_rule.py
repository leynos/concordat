"""Audit nested builds through the real policy, fixtures and exemptions.

Each case is a small synthetic checkout shaped like one the rule exists for:
the three timed tests leynos/rstest-bdd#910 describes before and after
leynos/rstest-bdd#916, the per-scenario temporary target in
leynos/ortho-config#611, and the cases whitaker's RFC 0006 listed for a private
target, including the reviewer's point that removing `CARGO_TARGET_DIR` alone
does not prove a directory is not shared.
"""

from __future__ import annotations

import pathlib
import typing as typ

import pygit2
import pytest

from concordat.rules import packages, runner

if typ.TYPE_CHECKING:
    import collections.abc as cabc

RULE_ID = "nested-cargo-cold-build"
LOCK_RULE_ID = "nested-cargo-offline-tests"
NAMESPACE = "canon.lint_rules.nested_cargo_cold_build"
POLICY = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / RULE_ID
    / "policy"
    / "nested_cargo_cold_build.rego"
)

CLI_TEST = """\
fn run_cargo_bdd_steps() -> Result<String> {
    let binary = locate_or_build_binary(&manifest(), BinaryName::new("cargo-bdd"))?;
    run(binary, &["steps"])
}
"""

TRYBUILD_TEST = """\
#[test]
fn step_macros_compile() {
    let t = trybuild::TestCases::new();
    t.pass("tests/fixtures/ok.rs");
}
"""

REBUILD_HARNESS = """\
use std::process::Command;

pub(crate) fn cargo_command(cwd: &std::path::Path) -> Command {
    let mut cmd = Command::new(env!("CARGO"));
    cmd.args(["--color", "never"]);
    cmd.current_dir(cwd);
    cmd
}
"""

PREPARATION = """\
#[test]
fn nested_fixture_preparation() {
    ensure_prepared_with(&family(), || build_everything());
}
"""

GATE_ONLY = """\
#[test]
fn timed_assertion() {
    ensure_prepared(&family());
}
"""

SETUP_SCRIPT = """\
[scripts.setup.prepare-nested]
command = "make prepare-nested-fixtures"

[[profile.default.scripts]]
filter = "{filter}"
setup = "{setup}"
"""

TEMPDIR_TARGET = """\
fn build_scenario() {
    let scratch = tempfile::tempdir().unwrap();
    std::process::Command::new("cargo")
        .arg("build")
        .env("CARGO_TARGET_DIR", scratch.path())
        .status()
        .unwrap();
}
"""

ORTHO_SCENARIO = """\
fn run_orthohelp(ctx: &Context) -> StepResult<()> {
    let target_dir = scenario_target_dir(ctx)?;
    let mut command = Command::new(exe.as_str());
    command
        .current_dir(workspace.as_path())
        .env("CARGO_TARGET_DIR", target_dir.as_str());
    command.output()?;
    Ok(())
}
"""

REMOVE_ONE = """\
fn build_in_scratch() {
    let scratch = tempfile::tempdir().unwrap();
    std::process::Command::new("cargo")
        .arg("build")
        .current_dir(scratch.path())
        .env_remove("CARGO_TARGET_DIR")
        .status()
        .unwrap();
}
"""

REMOVE_BOTH = REMOVE_ONE.replace(
    '.env_remove("CARGO_TARGET_DIR")',
    '.env_remove("CARGO_TARGET_DIR")\n        .env_remove("CARGO_BUILD_TARGET_DIR")',
)

CLEAR_THEN_SHARED = """\
fn build_with_harness_target(shared: &std::path::Path) {
    let scratch = tempfile::tempdir().unwrap();
    std::process::Command::new("cargo")
        .arg("build")
        .current_dir(scratch.path())
        .env_clear()
        .env("CARGO_TARGET_DIR", shared)
        .status()
        .unwrap();
}
"""

TARGET_FLAG = """\
fn build_flag() {
    let scratch = tempfile::tempdir().unwrap();
    std::process::Command::new("cargo")
        .arg("build")
        .arg("--target-dir")
        .arg(scratch.path())
        .status()
        .unwrap();
}
"""

BUILD_TARGET_VARIABLE = TEMPDIR_TARGET.replace(
    "CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR"
).replace(
    '.env("CARGO_BUILD_TARGET_DIR"',
    '.env_remove("CARGO_TARGET_DIR")\n        .env("CARGO_BUILD_TARGET_DIR"',
)

# Cargo reads CARGO_TARGET_DIR first, and a parent may have set it, so the build
# variable alone proves nothing about where the child builds.
BUILD_TARGET_ALONE = TEMPDIR_TARGET.replace(
    "CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR"
)

# After the removal, an environment call the reader cannot read may put either
# variable back.
OPAQUE_AFTER_CLEAR = """\
fn build_in_scratch(extra: &[(String, String)]) {
    let scratch = tempfile::tempdir().unwrap();
    std::process::Command::new("cargo")
        .arg("build")
        .env_clear()
        .envs(extra.iter().cloned())
        .current_dir(scratch.path())
        .status()
        .unwrap();
}
"""

METADATA_PRIVATE = """\
fn read_metadata() {
    let scratch = tempfile::tempdir().unwrap();
    std::process::Command::new("cargo")
        .arg("metadata")
        .env("CARGO_TARGET_DIR", scratch.path())
        .output()
        .unwrap();
}
"""

SHARED_FROM_ENVIRONMENT = """\
fn build_shared() {
    let target = std::env::var("CARGO_TARGET_DIR").unwrap();
    std::process::Command::new("cargo")
        .arg("build")
        .env("CARGO_TARGET_DIR", &target)
        .status()
        .unwrap();
}
"""


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def origin(root: pathlib.Path, url: str) -> None:
    """Initialise a repository at *root* whose origin is *url*."""
    repository = pygit2.init_repository(str(root))
    repository.remotes.create("origin", url)


def findings(root: pathlib.Path) -> tuple[runner.Finding, ...]:
    """Return what the real policy reports for *root* with its shipped defaults.

    Returns
    -------
    tuple[runner.Finding, ...]
        The findings, in the order the policy emitted them.
    """
    return runner.run_rule(RULE_ID, root).findings


def profile(root: pathlib.Path) -> set[tuple[str, str]]:
    """Return the `(rule id, path)` pairs the policy reports for *root*."""
    return {(found.rule_id, found.path) for found in findings(root)}


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
    # The policy reads its parameters through the runner and the envelope
    # builder reads the preparation names through the package registry, so both
    # reads are replaced: one without the other tests half a configuration.
    monkeypatch.setattr(runner, "_rule_parameters", lambda _rule_dir: dict(parameters))
    monkeypatch.setattr(packages, "rule_parameters", lambda _rule_dir: dict(parameters))
    return runner.run_rule(RULE_ID, root)


def rstest_bdd_before(root: pathlib.Path) -> None:
    """Write the three timed tests #910 names, as they were before #916."""
    origin(root, "https://github.com/leynos/rstest-bdd.git")
    for crate in ("crates/cargo-bdd", "crates/rstest-bdd"):
        put(root, f"{crate}/Cargo.toml", f'[package]\nname = "{crate.split("/")[1]}"\n')
    put(root, "crates/cargo-bdd/tests/cli.rs", CLI_TEST)
    put(root, "crates/rstest-bdd/tests/trybuild_macros.rs", TRYBUILD_TEST)
    put(
        root,
        "crates/rstest-bdd/tests/feature_rebuild_invalidation/harness/process.rs",
        REBUILD_HARNESS,
    )


def rstest_bdd_after(root: pathlib.Path) -> None:
    """Add #916's preparation tests and gates to each crate."""
    rstest_bdd_before(root)
    put(root, "crates/cargo-bdd/tests/cli/preparation.rs", PREPARATION)
    put(root, "crates/rstest-bdd/tests/trybuild_macros/preparation.rs", PREPARATION)


def test_the_namespace_is_the_one_the_runner_asks_conftest_for() -> None:
    """Conftest evaluates one namespace; a policy outside it never runs."""
    assert runner._policy_namespace(RULE_ID) == NAMESPACE
    declared = [
        line.split(maxsplit=1)[1].strip()
        for line in POLICY.read_text(encoding="utf-8").splitlines()
        if line.startswith("package ")
    ]
    assert declared == [NAMESPACE]


def test_the_package_resolves_to_the_cold_build_envelope(
    tmp_path: pathlib.Path,
) -> None:
    """The builder mapping and the manifest declaration must agree."""
    envelope = packages.default_envelope_builder(RULE_ID, tmp_path)
    assert envelope["kind"] == "policy-input/nested-cargo-cold-build"


def test_rstest_bdd_before_916_reports_the_three_timed_tests(
    tmp_path: pathlib.Path,
) -> None:
    """The three tests of #910 each draw NC-010, and none draws NC-009.

    None of the three builds into a temporary directory, so a private-target
    check alone would have missed every one of them.
    """
    rstest_bdd_before(tmp_path)
    reported = {(rule, path) for rule, path in profile(tmp_path) if rule != "EN-001"}
    assert reported == {
        ("NC-010", "crates/cargo-bdd/tests/cli.rs"),
        ("NC-010", "crates/rstest-bdd/tests/trybuild_macros.rs"),
        (
            "NC-010",
            "crates/rstest-bdd/tests/feature_rebuild_invalidation/harness/process.rs",
        ),
    }


def test_rstest_bdd_at_916_has_no_cold_build_finding(tmp_path: pathlib.Path) -> None:
    """A preparation test and a gate in each crate declare the preparation.

    The harness helper's arguments are added at call sites in other files, so the
    reader still cannot say where it builds and reports that as indeterminate;
    it is no longer a cold build.
    """
    rstest_bdd_after(tmp_path)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {f.rule_id for f in result.findings} <= {"EN-001"}
    assert result.exit_code == 0


def test_an_ordinary_function_named_like_a_preparation_test_prepares_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """Only an identified test counts: a helper of that name is not run as one."""
    rstest_bdd_before(tmp_path)
    put(
        tmp_path,
        "crates/cargo-bdd/tests/cli/helper.rs",
        "pub fn nested_fixture_preparation() { build_everything(); }\n",
    )
    assert ("NC-010", "crates/cargo-bdd/tests/cli.rs") in profile(tmp_path)


@pytest.mark.parametrize(
    "attribute", ["#[test]", "#[rstest]", "#[tokio::test]", "#[test]\n#[serial]"]
)
def test_a_test_attribute_makes_the_preparation_test(
    tmp_path: pathlib.Path, attribute: str
) -> None:
    """The ordinary test attributes all identify a preparation test."""
    rstest_bdd_before(tmp_path)
    put(
        tmp_path,
        "crates/cargo-bdd/tests/cli/preparation.rs",
        f"{attribute}\nfn nested_fixture_preparation() {{ build_everything(); }}\n",
    )
    assert ("NC-010", "crates/cargo-bdd/tests/cli.rs") not in profile(tmp_path)


def test_a_gate_call_alone_declares_the_preparation(tmp_path: pathlib.Path) -> None:
    """A timed test that refuses on a missing stamp has declared its preparation."""
    rstest_bdd_before(tmp_path)
    put(tmp_path, "crates/cargo-bdd/tests/cli/gate.rs", GATE_ONLY)
    assert ("NC-010", "crates/cargo-bdd/tests/cli.rs") not in profile(tmp_path)
    assert ("NC-010", "crates/rstest-bdd/tests/trybuild_macros.rs") in profile(tmp_path)


WRAPPER = """\
fn cargo(args: &[&str]) -> String {
    let output = std::process::Command::new(env!("CARGO")).args(args).output().unwrap();
    String::from_utf8(output.stdout).unwrap()
}
"""


def test_a_wrapper_with_an_unseen_subcommand_alone_is_only_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo(args)` may run `metadata`, so the reader does not call it a build."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    put(tmp_path, "tests/wrapper.rs", WRAPPER)
    found = {(f.rule_id, f.verdict) for f in findings(tmp_path)}
    assert found == {("EN-001", "indeterminate")}


def test_a_wrapper_in_a_crate_that_compiles_elsewhere_is_a_build(
    tmp_path: pathlib.Path,
) -> None:
    """The crate's own trybuild use settles what the hidden subcommand is."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    put(tmp_path, "tests/wrapper.rs", WRAPPER)
    put(tmp_path, "tests/ui.rs", TRYBUILD_TEST)
    assert ("NC-010", "tests/wrapper.rs") in profile(tmp_path)


def test_a_unit_graph_request_is_not_a_build(tmp_path: pathlib.Path) -> None:
    """`cargo test --unit-graph` prints the units and compiles none."""
    put(
        tmp_path,
        "tests/graph.rs",
        'fn graph(sub: &[&str]) {\n    std::process::Command::new(env!("CARGO"))\n'
        '        .args(sub)\n        .args(["--unit-graph", "--locked"])\n'
        "        .output()\n        .unwrap();\n}\n",
    )
    assert findings(tmp_path) == ()


def test_a_preparation_in_another_crate_prepares_nothing_here(
    tmp_path: pathlib.Path,
) -> None:
    """Preparation is matched by crate, so a sibling's test does not cover this one."""
    rstest_bdd_before(tmp_path)
    put(tmp_path, "crates/rstest-bdd/tests/trybuild_macros/preparation.rs", PREPARATION)
    reported = profile(tmp_path)
    assert ("NC-010", "crates/cargo-bdd/tests/cli.rs") in reported
    assert ("NC-010", "crates/rstest-bdd/tests/trybuild_macros.rs") not in reported


def test_a_repository_names_its_own_gate_in_the_parameters(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another repository's preparation names go in the parameter, not an exemption."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    put(tmp_path, "tests/ui.rs", TRYBUILD_TEST)
    put(tmp_path, "tests/warm.rs", "#[test]\nfn warm_the_cache() { warm(); }\n")
    assert ("NC-010", "tests/ui.rs") in profile(tmp_path)
    parameters = {
        "severity": "warning",
        "exemptions": [],
        "preparation_gates": {"functions": [], "tests": ["^warm_the_cache$"]},
    }
    assert result_with(monkeypatch, tmp_path, parameters).findings == ()


@pytest.mark.parametrize(
    ("script", "is_prepared"),
    [
        (SETUP_SCRIPT.format(filter="all()", setup="prepare-nested"), True),
        (SETUP_SCRIPT.format(filter="binary(ui)", setup="prepare-nested"), True),
        (SETUP_SCRIPT.format(filter="binary(other)", setup="prepare-nested"), False),
        (SETUP_SCRIPT.format(filter="all()", setup="missing"), False),
        # A name in a negated or subtracted filter is named to be left out.
        (SETUP_SCRIPT.format(filter="not binary(ui)", setup="prepare-nested"), False),
        (
            SETUP_SCRIPT.format(filter="all() - binary(ui)", setup="prepare-nested"),
            False,
        ),
        (SETUP_SCRIPT.format(filter="!binary(ui)", setup="prepare-nested"), False),
        # A hyphen inside a binary name is not a subtraction.
        (SETUP_SCRIPT.format(filter="binary(ui-tests)", setup="prepare-nested"), False),
        # An intersection narrows what the selector picks, and a predicate the
        # reader does not evaluate may do the same.
        (
            SETUP_SCRIPT.format(
                filter="binary(ui) & test(=one)", setup="prepare-nested"
            ),
            False,
        ),
        (
            SETUP_SCRIPT.format(
                filter="binary(ui) and kind(test)", setup="prepare-nested"
            ),
            False,
        ),
        # A union of selectors covers what any of them names.
        (
            SETUP_SCRIPT.format(
                filter="binary(ui) | binary(other)", setup="prepare-nested"
            ),
            True,
        ),
        (
            SETUP_SCRIPT.format(
                filter="package(app) or binary(other)", setup="prepare-nested"
            ),
            True,
        ),
    ],
    ids=[
        "covers-every-test",
        "names-the-binary",
        "names-another",
        "undefined-script",
        "not",
        "difference",
        "bang",
        "hyphenated-other-binary",
        "intersection",
        "unsupported-predicate",
        "union-with-bar",
        "union-with-or",
    ],
)
def test_a_nextest_setup_script_prepares_the_tests_it_covers(
    tmp_path: pathlib.Path, script: str, *, is_prepared: bool
) -> None:
    """Nextest runs a covering setup script before the tests it selects."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    put(tmp_path, "tests/ui.rs", TRYBUILD_TEST)
    put(tmp_path, ".config/nextest.toml", script)
    assert (("NC-010", "tests/ui.rs") in profile(tmp_path)) is not is_prepared


FROZEN_HELPER_CALL = """\
fn cargo_command(cwd: &std::path::Path) -> std::process::Command {
    let mut cmd = std::process::Command::new(env!("CARGO"));
    cmd.current_dir(cwd);
    cmd
}

fn build_frozen() {
    let scratch = tempfile::tempdir().unwrap();
    cargo_command(scratch.path())
        .arg("build")
        .arg("--frozen")
        .env("CARGO_TARGET_DIR", scratch.path())
        .status()
        .unwrap();
}
"""

SAME_NAME_TWO_FUNCTIONS = """\
fn build_shared() {
    let target = std::env::var("CARGO_TARGET_DIR").unwrap();
    std::process::Command::new("cargo")
        .arg("build")
        .env("CARGO_TARGET_DIR", &target)
        .status()
        .unwrap();
}

fn build_private() {
    let target = tempfile::tempdir().unwrap();
    std::process::Command::new("cargo")
        .arg("build")
        .env("CARGO_TARGET_DIR", target.path())
        .status()
        .unwrap();
}
"""


def test_a_frozen_helper_call_still_builds_into_its_target(
    tmp_path: pathlib.Path,
) -> None:
    """`--frozen` stops the network and not the compile, so NC-009 still reads it."""
    put(tmp_path, "tests/frozen.rs", FROZEN_HELPER_CALL)
    assert ("NC-009", "tests/frozen.rs") in profile(tmp_path)


def test_a_name_is_resolved_inside_the_function_that_builds(
    tmp_path: pathlib.Path,
) -> None:
    """Neither function is read through the other's binding of `target`.

    The shared build is not accused because a later function binds the name to a
    temporary directory, and the private one is not excused by the earlier one.
    """
    put(tmp_path, "tests/two.rs", SAME_NAME_TWO_FUNCTIONS)
    flagged = [f.line for f in findings(tmp_path) if f.rule_id == "NC-009"]
    assert flagged == [12]


def test_the_order_of_the_functions_does_not_matter(tmp_path: pathlib.Path) -> None:
    """Swapping the two functions reports the same one."""
    shared, private = SAME_NAME_TWO_FUNCTIONS.split("\n\n")
    put(tmp_path, "tests/two.rs", f"{private}\n\n{shared}")
    flagged = [f.line for f in findings(tmp_path) if f.rule_id == "NC-009"]
    assert flagged == [3]


def test_a_name_rebound_inside_one_function_is_not_guessed(
    tmp_path: pathlib.Path,
) -> None:
    """Within one function the reader does not track which binding is live."""
    put(
        tmp_path,
        "tests/rebound.rs",
        'fn rebuild() {\n    let target = std::env::var("CARGO_TARGET_DIR").unwrap();\n'
        "    let target = tempfile::tempdir().unwrap();\n"
        '    std::process::Command::new("cargo")\n        .arg("build")\n'
        '        .env("CARGO_TARGET_DIR", target.path())\n'
        "        .status()\n        .unwrap();\n}\n",
    )
    found = {(f.rule_id, f.verdict) for f in findings(tmp_path)}
    assert ("NC-009", "noncompliant") not in found
    assert ("EN-001", "indeterminate") in found


@pytest.mark.parametrize(
    ("filter_text", "is_prepared"),
    [
        # `package(=ui)` selects a package called ui, and this binary is in app.
        ("package(=ui)", False),
        ("package(=app)", True),
        ("binary(=ui)", True),
        ("binary(=app)", False),
        # Matchers are evaluated, not searched for the name.
        ("package(/^app[0-9]+$/)", False),
        ("package(/^app/)", True),
        ("package(~pp)", True),
        ("package(~ui)", False),
        ("binary(#u*)", True),
        ("binary(#x*)", False),
        # `app/tests/ui.rs` is binary `app::ui`; `ui` alone is package `ui`'s library.
        ("binary_id(=ui)", False),
        ("binary_id(=app)", False),
        ("binary_id(app::*)", True),
        # An unprefixed matcher is a glob.
        ("package(app*)", True),
        ("package(u*)", False),
        ("binary_id(app::ui)", True),
        ("binary_id(ui::ui)", False),
    ],
)
def test_a_setup_selector_is_matched_to_its_own_kind_of_name(
    tmp_path: pathlib.Path, filter_text: str, *, is_prepared: bool
) -> None:
    """A package name never covers a binary of the same name in another package."""
    put(tmp_path, "app/Cargo.toml", '[package]\nname = "app"\n')
    put(tmp_path, "ui/Cargo.toml", '[package]\nname = "ui"\n')
    put(tmp_path, "app/tests/ui.rs", TRYBUILD_TEST)
    put(
        tmp_path,
        ".config/nextest.toml",
        SETUP_SCRIPT.format(filter=filter_text, setup="prepare-nested"),
    )
    assert (("NC-010", "app/tests/ui.rs") in profile(tmp_path)) is not is_prepared


def covered_checkout(root: pathlib.Path, nextest: str) -> None:
    """Write a package whose trybuild test a nextest configuration may prepare."""
    put(root, "Cargo.toml", '[package]\nname = "app"\n')
    put(root, "tests/ui.rs", TRYBUILD_TEST)
    put(root, ".config/nextest.toml", nextest)


PREPARING_SCRIPT = SETUP_SCRIPT.format(filter="all()", setup="prepare-nested")


def test_a_setup_script_in_another_profile_does_not_prepare_a_default_run(
    tmp_path: pathlib.Path,
) -> None:
    """`profile.ci` runs only under `--profile ci`, so it proves nothing by default."""
    covered_checkout(
        tmp_path, PREPARING_SCRIPT.replace("profile.default", "profile.ci")
    )
    reported = profile(tmp_path)
    assert ("NC-010", "tests/ui.rs") in reported
    assert ("EN-001", "") in reported


def test_a_platform_restricted_setup_rule_is_not_counted(
    tmp_path: pathlib.Path,
) -> None:
    """A rule that runs only on one platform may not run on the one under test."""
    script = PREPARING_SCRIPT.replace(
        'filter = "all()"', 'filter = "all()"\nplatform = "cfg(windows)"'
    )
    covered_checkout(tmp_path, script)
    reported = profile(tmp_path)
    assert ("NC-010", "tests/ui.rs") in reported
    assert ("EN-001", "") in reported


@pytest.mark.parametrize(
    "matcher",
    # The second is a TOML basic string holding one backslash: `binary(u\i)`.
    [
        "package(app{2,3})",
        "binary(u\\\\i)",
        # Nextest decodes escapes in an equality matcher, and its regex flavour
        # accepts `\z`, which Python's does not.
        "package(=a\\\\u{70}p)",
        "package(/^app\\\\z/)",
    ],
    ids=["braces", "backslash", "unicode-escape", "regex-end-anchor"],
)
def test_a_glob_with_braces_or_escapes_is_not_evaluated(
    tmp_path: pathlib.Path, matcher: str
) -> None:
    """Nextest reads globs with globset; fnmatch does not, so the reader declines."""
    covered_checkout(
        tmp_path, SETUP_SCRIPT.format(filter=matcher, setup="prepare-nested")
    )
    found = findings(tmp_path)
    assert ("NC-010", "tests/ui.rs") in {(f.rule_id, f.path) for f in found}
    reasons = [f.message for f in found if f.rule_id == "EN-001"]
    assert any("brace or escape in a glob" in reason for reason in reasons), reasons


def test_a_regex_matcher_may_hold_braces(tmp_path: pathlib.Path) -> None:
    """`package(/^app[0-9]{2}$/)` is a regular expression, not a glob."""
    put(tmp_path, "app12/Cargo.toml", '[package]\nname = "app12"\n')
    put(tmp_path, "app12/tests/ui.rs", TRYBUILD_TEST)
    put(
        tmp_path,
        ".config/nextest.toml",
        SETUP_SCRIPT.format(filter="package(/^app[0-9]{2}$/)", setup="prepare-nested"),
    )
    found = {(f.rule_id, f.path) for f in findings(tmp_path)}
    assert ("NC-010", "app12/tests/ui.rs") not in found
    assert not any(rule == "EN-001" for rule, _path in found)


def test_a_nextest_file_that_links_outside_the_checkout_is_not_read(
    tmp_path: pathlib.Path,
) -> None:
    """A symlinked configuration is reported, never read through."""
    outside = tmp_path / "outside"
    put(outside, "nextest.toml", PREPARING_SCRIPT)
    checkout = tmp_path / "checkout"
    put(checkout, "Cargo.toml", '[package]\nname = "app"\n')
    put(checkout, "tests/ui.rs", TRYBUILD_TEST)
    (checkout / ".config").mkdir()
    (checkout / ".config/nextest.toml").symlink_to(outside / "nextest.toml")
    reported = profile(checkout)
    assert ("NC-010", "tests/ui.rs") in reported
    assert ("EN-001", "") in reported


def test_a_config_directory_that_links_outside_the_checkout_is_not_read(
    tmp_path: pathlib.Path,
) -> None:
    """The same holds when an ancestor of the file is the link."""
    outside = tmp_path / "outside"
    put(outside, "nextest.toml", PREPARING_SCRIPT)
    checkout = tmp_path / "checkout"
    put(checkout, "Cargo.toml", '[package]\nname = "app"\n')
    put(checkout, "tests/ui.rs", TRYBUILD_TEST)
    (checkout / ".config").symlink_to(outside, target_is_directory=True)
    reported = profile(checkout)
    assert ("NC-010", "tests/ui.rs") in reported
    assert ("EN-001", "") in reported


def test_a_negated_setup_filter_is_reported_as_a_problem(
    tmp_path: pathlib.Path,
) -> None:
    """A filter the reader does not evaluate is said so, not silently trusted."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    put(tmp_path, "tests/ui.rs", TRYBUILD_TEST)
    put(
        tmp_path,
        ".config/nextest.toml",
        SETUP_SCRIPT.format(filter="not binary(ui)", setup="prepare-nested"),
    )
    messages = [f.message for f in findings(tmp_path) if f.rule_id == "EN-001"]
    assert any("negation, difference, intersection" in message for message in messages)


HELPER_WITH_TEMP_PARAMETER = """\
fn cargo_command(target: &tempfile::TempDir) -> std::process::Command {
    let mut cmd = std::process::Command::new(env!("CARGO"));
    cmd.env("CARGO_TARGET_DIR", target.path());
    cmd
}

fn build_in_scratch() {
    let scratch = tempfile::tempdir().unwrap();
    let target = std::env::var("CARGO_TARGET_DIR").unwrap();
    cargo_command(&scratch).arg("build").status().unwrap();
    drop(target);
}
"""

# The helper's `target` is whatever the caller passes, which the reader cannot
# see; the caller's own, unrelated `target` must not stand in for it.
HELPER_WITH_PATH_PARAMETER = """\
fn cargo_command(target: &std::path::Path) -> std::process::Command {
    let mut cmd = std::process::Command::new(env!("CARGO"));
    cmd.env("CARGO_TARGET_DIR", target);
    cmd
}

fn build_in_shared() {
    let scratch = tempfile::tempdir().unwrap();
    let target = std::env::var("CARGO_TARGET_DIR").unwrap();
    cargo_command(scratch.path()).arg("build").status().unwrap();
    drop(target);
}
"""

HELPER_WITH_STRUCT_PARAMETER = """\
struct Pair {
    path: std::path::PathBuf,
}

fn cargo_command(Pair { path: target }: Pair) -> std::process::Command {
    let mut cmd = std::process::Command::new(env!("CARGO"));
    cmd.env("CARGO_TARGET_DIR", target);
    cmd
}

fn build_in_scratch() {
    let scratch = tempfile::tempdir().unwrap();
    let target = scratch.path().join("t");
    cargo_command(Pair { path: std::path::PathBuf::from("x") })
        .arg("build")
        .status()
        .unwrap();
    drop(target);
}
"""

CONDITIONAL_TARGET_FLAG = """\
fn build(is_isolated: bool) {
    let scratch = tempfile::tempdir().unwrap();
    let mut cmd = std::process::Command::new("cargo");
    cmd.arg("build");
    if is_isolated {
        cmd.args(["--target-dir", scratch.path().to_str().unwrap()]);
    }
    cmd.status().unwrap();
}
"""


def test_a_helper_resolves_its_names_in_its_own_scope(tmp_path: pathlib.Path) -> None:
    """The helper's `target` is its parameter, not the caller's unrelated binding."""
    put(tmp_path, "tests/helper.rs", HELPER_WITH_TEMP_PARAMETER)
    assert ("NC-009", "tests/helper.rs") in profile(tmp_path)


def test_a_helper_parameter_is_not_resolved_in_the_caller(
    tmp_path: pathlib.Path,
) -> None:
    """An untyped-for-the-reader parameter reads as unknown, never as the caller's."""
    put(tmp_path, "tests/helper.rs", HELPER_WITH_PATH_PARAMETER)
    reported = profile(tmp_path)
    assert ("NC-009", "tests/helper.rs") not in reported
    assert ("EN-001", "tests/helper.rs") in reported


def test_a_destructured_helper_parameter_is_not_resolved_in_the_caller(
    tmp_path: pathlib.Path,
) -> None:
    """`Pair { path: target }: Pair` binds `target`, whatever the caller calls its own.

    The caller's `target` is a temporary directory, and must not stand in for the
    helper's, which is whatever was passed in `Pair`.
    """
    put(tmp_path, "tests/pair.rs", HELPER_WITH_STRUCT_PARAMETER)
    reported = profile(tmp_path)
    assert ("NC-009", "tests/pair.rs") not in reported
    assert ("EN-001", "tests/pair.rs") in reported


ESCAPED_TARGET_FLAG = """\
fn build(use_shared: bool) {
    let scratch = tempfile::tempdir().unwrap();
    let mut cmd = std::process::Command::new("cargo");
    cmd.arg("build").env("CARGO_TARGET_DIR", scratch.path());
    if use_shared {
        cmd.arg("\\x2d\\x2dtarget-dir=target/shared");
    }
    cmd.status().unwrap();
}
"""


def test_an_escaped_target_flag_is_not_read_as_text(tmp_path: pathlib.Path) -> None:
    """An escaped spelling of the flag is the flag at run time, so it is unknown."""
    put(tmp_path, "tests/escaped.rs", ESCAPED_TARGET_FLAG)
    reported = profile(tmp_path)
    assert ("NC-009", "tests/escaped.rs") not in reported
    assert ("EN-001", "tests/escaped.rs") in reported


def test_a_conditional_target_flag_is_unknown_not_inherited(
    tmp_path: pathlib.Path,
) -> None:
    """A call on some paths may select a private target, so the reader says so."""
    put(tmp_path, "tests/conditional.rs", CONDITIONAL_TARGET_FLAG)
    reported = profile(tmp_path)
    assert ("EN-001", "tests/conditional.rs") in reported
    assert ("NC-009", "tests/conditional.rs") not in reported


def test_a_fixed_path_under_the_system_temporary_directory_is_not_private(
    tmp_path: pathlib.Path,
) -> None:
    """The system temporary directory is shared, so a fixed path persists."""
    put(
        tmp_path,
        "tests/fixed.rs",
        'fn build() {\n    let dir = std::env::temp_dir().join("scenario");\n'
        '    std::process::Command::new("cargo")\n        .arg("build")\n'
        '        .env("CARGO_TARGET_DIR", &dir)\n'
        "        .status()\n        .unwrap();\n}\n",
    )
    assert ("NC-009", "tests/fixed.rs") not in profile(tmp_path)


def test_an_exemption_does_not_hide_an_unreadable_target(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exemptions suppress NC-009 and NC-010 only; the rule's own doubt stays."""
    put(tmp_path, "tests/steps_cmd.rs", ORTHO_SCENARIO)
    exemption = {
        "repository": "leynos/ortho-config",
        "path": "tests/steps_cmd.rs",
        "rationale": "the scenario builds through a shared tree",
        "review_trigger": "review when the target helper changes",
    }
    origin(tmp_path, "https://github.com/leynos/ortho-config.git")
    parameters = {"severity": "warning", "exemptions": [exemption]}
    found = {
        (f.rule_id, f.verdict)
        for f in result_with(monkeypatch, tmp_path, parameters).findings
    }
    assert ("EN-001", "indeterminate") in found
    assert ("NC-010", "noncompliant") not in found


def test_a_private_tempdir_target_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """A target named after a temporary directory can never be warm."""
    put(tmp_path, "tests/scenario.rs", TEMPDIR_TARGET)
    assert ("NC-009", "tests/scenario.rs") in profile(tmp_path)


@pytest.mark.parametrize(
    "source",
    [TARGET_FLAG, BUILD_TARGET_VARIABLE, REMOVE_BOTH],
    ids=["target-dir-flag", "build-target-variable", "both-removed-in-temp-cwd"],
)
def test_every_route_to_a_private_target_is_read(
    tmp_path: pathlib.Path, source: str
) -> None:
    """Cargo takes its target from three places, and each one is read."""
    put(tmp_path, "tests/scenario.rs", source)
    assert ("NC-009", "tests/scenario.rs") in profile(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        REMOVE_ONE,
        CLEAR_THEN_SHARED,
        SHARED_FROM_ENVIRONMENT,
        METADATA_PRIVATE,
        BUILD_TARGET_ALONE,
        OPAQUE_AFTER_CLEAR,
    ],
    ids=[
        "one-variable-removed",
        "clear-then-shared",
        "read-from-environment",
        "no-compile",
        "build-variable-alone",
        "opaque-after-clear",
    ],
)
def test_a_target_that_may_be_shared_is_not_private(
    tmp_path: pathlib.Path, source: str
) -> None:
    """Whitaker's RFC 0006 review: only evidence of privacy is a finding.

    Removing `CARGO_TARGET_DIR` alone leaves `CARGO_BUILD_TARGET_DIR` free to
    name a shared directory, and a later write to the variable wins over an
    `env_clear`. A command that compiles nothing builds nothing.
    """
    put(tmp_path, "tests/scenario.rs", source)
    assert ("NC-009", "tests/scenario.rs") not in profile(tmp_path)


def test_ortho_config_scenario_target_is_reported_as_unknown(
    tmp_path: pathlib.Path,
) -> None:
    """The per-scenario value comes from a function in another file.

    The reader does not follow it, so the answer is indeterminate rather than
    a private-target finding or a pass, which is the choice RFC 0006 made for
    the same case.
    """
    put(tmp_path, "tests/steps_cmd.rs", ORTHO_SCENARIO)
    found = {(f.rule_id, f.verdict, f.path) for f in findings(tmp_path)}
    assert ("EN-001", "indeterminate", "tests/steps_cmd.rs") in found
    assert not any(rule == "NC-009" for rule, _verdict, _path in found)


def test_a_program_that_builds_through_its_own_cargo_is_read(
    tmp_path: pathlib.Path,
) -> None:
    """`Command::new(exe)` with a temporary target builds into that target."""
    put(
        tmp_path,
        "tests/steps_cmd.rs",
        ORTHO_SCENARIO.replace(
            "let target_dir = scenario_target_dir(ctx)?;",
            "let target_dir = tempfile::tempdir()?;",
        ).replace("target_dir.as_str()", "target_dir.path()"),
    )
    assert ("NC-009", "tests/steps_cmd.rs") in profile(tmp_path)


def test_a_program_that_sets_no_target_is_not_a_build(tmp_path: pathlib.Path) -> None:
    """A command that is not cargo and names no target is ordinary test code."""
    put(
        tmp_path,
        "tests/run.rs",
        'fn go() { Command::new("echo").arg("hi").status(); }\n',
    )
    assert findings(tmp_path) == ()


def test_a_test_binary_rerun_in_a_temporary_directory_is_not_a_build(
    tmp_path: pathlib.Path,
) -> None:
    """ortho-config's XDG oracle reruns the test executable with a clean environment.

    A temporary working directory and an `env_clear()` are how a *cargo* command
    is proven private; on any other program they say nothing about a build.
    """
    put(
        tmp_path,
        "src/paths_tests.rs",
        "#[cfg(test)]\nmod tests {\n    fn oracle() {\n"
        "        let root = tempfile::TempDir::new().unwrap();\n"
        "        std::process::Command::new(std::env::current_exe().unwrap())\n"
        "            .env_clear()\n            .current_dir(root.path())\n"
        "            .output()\n            .unwrap();\n    }\n}\n",
    )
    assert findings(tmp_path) == ()


@pytest.mark.parametrize(
    "command",
    [
        # whitaker's `ls --target-dir`: a flag of that program, not cargo's.
        (
            'Command::new(env!("CARGO_BIN_EXE_tool"))\n'
            '            .args(["ls", "--target-dir"])\n'
            "            .arg(scratch.path())"
        ),
        # whitaker's lock child: the test executable rerun with a target variable.
        (
            "Command::new(std::env::current_exe().unwrap())\n"
            '            .env("CARGO_TARGET_DIR", scratch.path())'
        ),
        # The same rerun through a binding, as whitaker's lock test writes it.
        (
            "let executable = std::env::current_exe().unwrap();\n"
            "    Command::new(executable)\n"
            '            .env("CARGO_TARGET_DIR", scratch.path())'
        ),
    ],
    ids=["program-flag", "test-executable-rerun", "rerun-through-a-binding"],
)
def test_a_program_that_is_not_a_build_is_not_judged(
    tmp_path: pathlib.Path, command: str
) -> None:
    """Only a target *variable* on a program is evidence that it builds."""
    put(
        tmp_path,
        "tests/child.rs",
        "fn child() {\n    let scratch = tempfile::tempdir().unwrap();\n"
        f"    {command}\n            .output()\n            .unwrap();\n}}\n",
    )
    assert findings(tmp_path) == ()


def test_the_default_exemption_covers_netsukes_generated_fixture(
    tmp_path: pathlib.Path,
) -> None:
    """The shipped default records one reviewed cold build."""
    origin(tmp_path, "https://github.com/leynos/netsuke.git")
    put(tmp_path, "tests/locale_stub_ui_tests.rs", TEMPDIR_TARGET)
    assert findings(tmp_path) == ()


def test_the_default_exemption_does_not_travel(tmp_path: pathlib.Path) -> None:
    """The same file in another repository is audited."""
    origin(tmp_path, "https://github.com/leynos/other.git")
    put(tmp_path, "tests/locale_stub_ui_tests.rs", TEMPDIR_TARGET)
    assert ("NC-009", "tests/locale_stub_ui_tests.rs") in profile(tmp_path)


def test_other_nested_cargo_exemptions_do_not_reach_this_rule(
    tmp_path: pathlib.Path,
) -> None:
    """A file exempt from NC-007 is still audited for a cold build."""
    origin(tmp_path, "https://github.com/leynos/whitaker.git")
    put(
        tmp_path,
        "installer/src/builder.rs",
        "#[cfg(test)]\nmod t {\n" + TEMPDIR_TARGET + "}\n",
    )
    assert ("NC-009", "installer/src/builder.rs") in profile(tmp_path)
    assert LOCK_RULE_ID != RULE_ID


def test_a_clean_repository_is_compliant(tmp_path: pathlib.Path) -> None:
    """A repository that starts no nested build has nothing to report."""
    put(tmp_path, "Cargo.toml", '[package]\nname = "plain"\n')
    put(tmp_path, "src/lib.rs", "pub fn add(a: u32, b: u32) -> u32 { a + b }\n")
    put(tmp_path, "tests/add.rs", "#[test]\nfn adds() { assert_eq!(1 + 1, 2); }\n")
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.findings == ()
    assert result.verdict == "compliant"


def test_a_survey_finding_is_reported_but_does_not_block(
    tmp_path: pathlib.Path,
) -> None:
    """The package ships at warning, so a gate continues while the estate is fixed."""
    rstest_bdd_before(tmp_path)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {f.severity for f in result.findings} == {"warning"}
    assert result.verdict == "noncompliant"
    assert result.exit_code == 0
    assert result.blocking is False


def test_the_severity_parameter_restores_blocking(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Promotion is a parameter change, not a policy edit."""
    rstest_bdd_before(tmp_path)
    result = result_with(monkeypatch, tmp_path, {"severity": "error", "exemptions": []})
    assert {f.severity for f in result.findings} == {"error"}
    assert result.exit_code == 1


def test_the_remedy_names_both_examples(tmp_path: pathlib.Path) -> None:
    """A reader who has never met the rule is told what to do and shown two cases."""
    put(tmp_path, "tests/ui.rs", TRYBUILD_TEST)
    [found] = findings(tmp_path)
    assert "nextest setup script" in found.message
    assert "https://github.com/leynos/rstest-bdd/pull/916" in found.message
    assert "https://github.com/leynos/whitaker/issues/518" in found.message


def test_a_source_the_reader_cannot_decode_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """Evidence the reader cannot read is reported, never read as a pass."""
    put(tmp_path, "tests/broken.rs", "fn open() {\n")
    result = runner.run_rule(RULE_ID, tmp_path)
    assert [(f.rule_id, f.verdict) for f in result.findings] == [
        ("EN-001", "indeterminate")
    ]


BINARY_UI_SCRIPT = SETUP_SCRIPT.format(filter="binary(ui)", setup="prepare-nested")
SUPPORT_BUILD = """\
pub fn build() {
    let t = trybuild::TestCases::new();
    t.pass("x.rs");
}
"""


def support_checkout(root: pathlib.Path, files: dict[str, str]) -> None:
    """Write a package whose `ui` binary a setup script covers, plus *files*."""
    put(root, "Cargo.toml", '[package]\nname = "app"\n')
    put(root, ".config/nextest.toml", BINARY_UI_SCRIPT)
    for relative, text in files.items():
        put(root, relative, text)


@pytest.mark.parametrize(
    ("files", "support", "is_covered"),
    [
        pytest.param(
            {"tests/ui.rs": "mod support;\n", "tests/support/mod.rs": SUPPORT_BUILD},
            "tests/support/mod.rs",
            True,
            id="mod-from-covered-binary",
        ),
        pytest.param(
            {
                "tests/ui.rs": '#[path = "helpers/build.rs"]\nmod build;\n',
                "tests/helpers/build.rs": SUPPORT_BUILD,
            },
            "tests/helpers/build.rs",
            True,
            id="path-from-covered-binary",
        ),
        pytest.param(
            {
                "tests/ui.rs": "mod outer;\n",
                "tests/outer/mod.rs": "mod inner;\n",
                "tests/outer/inner.rs": SUPPORT_BUILD,
            },
            "tests/outer/inner.rs",
            True,
            id="second-hop-from-covered-binary",
        ),
        pytest.param(
            {
                "tests/ui.rs": "mod support;\n",
                "tests/other.rs": "mod support;\n",
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="also-reached-from-an-uncovered-binary",
        ),
        pytest.param(
            {
                "tests/ui.rs": "mod support;\n",
                "src/lib.rs": (
                    '#[cfg(test)]\n#[path = "../tests/support/mod.rs"]\nmod support;\n'
                ),
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="also-reached-from-the-library",
        ),
        pytest.param(
            {"tests/ui.rs": "// no module\n", "tests/support/mod.rs": SUPPORT_BUILD},
            "tests/support/mod.rs",
            False,
            id="reached-by-nothing",
        ),
        pytest.param(
            {
                "tests/other.rs": "mod support;\n",
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="reached-only-from-an-uncovered-binary",
        ),
        pytest.param(
            {
                "tests/ui.rs": "mod support;\n",
                "tests/mod.rs": "mod support;\n",
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="also-reached-from-a-top-level-tests-mod-rs",
        ),
        pytest.param(
            {
                "tests/ui.rs": "mod support;\n",
                "src/lib.rs": "mod foo;\n",
                "src/foo.rs": (
                    '#[cfg(test)]\n#[path = "../tests/support/mod.rs"]\nmod support;\n'
                ),
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="also-reached-through-an-ordinary-library-module",
        ),
        pytest.param(
            {
                "tests/ui.rs": '#[path = "helpers/lib.rs"]\nmod helper;\n',
                "tests/helpers/lib.rs": "mod build;\n",
                "tests/helpers/build.rs": SUPPORT_BUILD,
            },
            "tests/helpers/build.rs",
            True,
            id="a-lib-rs-outside-src-is-not-a-target",
        ),
        pytest.param(
            {
                "tests/ui.rs": '#[path = "../src/component.rs"]\nmod component;\n',
                "src/lib.rs": "mod component;\n",
                "src/component.rs": (
                    '#[cfg(test)]\n#[path = "../tests/support/mod.rs"]\nmod support;\n'
                ),
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="a-library-module-a-covered-binary-also-imports-by-path",
        ),
        pytest.param(
            {
                "tests/ui.rs": "mod support;\n",
                "src/lib.rs": '#[path = "../tests/support/mod.rs"]\nmod support;\n',
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="a-library-declaration-that-points-into-the-test-directory",
        ),
        pytest.param(
            {
                "tests/ui.rs": "mod support;\n",
                "src/lib.rs": '#[path = "../tests/support/./mod.rs"]\nmod support;\n',
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="a-library-path-with-a-dot-component",
        ),
        pytest.param(
            {
                "tests/ui.rs": "mod support;\n",
                "src/lib.rs": '#[path = "../tests/support/mod.rs"]\nmod support;\n',
                "tests/support/mod.rs": "mod inner;\n",
                "tests/support/inner.rs": SUPPORT_BUILD,
            },
            "tests/support/inner.rs",
            False,
            id="a-file-below-one-the-library-names-by-path",
        ),
        pytest.param(
            {
                "tests/ui.rs": "// the covered binary\n" + SUPPORT_BUILD,
                "tests/other.rs": '#[path = "ui.rs"]\nmod ui;\n',
            },
            "tests/ui.rs",
            False,
            id="a-covered-binary-an-uncovered-one-also-imports",
        ),
        pytest.param(
            {
                "Cargo.toml": (
                    '[package]\nname = "app"\n[[test]]\nname = "support"\n'
                    'path = "tests/support/mod.rs"\n'
                ),
                "tests/ui.rs": "mod support;\n",
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="a-manifest-declared-test-target",
        ),
        pytest.param(
            {
                "tests/ui.rs": SUPPORT_BUILD,
                "src/lib.rs": '#[path = "../tests/ui.rs"]\nmod ui;\n',
            },
            "tests/ui.rs",
            False,
            id="a-library-owner-is-not-mistaken-for-the-binary-of-the-same-file",
        ),
        pytest.param(
            {
                "Cargo.toml": (
                    '[package]\nname = "app"\n[[test]]\nname = "ui"\n'
                    'path = "tests/support/mod.rs"\n'
                ),
                "src/lib.rs": '#[path = "../tests/support/mod.rs"]\nmod support;\n',
                "tests/ui.rs": "mod support;\n",
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="a-manifest-target-does-not-replace-the-library-owner",
        ),
        pytest.param(
            {
                "crates/app/Cargo.toml": '[package]\nname = "inner"\n',
                "crates/app/tests/ui.rs": SUPPORT_BUILD,
                "crates/app/src/lib.rs": '#[path = "../tests/ui.rs"]\nmod ui;\n',
            },
            "crates/app/tests/ui.rs",
            False,
            id="an-unseen-owner-in-a-nested-crate-is-not-a-binary",
        ),
        pytest.param(
            {
                ".config/nextest.toml": SETUP_SCRIPT.format(
                    filter="binary(ui) | package(=app)", setup="prepare-nested"
                ),
                "crates/app/Cargo.toml": '[package]\nname = "inner"\n',
                "crates/app/tests/ui.rs": SUPPORT_BUILD,
                "crates/app/src/lib.rs": '#[path = "../tests/ui.rs"]\nmod ui;\n',
            },
            "crates/app/tests/ui.rs",
            False,
            id="an-unseen-owner-is-not-in-the-root-package",
        ),
        pytest.param(
            {
                ".config/nextest.toml": SETUP_SCRIPT.format(
                    filter="package(=a)", setup="prepare-nested"
                ),
                "a/Cargo.toml": '[package]\nname = "a"\n',
                "b/Cargo.toml": '[package]\nname = "b"\n',
                "a/tests/support/mod.rs": SUPPORT_BUILD,
                "b/tests/other.rs": (
                    '#[path = "../../a/tests/support/mod.rs"]\nmod support;\n'
                ),
            },
            "a/tests/support/mod.rs",
            False,
            id="a-source-package-rule-does-not-cover-an-importer-in-another",
        ),
        pytest.param(
            {
                ".config/nextest.toml": SETUP_SCRIPT.format(
                    filter="package(=a)", setup="prepare-nested"
                ),
                "a/Cargo.toml": '[package]\nname = "a"\n',
                "b/Cargo.toml": '[package]\nname = "b"\n',
                "a/tests/support/mod.rs": SUPPORT_BUILD,
                "b/tests/other.rs": ('include!("../../a/tests/support/mod.rs");\n'),
            },
            "a/tests/support/mod.rs",
            False,
            id="an-unreached-support-file-is-not-covered-by-its-source-package",
        ),
        pytest.param(
            {
                ".config/nextest.toml": SETUP_SCRIPT.format(
                    filter="package(=a)", setup="prepare-nested"
                ),
                "a/Cargo.toml": '[package]\nname = "a"\n',
                "b/Cargo.toml": '[package]\nname = "b"\n',
                "a/tests/ui.rs": "mod support;\n",
                "a/tests/support/mod.rs": SUPPORT_BUILD,
                "b/tests/other.rs": 'include!("../../a/tests/support/mod.rs");\n',
            },
            "a/tests/support/mod.rs",
            False,
            id="a-reached-support-file-also-included-from-another-package",
        ),
        pytest.param(
            {
                ".config/nextest.toml": SETUP_SCRIPT.format(
                    filter="package(=a)", setup="prepare-nested"
                ),
                "a/Cargo.toml": '[package]\nname = "a"\n',
                "b/Cargo.toml": '[package]\nname = "b"\n',
                "a/tests/ui.rs": "mod support;\n",
                "a/tests/support/mod.rs": SUPPORT_BUILD,
                "b/tests/other.rs": 'include! { "../../a/tests/support/mod.rs" }\n',
            },
            "a/tests/support/mod.rs",
            False,
            id="an-include-with-braces",
        ),
        pytest.param(
            {
                ".config/nextest.toml": SETUP_SCRIPT.format(
                    filter="package(=a)", setup="prepare-nested"
                ),
                "a/Cargo.toml": '[package]\nname = "a"\n',
                "b/Cargo.toml": '[package]\nname = "b"\n',
                "a/tests/ui.rs": "mod support;\n",
                "a/tests/support/mod.rs": SUPPORT_BUILD,
                "b/tests/other.rs": 'include![ "../../a/tests/support/mod.rs" ];\n',
            },
            "a/tests/support/mod.rs",
            False,
            id="an-include-with-brackets",
        ),
        pytest.param(
            {
                ".config/nextest.toml": SETUP_SCRIPT.format(
                    filter="package(=a)", setup="prepare-nested"
                ),
                "a/Cargo.toml": '[package]\nname = "a"\n',
                "b/Cargo.toml": '[package]\nname = "b"\n',
                "a/tests/ui.rs": "mod support;\n",
                "a/tests/support/mod.rs": SUPPORT_BUILD,
                "b/tests/other.rs": 'include!(r"../../a/tests/support/mod.rs");\n',
            },
            "a/tests/support/mod.rs",
            False,
            id="an-include-of-a-raw-string",
        ),
        pytest.param(
            {
                ".config/nextest.toml": SETUP_SCRIPT.format(
                    filter="package(=a)", setup="prepare-nested"
                ),
                "a/Cargo.toml": '[package]\nname = "a"\n',
                "b/Cargo.toml": '[package]\nname = "b"\n',
                "a/tests/ui.rs": "mod support;\n",
                "a/tests/support/mod.rs": SUPPORT_BUILD,
                "b/tests/other.rs": 'include!("../../a/tests/support/mod.rs",);\n',
            },
            "a/tests/support/mod.rs",
            False,
            id="an-include-with-a-trailing-comma",
        ),
        pytest.param(
            {
                "tests/ui.rs": "mod support;\n",
                "src/lib.rs": (
                    '#[path = "../tests/support/tmp/../mod.rs"]\nmod support;\n'
                ),
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="a-library-path-with-a-parent-component",
        ),
        pytest.param(
            {
                "tests/ui.rs": '#[path = "shared/leaf.rs"]\nmod leaf;\n',
                "src/lib.rs": '#[path = "../tests/shared/bridge.rs"]\nmod bridge;\n',
                "tests/shared/bridge.rs": "mod leaf;\n",
                "tests/shared/leaf.rs": SUPPORT_BUILD,
                "tests/shared/bridge/leaf.rs": "// a decoy beside the wrong stem\n",
            },
            "tests/shared/leaf.rs",
            False,
            id="a-library-path-seed-resolves-its-children-beside-it",
        ),
        pytest.param(
            {
                "Cargo.toml": (
                    '[package]\nname = "app"\n[lib]\npath = "tests/support/mod.rs"\n'
                    '[[test]]\nname = "ui"\npath = "tests/support/mod.rs"\n'
                ),
                "tests/ui.rs": "mod support;\n",
                "tests/support/mod.rs": SUPPORT_BUILD,
            },
            "tests/support/mod.rs",
            False,
            id="two-manifest-targets-share-one-file",
        ),
        pytest.param(
            {
                "tests/ui.rs": '#[path = "../src/lib.rs"]\nmod app;\n',
                "src/lib.rs": SUPPORT_BUILD,
            },
            "src/lib.rs",
            False,
            id="a-library-also-reached-by-a-covered-binary-is-still-a-target",
        ),
    ],
)
def test_a_support_file_is_covered_when_every_binary_that_reaches_it_is(
    tmp_path: pathlib.Path,
    files: dict[str, str],
    support: str,
    *,
    is_covered: bool,
) -> None:
    """A setup rule that covers a binary also covers the files only it compiles in."""
    support_checkout(tmp_path, files)
    assert (("NC-010", support) in profile(tmp_path)) is not is_covered
