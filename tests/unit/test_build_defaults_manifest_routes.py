"""Exercise root Cargo profile facts through the public coverage and suite routes."""

from __future__ import annotations

import pathlib
import shutil
import typing as typ

import pytest

from concordat.rules.runner import run_rule

_FIXTURES: typ.Final = (
    pathlib.Path(__file__).parents[2]
    / "platform-standards/canon/lint-rules/rust-build-defaults/fixtures/repos"
)
_RELEASE_CASES: typ.Final = [
    pytest.param("", "compliant", id="default-release-llvm"),
    pytest.param(
        '[profile.release]\ncodegen-backend = "llvm"\n', "compliant", id="direct-llvm"
    ),
    pytest.param(
        '[profile.release]\ncodegen-backend = "cranelift"\n',
        "noncompliant",
        id="direct-cranelift",
    ),
    pytest.param(
        '[profile.release.package.example]\ncodegen-backend = "cranelift"\n',
        "indeterminate",
        id="package-cranelift",
    ),
    pytest.param(
        '[profile.release.package.example]\ncodegen-backend = "llvm"\n',
        "compliant",
        id="package-llvm-default-still-llvm",
    ),
    pytest.param(
        '[profile.release]\ncodegen-backend = "llvm"\n'
        '[profile.release.package.example]\ncodegen-backend = "cranelift"\n',
        "indeterminate",
        id="package-displaces-profile-llvm",
    ),
    pytest.param(
        "[profile.release]\ncodegen-backend = 3\n",
        "indeterminate",
        id="malformed-backend",
    ),
    pytest.param(
        '[profile.release]\ncodegen-backend = "custom"\n',
        "indeterminate",
        id="unsupported-backend",
    ),
]


@pytest.mark.parametrize(("profile_text", "verdict"), _RELEASE_CASES)
def test_root_release_backend_determines_whether_coverage_proves_llvm(
    tmp_path: pathlib.Path, profile_text: str, verdict: str
) -> None:
    """Release defaults cannot hide a manifest's explicit or unresolved override."""
    shutil.copytree(_FIXTURES / "coverage-make-profile", tmp_path, dirs_exist_ok=True)
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        profile_text + "\n" + manifest.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "Makefile").write_text(
        "coverage:\n\tcargo llvm-cov --release --lcov\n", encoding="utf-8"
    )
    result = run_rule("rust-build-defaults", tmp_path)
    assert result.verdict == verdict, result
    if verdict != "compliant":
        assert {finding.rule_id for finding in result.findings} == {"BD-007"}


@pytest.mark.parametrize(
    "profile_text",
    [
        'profile = "malformed"\n',
        "[profile]\ndev = 3\n",
        "[profile.dev]\ncodegen-backend = 3\n",
        "[profile.dev.package]\nexample = 3\n",
    ],
    ids=[
        "profile-not-table",
        "profile-entry-not-table",
        "backend-not-string",
        "package-not-table",
    ],
)
def test_a_direct_suite_cannot_prove_isolation_from_malformed_manifest_facts(
    tmp_path: pathlib.Path, profile_text: str
) -> None:
    """The public adapter retains unsupported profile shapes as indeterminate."""
    shutil.copytree(_FIXTURES / "gate-isolated-whitaker", tmp_path, dirs_exist_ok=True)
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        profile_text + "\n" + manifest.read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = run_rule("rust-build-defaults", tmp_path)
    assert result.verdict == "indeterminate", result
    assert {finding.rule_id for finding in result.findings} == {"BD-008"}


@pytest.mark.parametrize(("profile_text", "verdict"), _RELEASE_CASES)
def test_root_release_backend_is_consistent_in_workflow_coverage(
    tmp_path: pathlib.Path, profile_text: str, verdict: str
) -> None:
    """Workflow coverage cannot bypass unproved root facts with config LLVM."""
    shutil.copytree(_FIXTURES / "coverage-make-profile", tmp_path, dirs_exist_ok=True)
    (tmp_path / "Makefile").unlink()
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        profile_text + "\n" + manifest.read_text(encoding="utf-8"), encoding="utf-8"
    )
    command = "cargo llvm-cov --release --lcov"
    if verdict == "indeterminate":
        config = tmp_path / ".cargo/config.toml"
        with config.open("a", encoding="utf-8") as stream:
            stream.write('\n[profile.release]\ncodegen-backend = "llvm"\n')
        command = "cargo llvm-cov --profile release --lcov"
    workflow = tmp_path / ".github/workflows/coverage.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: coverage\non: push\njobs:\n  coverage:\n"
        "    runs-on: ubuntu-latest\n    steps:\n"
        f"      - run: {command}\n",
        encoding="utf-8",
    )
    result = run_rule("rust-build-defaults", tmp_path)
    # A known same-profile config backend supersedes an unknown root string.
    is_overridden_unknown = (
        profile_text == '[profile.release]\ncodegen-backend = "custom"\n'
    )
    expected = "compliant" if is_overridden_unknown else verdict
    assert result.verdict == expected, result
    if expected != "compliant":
        assert {finding.rule_id for finding in result.findings} == {"BD-007"}


@pytest.mark.parametrize("route", ["make", "workflow"])
def test_config_release_package_backend_prevents_wholly_llvm_coverage(
    tmp_path: pathlib.Path, route: str
) -> None:
    """A typed config package override is not erased by the profile's LLVM default."""
    shutil.copytree(_FIXTURES / "coverage-make-profile", tmp_path, dirs_exist_ok=True)
    config = tmp_path / ".cargo/config.toml"
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            '\n[profile.release]\ncodegen-backend = "llvm"\n'
            '[profile.release.package.example]\ncodegen-backend = "cranelift"\n'
        )
    command = "cargo llvm-cov --release --lcov"
    if route == "make":
        (tmp_path / "Makefile").write_text(
            f"coverage:\n\t{command}\n", encoding="utf-8"
        )
    else:
        (tmp_path / "Makefile").unlink()
        workflow = tmp_path / ".github/workflows/coverage.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "name: coverage\non: push\njobs:\n  coverage:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: {command}\n",
            encoding="utf-8",
        )
    result = run_rule("rust-build-defaults", tmp_path)
    assert result.verdict == "indeterminate", result
    assert {finding.rule_id for finding in result.findings} == {"BD-007"}


@pytest.mark.parametrize("route", ["make", "workflow"])
@pytest.mark.parametrize("profile_flag", ["--release", "--profile release"])
@pytest.mark.parametrize(
    "backend_case",
    [
        pytest.param(("cranelift", "llvm", "compliant"), id="config-llvm-wins"),
        pytest.param(("llvm", "cranelift", "noncompliant"), id="config-cranelift-wins"),
    ],
)
def test_config_release_backend_overrides_same_manifest_profile(
    tmp_path: pathlib.Path,
    route: str,
    profile_flag: str,
    backend_case: tuple[str, str, str],
) -> None:
    """Cargo config overrides the same manifest profile for both coverage routes."""
    manifest_backend, config_backend, verdict = backend_case
    shutil.copytree(_FIXTURES / "coverage-make-profile", tmp_path, dirs_exist_ok=True)
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        f'[profile.release]\ncodegen-backend = "{manifest_backend}"\n\n'
        + manifest.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = tmp_path / ".cargo/config.toml"
    with config.open("a", encoding="utf-8") as stream:
        stream.write(f'\n[profile.release]\ncodegen-backend = "{config_backend}"\n')
    command = f"cargo llvm-cov {profile_flag} --lcov"
    if route == "make":
        (tmp_path / "Makefile").write_text(
            f"coverage:\n\t{command}\n", encoding="utf-8"
        )
    else:
        (tmp_path / "Makefile").unlink()
        workflow = tmp_path / ".github/workflows/coverage.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "name: coverage\non: push\njobs:\n  coverage:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: {command}\n",
            encoding="utf-8",
        )
    result = run_rule("rust-build-defaults", tmp_path)
    assert result.verdict == verdict, result
    if verdict == "noncompliant":
        assert {finding.rule_id for finding in result.findings} == {"BD-007"}


@pytest.mark.parametrize("route", ["make", "workflow"])
@pytest.mark.parametrize("profile_flag", ["--release", "--profile release"])
def test_config_llvm_overrides_unknown_same_manifest_profile(
    tmp_path: pathlib.Path, route: str, profile_flag: str
) -> None:
    """An inactive root backend string cannot obscure effective config LLVM."""
    shutil.copytree(_FIXTURES / "coverage-make-profile", tmp_path, dirs_exist_ok=True)
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        '[profile.release]\ncodegen-backend = "custom"\n\n'
        + manifest.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = tmp_path / ".cargo/config.toml"
    with config.open("a", encoding="utf-8") as stream:
        stream.write('\n[profile.release]\ncodegen-backend = "llvm"\n')
    command = f"cargo llvm-cov {profile_flag} --lcov"
    if route == "make":
        (tmp_path / "Makefile").write_text(
            f"coverage:\n\t{command}\n", encoding="utf-8"
        )
    else:
        (tmp_path / "Makefile").unlink()
        workflow = tmp_path / ".github/workflows/coverage.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "name: coverage\non: push\njobs:\n  coverage:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            f"      - run: {command}\n",
            encoding="utf-8",
        )
    result = run_rule("rust-build-defaults", tmp_path)
    assert result.verdict == "compliant", result
    assert result.findings == ()
