"""Audit nested cargo spawns through the real policy, fixtures and exemptions.

Each case is a small synthetic checkout shaped like one the issue names: the
two spawns leynos/whitaker ran before leynos/whitaker#518 and the same pair
after it, and the forwarded argument list that makes leynos/theoremc's helper
undecidable. The rest pin what the package promises around them — the
survey-mode default, the namespace Conftest is asked for, and the fact that
this rule's exemptions and the fixture-lock rule's exemptions stay apart.
"""

from __future__ import annotations

import pathlib
import typing as typ

import pygit2
import pytest

from concordat.rules import packages, runner

if typ.TYPE_CHECKING:
    import collections.abc as cabc

RULE_ID = "nested-cargo-offline-tests"
LOCK_RULE_ID = "nested-cargo-fixture-lockfiles"
NAMESPACE = "canon.lint_rules.nested_cargo_offline_tests"
POLICY = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / RULE_ID
    / "policy"
    / "nested_cargo_offline_tests.rego"
)
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"

WHITAKER_PACKAGE = """\
use std::process::Command;

fn package_crate_path() -> std::io::Result<()> {
    let status = Command::new("cargo")
        .args(["package", "-p", "whitaker-common", "--allow-dirty", "--no-verify"])
        .status()?;
    Ok(())
}
"""

WHITAKER_PACKAGE_FROZEN = WHITAKER_PACKAGE.replace(
    '"package",', '"package", "--frozen",'
)

WHITAKER_DYLINT = """\
use std::process::Command;

fn run_cargo_dylint() -> std::io::Result<()> {
    let output = Command::new("cargo")
        .arg("dylint")
        .arg("--all")
        .env("DYLINT_LIBRARY_PATH", "lib")
        .output()?;
    Ok(())
}
"""

WHITAKER_DYLINT_FROZEN = WHITAKER_DYLINT.replace(
    '.arg("dylint")', '.arg("dylint")\n        .arg("--frozen")'
).replace(
    '.env("DYLINT_LIBRARY_PATH", "lib")',
    '.env("DYLINT_LIBRARY_PATH", "lib")\n        .env("CARGO_NET_OFFLINE", "true")',
)

MAKEFILE_COLD = """\
CARGO ?= cargo
CARGO_LOCKED ?=

test: ## Run tests with warnings treated as errors
\t$(CARGO) nextest run $(CARGO_LOCKED)
"""

MAKEFILE_FETCHED = """\
CARGO ?= cargo
CARGO_LOCKED ?= --locked

.PHONY: fetch test

fetch: ## Fetch dependencies before tests
\t$(CARGO) fetch $(CARGO_LOCKED)

test: fetch ## Run tests with warnings treated as errors
\t$(CARGO) nextest run $(CARGO_LOCKED)
"""

THEOREMC_FORWARDED = """\
pub struct Subcommand {
    args: Vec<String>,
}

pub fn cargo_run_output(subcommand: &Subcommand) -> std::io::Result<()> {
    let _ = std::process::Command::new("cargo")
        .args(subcommand.args())
        .output()?;
    Ok(())
}

fn kani_is_installed() -> bool {
    std::process::Command::new("cargo")
        .args(["kani", "--version"])
        .status()
        .is_ok()
}
"""

FIXTURE_MANIFEST = (
    '[package]\nname = "fixture"\nversion = "0.0.0"\n[dependencies]\nserde = "1"\n'
)
DRIFTED_LOCK = (
    "version = 4\n\n"
    f'[[package]]\nname = "anyhow"\nversion = "1.0.99"\nsource = "{REGISTRY}"\n'
)


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


def result_with(
    monkeypatch: pytest.MonkeyPatch,
    root: pathlib.Path,
    parameters: cabc.Mapping[str, object],
) -> runner.RuleRunResult:
    """Run the rule as if its manifest declared *parameters*.

    The manifest's defaults reach Conftest only through `rule_parameters`, so
    replacing that one call is exactly the difference between the shipped
    configuration and another one of the same package. The monkeypatch fixture
    restores it, so a failing assertion cannot leak the substitution.

    Returns
    -------
    runner.RuleRunResult
        The result the package reports under *parameters*.
    """
    monkeypatch.setattr(runner, "_rule_parameters", lambda _rule_dir: dict(parameters))
    return runner.run_rule(RULE_ID, root)


def whitaker_checkout(root: pathlib.Path, *, fixed: bool) -> None:
    """Write a checkout with whitaker's two nested cargo spawns.

    The fixed form carries the evidence leynos/whitaker#518 added: `--frozen`
    on the package command, `--frozen` and `CARGO_NET_OFFLINE` on the dylint
    command, and a `fetch` target the test recipe depends on.
    """
    origin(root, "https://github.com/leynos/whitaker.git")
    put(
        root,
        "common/tests/i18n_packaging.rs",
        WHITAKER_PACKAGE_FROZEN if fixed else WHITAKER_PACKAGE,
    )
    put(
        root,
        "crates/no_std_fs_operations/tests/integration_exclusion.rs",
        WHITAKER_DYLINT_FROZEN if fixed else WHITAKER_DYLINT,
    )
    put(root, "Makefile", MAKEFILE_FETCHED if fixed else MAKEFILE_COLD)


def test_the_namespace_is_the_one_the_runner_asks_conftest_for() -> None:
    """Conftest evaluates one namespace; a policy outside it never runs."""
    assert runner._policy_namespace(RULE_ID) == NAMESPACE
    declared = [
        line.split(maxsplit=1)[1].strip()
        for line in POLICY.read_text(encoding="utf-8").splitlines()
        if line.startswith("package ")
    ]
    assert declared == [NAMESPACE], (
        f"the policy must declare the namespace the runner selects, got {declared}"
    )


def test_the_package_resolves_to_the_offline_envelope(tmp_path: pathlib.Path) -> None:
    """The builder mapping and the manifest declaration must agree."""
    envelope = packages.default_envelope_builder(RULE_ID, tmp_path)
    assert envelope["kind"] == "policy-input/nested-cargo-offline-tests"


def test_whitaker_before_its_fix_is_noncompliant(tmp_path: pathlib.Path) -> None:
    """Both online spawns are reported, and the cold entry point with them."""
    whitaker_checkout(tmp_path, fixed=False)
    reported = {(f.rule_id, f.path) for f in findings(tmp_path)}
    assert ("NC-007", "common/tests/i18n_packaging.rs") in reported
    assert (
        "NC-007",
        "crates/no_std_fs_operations/tests/integration_exclusion.rs",
    ) in reported
    assert ("NC-008", "Makefile") in reported


def test_whitaker_after_its_fix_is_compliant(tmp_path: pathlib.Path) -> None:
    """The prefetch and the command-local evidence leave nothing to report."""
    whitaker_checkout(tmp_path, fixed=True)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.findings == ()
    assert result.verdict == "compliant"


def test_a_survey_finding_is_reported_but_does_not_block(
    tmp_path: pathlib.Path,
) -> None:
    """The package ships at warning, so a gate continues while the estate is fixed."""
    whitaker_checkout(tmp_path, fixed=False)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {f.severity for f in result.findings} == {"warning"}
    assert result.verdict == "noncompliant", (
        "the verdict must still record the noncompliance it found"
    )
    assert result.exit_code == 0
    assert result.blocking is False


def test_the_severity_parameter_restores_blocking(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Promotion is a parameter change, not a policy edit."""
    whitaker_checkout(tmp_path, fixed=False)
    result = result_with(monkeypatch, tmp_path, {"severity": "error", "exemptions": []})
    assert {f.severity for f in result.findings} == {"error"}
    assert result.exit_code == 1
    assert result.blocking is True


def test_theoremc_forwards_arguments_and_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """A helper whose arguments are unknown is reported, never called clean."""
    origin(tmp_path, "https://github.com/leynos/theoremc.git")
    put(tmp_path, "tests/cargo_runner.rs", THEOREMC_FORWARDED)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert [(f.rule_id, f.verdict) for f in result.findings] == [
        ("EN-001", "indeterminate")
    ]
    assert result.verdict == "indeterminate"


def test_a_plugin_version_probe_draws_no_finding(tmp_path: pathlib.Path) -> None:
    """A version probe in leynos/theoremc resolves nothing, so it is not judged."""
    put(tmp_path, "tests/probe.rs", THEOREMC_FORWARDED)
    reported = [(f.rule_id, f.path, f.line) for f in findings(tmp_path)]
    # Only the forwarded-arguments spawn (line 6) is judged; the
    # `cargo kani --version` probe on line 13 draws nothing.
    assert reported == [("EN-001", "tests/probe.rs", 6)], reported


def test_a_fixture_package_source_draws_no_spawn_finding(
    tmp_path: pathlib.Path,
) -> None:
    """A fixture's own cargo call is the build under audit, not the test."""
    put(
        tmp_path,
        "tests/fixtures/a/Cargo.toml",
        '[workspace]\n\n[package]\nname = "fixture"\nversion = "0.0.0"\n',
    )
    put(tmp_path, "tests/fixtures/a/tests/spawn.rs", WHITAKER_PACKAGE)
    assert findings(tmp_path) == ()


def test_the_default_exemption_suppresses_whitakers_installer(
    tmp_path: pathlib.Path,
) -> None:
    """The shipped default records the installer, which must reach crates.io."""
    origin(tmp_path, "https://github.com/leynos/whitaker.git")
    put(tmp_path, "installer/src/builder.rs", _INSTALLER_TEST_SPAWN)
    assert findings(tmp_path) == ()


def test_the_default_exemption_does_not_travel(tmp_path: pathlib.Path) -> None:
    """The same file in another repository is audited."""
    origin(tmp_path, "https://github.com/leynos/other.git")
    put(tmp_path, "installer/src/builder.rs", _INSTALLER_TEST_SPAWN)
    assert {f.rule_id for f in findings(tmp_path)} == {"NC-007"}


# A spawn inside a `#[cfg(test)]` module of a source file is in test scope,
# which is the case the shipped default exemption exists for.
_INSTALLER_TEST_SPAWN = """\
#[cfg(test)]
mod tests {
    #[test]
    fn an_install_smoke_test() {
        let status = std::process::Command::new("cargo")
            .args(["install", "whitaker"])
            .status();
        assert!(status.is_ok());
    }
}
"""


def test_the_fixture_lock_rules_exemptions_do_not_reach_this_rule(
    tmp_path: pathlib.Path,
) -> None:
    """A directory exempt from NC-001/NC-002 is still audited for NC-007.

    leynos/rstest-bdd's `ui_lints` lane is exempt from the fixture-lock rule
    because it isolates its own target directory; that says nothing about
    whether the cargo it runs can reach the network.
    """
    origin(tmp_path, "https://github.com/leynos/rstest-bdd.git")
    put(
        tmp_path,
        "crates/rstest-bdd/tests/ui_lints/tests/spawn.rs",
        WHITAKER_PACKAGE,
    )
    reported = findings(tmp_path)
    assert [(f.rule_id, f.path) for f in reported] == [
        ("NC-007", "crates/rstest-bdd/tests/ui_lints/tests/spawn.rs")
    ]


def test_this_rules_exemption_does_not_suppress_a_lock_finding(
    tmp_path: pathlib.Path,
) -> None:
    """Suppression is scoped to NC-007 and NC-008, not to the checkout.

    The installer file is exempt from this rule; a drifting fixture lock in the
    same repository is still the fixture-lock rule's business.
    """
    origin(tmp_path, "https://github.com/leynos/whitaker.git")
    put(tmp_path, "installer/src/builder.rs", _INSTALLER_TEST_SPAWN)
    put(tmp_path, "tests/fixtures/a/Cargo.toml", FIXTURE_MANIFEST)
    put(tmp_path, "tests/fixtures/a/Cargo.lock", DRIFTED_LOCK)
    put(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "root"\nversion = "0.1.0"\n',
    )
    put(
        tmp_path,
        "Cargo.lock",
        "version = 4\n\n"
        f'[[package]]\nname = "serde"\nversion = "1.0.0"\nsource = "{REGISTRY}"\n',
    )
    assert findings(tmp_path) == ()
    assert {f.rule_id for f in runner.run_rule(LOCK_RULE_ID, tmp_path).findings} == {
        "NC-001"
    }


def test_an_unknown_severity_is_refused_by_the_manifest_schema_enum() -> None:
    """A spelling the policy does not know would silently weaken the gate."""
    manifest = packages.rule_manifest(packages.rule_package_dir(RULE_ID))
    parameters = typ.cast("dict[str, object]", manifest["parameters"])
    schema = typ.cast("dict[str, object]", parameters["schema"])
    severity = typ.cast("dict[str, object]", schema["properties"])["severity"]
    assert typ.cast("dict[str, object]", severity)["enum"] == ["warning", "error"]


@pytest.mark.parametrize("key", ["severity", "exemptions"])
def test_the_manifest_declares_each_parameter_default(key: str) -> None:
    """A parameter the schema declares must have a default, or it is inert."""
    defaults = packages.rule_parameters(packages.rule_package_dir(RULE_ID))
    assert key in defaults, f"{key} is declared in the schema but not defaulted"
