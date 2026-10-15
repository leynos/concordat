"""Exercise the shipped BD-008 Whitaker isolation policy through real checkouts."""

from __future__ import annotations

import pathlib
import shutil
import typing as typ

import pytest

from concordat.rules.runner import run_rule

_RULE_ID: typ.Final = "rust-build-defaults"
_FIXTURES: typ.Final = (
    pathlib.Path(__file__).parents[2]
    / "platform-standards/canon/lint-rules/rust-build-defaults/fixtures/repos"
)


@pytest.fixture
def isolated_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Stage the reviewed fixture so each case may change only its Make route."""
    root = tmp_path / "consumer"
    shutil.copytree(_FIXTURES / "gate-isolated-whitaker", root)
    return root


@pytest.mark.parametrize(
    ("makefile", "verdict"),
    [
        pytest.param(
            'lint:\n\tRUSTFLAGS="-D warnings" whitaker --all\n',
            "compliant",
            id="direct-warning-only",
        ),
        pytest.param(
            'lint:\n\tenv RUSTFLAGS="-D warnings" /opt/bin/whitaker --all\n',
            "compliant",
            id="explicit-binary",
        ),
        pytest.param(
            "export RUSTFLAGS=-D warnings\nlint:\n\twhitaker --all\n",
            "compliant",
            id="global-warning-only",
        ),
        pytest.param(
            "lint: export RUSTFLAGS := -D warnings\nlint:\n\twhitaker --all\n",
            "compliant",
            id="target-warning-only",
        ),
        pytest.param(
            "lint: export RUSTFLAGS := -D warnings\nlint: suite\n"
            "suite:\n\twhitaker --all\n",
            "compliant",
            id="inherited-target-warning-only",
        ),
        pytest.param(
            "export RUSTFLAGS=-Zthreads=8\nlint:\n"
            '\tRUSTFLAGS="-D warnings" whitaker --all\n',
            "compliant",
            id="prefix-displaces-global",
        ),
        pytest.param(
            "lint:\n\twhitaker --all\n", "indeterminate", id="ambient-unknown"
        ),
        pytest.param(
            'lint:\n\tRUSTFLAGS="$$FLAGS" whitaker --all\n',
            "indeterminate",
            id="escaped-shell-expansion",
        ),
        pytest.param(
            'lint:\n\tRUSTFLAGS="$F" whitaker --all\n',
            "indeterminate",
            id="single-character-make-expansion",
        ),
        pytest.param(
            'lint:\n\tRUSTFLAGS="$FLAGS" whitaker --all\n',
            "indeterminate",
            id="bare-dollar-expansion",
        ),
        pytest.param(
            'lint:\n\tRUSTFLAGS="$(UNKNOWN_FLAGS)" whitaker --all\n',
            "indeterminate",
            id="unresolved-flags",
        ),
        pytest.param(
            'lint:\n\tRUSTFLAGS="-D warnings" $(UNKNOWN_SUITE) --all\n',
            "noncompliant",
            id="unresolved-executable-not-exempt",
        ),
        pytest.param(
            'lint:\n\tRUSTFLAGS="-D warnings" python suite.py whitaker\n',
            "noncompliant",
            id="unknown-wrapper-not-exempt",
        ),
        pytest.param(
            'lint:\n\tRUSTFLAGS="-D warnings" cargo test -p whitaker\n',
            "noncompliant",
            id="cargo-argument-not-exempt",
        ),
        pytest.param(
            'lint:\n\tRUSTFLAGS="-D warnings" whitaker --all; '
            'RUSTFLAGS="-D warnings" cargo clippy\n',
            "noncompliant",
            id="mixed-line-keeps-cargo-gate",
        ),
    ],
)
def test_the_public_rule_distinguishes_proven_isolation_from_other_routes(
    isolated_checkout: pathlib.Path, makefile: str, verdict: str
) -> None:
    """Only a direct, provably isolated suite route receives the exemption."""
    (isolated_checkout / "Makefile").write_text(makefile, encoding="utf-8")
    result = run_rule(_RULE_ID, isolated_checkout)
    assert result.verdict == verdict, result
    if verdict != "compliant":
        assert {finding.rule_id for finding in result.findings} == {"BD-008"}


@pytest.mark.parametrize(
    "flag",
    [
        "-Zthreads=8",
        "-Z threads=8",
        "-Zcodegen-backend=cranelift",
        "-Z codegen-backend=cranelift",
        "-Clink-arg=-fuse-ld=mold",
    ],
    ids=["frontend", "split-frontend", "backend", "split-backend", "linker"],
)
@pytest.mark.parametrize("assignment", ["prefix", "global", "target", "inherited"])
def test_development_flag_contamination_is_rejected_at_each_make_boundary(
    isolated_checkout: pathlib.Path, flag: str, assignment: str
) -> None:
    """An exemption cannot turn development flags into an isolated suite build."""
    match assignment:
        case "prefix":
            makefile = f'lint:\n\tRUSTFLAGS="-D warnings {flag}" whitaker --all\n'
        case "global":
            makefile = f"export RUSTFLAGS=-D warnings {flag}\nlint:\n\twhitaker --all\n"
        case "target":
            makefile = (
                f"lint: export RUSTFLAGS := -D warnings {flag}\n"
                "lint:\n\twhitaker --all\n"
            )
        case _:
            makefile = (
                f"lint: export RUSTFLAGS := -D warnings {flag}\nlint: suite\n"
                "suite:\n\twhitaker --all\n"
            )
    (isolated_checkout / "Makefile").write_text(makefile, encoding="utf-8")
    result = run_rule(_RULE_ID, isolated_checkout)
    assert result.verdict == "noncompliant", result
    assert {finding.rule_id for finding in result.findings} == {"BD-008"}


@pytest.mark.parametrize(
    ("linker", "verdict"),
    [
        pytest.param(
            "-Clink-arg=-fuse-ld=mold-wrapper", "compliant", id="different-linker"
        ),
        pytest.param("-Clink-arg=-fuse-ld=mold", "noncompliant", id="embedded-mold"),
        pytest.param("-C link-arg=-fuse-ld=mold", "noncompliant", id="split-mold"),
    ],
)
def test_linker_contamination_matches_the_complete_linker_token(
    isolated_checkout: pathlib.Path, linker: str, verdict: str
) -> None:
    """A distinct linker name must not be mistaken for the development linker."""
    (isolated_checkout / "Makefile").write_text(
        f'lint:\n\tRUSTFLAGS="-D warnings {linker}" whitaker --all\n',
        encoding="utf-8",
    )
    result = run_rule(_RULE_ID, isolated_checkout)
    assert result.verdict == verdict, result
    if verdict != "compliant":
        assert {finding.rule_id for finding in result.findings} == {"BD-008"}


@pytest.mark.parametrize(
    ("override", "verdict"),
    [
        pytest.param(
            'CARGO_ENCODED_RUSTFLAGS="-Zthreads=8"',
            "noncompliant",
            id="encoded-flags-displace-safe-flags",
        ),
        pytest.param(
            "CARGO_PROFILE_DEV_CODEGEN_BACKEND=cranelift",
            "noncompliant",
            id="dev-profile-override",
        ),
        pytest.param(
            "CARGO_PROFILE_TEST_CODEGEN_BACKEND=cranelift",
            "noncompliant",
            id="test-profile-override",
        ),
        pytest.param(
            'CARGO_ENCODED_RUSTFLAGS="$(UNRESOLVED)"',
            "indeterminate",
            id="encoded-override-unproved",
        ),
    ],
)
def test_codegen_overrides_cannot_bypass_the_suite_boundary(
    isolated_checkout: pathlib.Path, override: str, verdict: str
) -> None:
    """A safe RUSTFLAGS value cannot hide an explicit overriding selection."""
    (isolated_checkout / "Makefile").write_text(
        f'lint:\n\tRUSTFLAGS="-D warnings" {override} whitaker --all\n',
        encoding="utf-8",
    )
    result = run_rule(_RULE_ID, isolated_checkout)
    assert result.verdict == verdict, result
    assert {finding.rule_id for finding in result.findings} == {"BD-008"}


@pytest.mark.parametrize("source", ["config", "manifest"])
@pytest.mark.parametrize("profile", ["dev", "test", "dev.package.example"])
def test_separate_profile_backends_are_not_erased_by_safe_rustflags(
    isolated_checkout: pathlib.Path, source: str, profile: str
) -> None:
    """A distinct profile selection needs proof beyond replacing rustflags."""
    config = isolated_checkout / ".cargo/config.toml"
    with config.open("a", encoding="utf-8") as stream:
        stream.write("\n[unstable]\ncodegen-backend = true\n")
    target = config if source == "config" else isolated_checkout / "Cargo.toml"
    with target.open("a", encoding="utf-8") as stream:
        stream.write(f'\n[profile.{profile}]\ncodegen-backend = "cranelift"\n')
    (isolated_checkout / "Makefile").write_text(
        'lint:\n\tRUSTFLAGS="-D warnings" whitaker --all\n', encoding="utf-8"
    )
    result = run_rule(_RULE_ID, isolated_checkout)
    assert result.verdict == "indeterminate", result
    assert {finding.rule_id for finding in result.findings} == {"BD-008"}
