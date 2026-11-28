"""Regenerate the policy-input fixtures for gate-lint-coverage.

Each scenario lays out a synthetic checkout from the file contents written
below, then hands it to the production ``build_lint_coverage_envelope`` so the
recorded envelopes are exactly what ``concordat artefact rule run`` would send to
Conftest. ``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify``, beside the manifest's default parameters.

The scenarios are laid out in the three shapes a Rust repository takes: a
workspace designed as one (a virtual workspace of ``crates/*``), a workspace
grown from a single crate (a root package that gained a member), and a Rust
extension inside a Python project (``rust_extension/Cargo.toml`` with no root
workspace, shaped on femtologging's, whose lanes come from a script).

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/gate-lint-coverage/fixtures/generate.py
"""

from __future__ import annotations

import json
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.lint_coverage_envelope import (
    LintCoverageEnvelope,
    build_lint_coverage_envelope,
)

FIXTURES_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = FIXTURES_DIR.parent / "rule.yaml"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."

DENY = 'RUSTFLAGS="-D warnings"'
DENY_DOC = 'RUSTDOCFLAGS="-D warnings"'
CLIPPY = f"{DENY} cargo clippy --workspace --all-targets --all-features"
DOC = f"{DENY_DOC} cargo doc --workspace --no-deps"
WHITAKER = f"{DENY} whitaker --all -- --workspace --all-targets --all-features"
UNREADABLE = "[package\nname = \n"
PYPROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\n'
FEATURES = '\n[features]\ndefault = ["fast"]\nfast = []\nslow = []\n'
EXTENSION_FEATURES = (
    '\n[features]\ndefault = ["extension-module", "python"]\n'
    'extension-module = ["python"]\npython = []\ntest-util = []\n'
)
LANE_SCRIPT = (
    '"""Run the lint lanes."""\nimport subprocess\n\n'
    'LANES = ["none", "python", "all"]\n\n'
    "def run(lane):\n"
    '    subprocess.run(["cargo", "clippy", "--all-targets", lane], check=True)\n'
)


def package(name: str, extra: str = "") -> str:
    """Return a `[package]` table, then *extra*."""
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


def makefile(*lint: str, preamble: str = "") -> str:
    """Return a Makefile whose `lint` target runs the given commands."""
    lines = [f"{preamble}.PHONY: lint test", "", "lint:"]
    lines.extend(f"\t{command}" for command in lint)
    lines.extend(["", "test:", "\t@echo test"])
    return "\n".join(lines) + "\n"


def designed(
    *lint: str, preamble: str = "", extra: dict[str, str] | None = None
) -> dict[str, str]:
    """Return a virtual workspace (a library `a` with features, and `b`) and a gate."""
    return {
        "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\nresolver = "3"\n',
        "crates/a/Cargo.toml": package("a", FEATURES),
        "crates/a/src/lib.rs": "",
        "crates/b/Cargo.toml": package("b"),
        "crates/b/src/lib.rs": "",
        "Makefile": makefile(*lint, preamble=preamble),
        **(extra or {}),
    }


def grown(*lint: str) -> dict[str, str]:
    """Return a root package that gained a member, and a gate."""
    return {
        "Cargo.toml": package("app", FEATURES)
        + '\n[workspace]\nmembers = ["crates/*"]\n',
        "src/lib.rs": "",
        "crates/b/Cargo.toml": package("b"),
        "crates/b/src/lib.rs": "",
        "Makefile": makefile(*lint),
    }


def extension(
    *lint: str, extra: dict[str, str] | None = None, preamble: str = ""
) -> dict[str, str]:
    """Return femtologging's shape: a standalone crate in `rust_extension/`."""
    return {
        "pyproject.toml": PYPROJECT,
        "rust_extension/Cargo.toml": package("ext", EXTENSION_FEATURES),
        "rust_extension/src/lib.rs": "",
        "Makefile": makefile(*lint, preamble=preamble),
        **(extra or {}),
    }


def workflow(run: str) -> str:
    """Return a one-job workflow whose single step runs *run*."""
    return (
        "name: ci\non: push\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: |\n"
        + "".join(f"          {line}\n" for line in run.splitlines())
    )


EXT = "--manifest-path rust_extension/Cargo.toml"

SCENARIOS: dict[str, dict[str, str]] = {
    "no_cargo_manifest": {"README.md": "# demo\n"},
    # -- a workspace designed as one ------------------------------------------------
    "designed_clean": designed(CLIPPY, WHITAKER, DOC),
    "designed_no_all_targets": designed(
        f"{DENY} cargo clippy --workspace --all-features", DOC
    ),
    "designed_explicit_target_list": designed(
        f"{DENY} cargo clippy --workspace --lib --bins --tests --benches "
        "--examples --all-features",
        DOC,
    ),
    "designed_feature_lanes_cover_everything": designed(
        f"{DENY} cargo clippy --workspace --all-targets --features fast",
        f"{DENY} cargo clippy --workspace --all-targets --features slow",
        DOC,
    ),
    "designed_missing_feature_lane": designed(
        f"{DENY} cargo clippy --workspace --all-targets --features fast", DOC
    ),
    "designed_default_set_never_checked": designed(
        f"{DENY} cargo clippy --workspace --all-targets --no-default-features "
        "--features fast,slow",
        DOC,
    ),
    "designed_package_qualified_features": designed(
        f"{DENY} cargo clippy --workspace --all-targets -F a/fast -F a/slow", DOC
    ),
    "designed_warnings_not_denied": designed(
        "cargo clippy --workspace --all-targets --all-features", DOC
    ),
    "designed_warnings_denied_after_separator": designed(
        "cargo clippy --workspace --all-targets --all-features -- -D warnings", DOC
    ),
    "designed_rustflags_without_deny": designed(
        'RUSTFLAGS="-C debuginfo=0" cargo clippy --workspace --all-targets '
        "--all-features",
        DOC,
    ),
    "designed_rustflags_exported": designed(
        "cargo clippy --workspace --all-targets --all-features",
        "cargo doc --workspace --no-deps",
        preamble=(
            "export RUSTFLAGS := -D warnings\nexport RUSTDOCFLAGS := -D warnings\n"
        ),
    ),
    "designed_rustflags_exported_without_deny": designed(
        "cargo clippy --workspace --all-targets --all-features",
        DOC,
        preamble="export RUSTFLAGS := -C debuginfo=0\n",
    ),
    "designed_rustdocflags_exported_unexpandable": designed(
        CLIPPY,
        "cargo doc --workspace --no-deps",
        preamble="export RUSTDOCFLAGS = $(shell echo -D warnings)\n",
    ),
    "designed_config_denies_warnings": designed(
        "cargo clippy --workspace --all-targets --all-features",
        DOC,
        extra={".cargo/config.toml": '[build]\nrustflags = ["-D", "warnings"]\n'},
    ),
    "designed_no_cargo_doc": designed(CLIPPY),
    "designed_doc_without_no_deps": designed(
        CLIPPY, f"{DENY_DOC} cargo doc --workspace"
    ),
    "designed_doc_one_package_only": designed(
        CLIPPY, f"{DENY_DOC} cargo doc --no-deps -p a"
    ),
    "designed_doc_every_package_named": designed(
        CLIPPY, f"{DENY_DOC} cargo doc --no-deps -p a -p b"
    ),
    "designed_doc_warnings_not_denied": designed(
        CLIPPY, "cargo doc --workspace --no-deps"
    ),
    "designed_doc_only_in_a_target_lint_does_not_reach": {
        **designed(CLIPPY),
        "Makefile": (
            ".PHONY: lint docs\nlint:\n\t" + CLIPPY + "\n\ndocs:\n\t" + DOC + "\n"
        ),
    },
    "designed_clippy_not_run": designed(DOC),
    "designed_both_packages_declare_fast": designed(
        f"{DENY} cargo clippy --workspace --all-targets -F a/fast -F a/slow",
        DOC,
        extra={"crates/b/Cargo.toml": package("b", FEATURES)},
    ),
    "designed_package_selected_features": designed(
        f"{DENY} cargo clippy -p a --all-targets --all-features",
        f"{DENY} cargo clippy -p b --all-targets --features fast",
        DOC,
        extra={"crates/b/Cargo.toml": package("b", FEATURES)},
    ),
    "grown_bare_command_selects_only_the_root_package": {
        **grown(f"{DENY} cargo clippy --all-targets --all-features", DOC),
        "crates/b/Cargo.toml": package("b", FEATURES),
    },
    "designed_workspace_run_excludes_a_member": designed(
        f"{DENY} cargo clippy --workspace --exclude b --all-targets --all-features",
        DOC,
        extra={"crates/b/Cargo.toml": package("b", FEATURES)},
    ),
    "designed_explicit_default_feature_asks_for_the_defaults": designed(
        f"{DENY} cargo clippy --workspace --all-targets --no-default-features "
        "--features default,fast,slow",
        DOC,
    ),
    "designed_resolver_one_applies_a_bare_feature_to_one_package": designed(
        f"{DENY} cargo clippy --workspace --all-targets --features fast,slow",
        DOC,
        extra={
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
            "crates/b/Cargo.toml": package("b", FEATURES),
        },
    ),
    "designed_workspace_run_excludes_a_pattern": designed(
        f"{DENY} cargo clippy --workspace --exclude 'b*' --all-targets --all-features",
        DOC,
        extra={"crates/b/Cargo.toml": package("b", FEATURES)},
    ),
    "designed_default_set_checked_for_one_package_only": designed(
        f"{DENY} cargo clippy -p a --all-targets --features fast,slow",
        f"{DENY} cargo clippy -p b --all-targets --no-default-features "
        "--features fast,slow",
        DOC,
        extra={"crates/b/Cargo.toml": package("b", FEATURES)},
    ),
    "designed_default_members_glob_is_not_expanded": designed(
        f"{DENY} cargo clippy --all-targets --all-features",
        DOC,
        extra={
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n'
            'default-members = ["crates/*"]\nresolver = "3"\n'
        },
    ),
    "designed_conditional_cd_unplaces_what_follows": designed(
        'if [ -n "$$X" ]; then cd crates/a; fi; '
        f"{DENY} cargo clippy --all-targets --all-features",
        DOC,
    ),
    "designed_exported_flags_end_with_warn": designed(
        "cargo clippy --workspace --all-targets --all-features",
        DOC,
        preamble="export RUSTFLAGS := -D warnings -W warnings\n",
    ),
    "designed_config_denies_but_a_workflow_run_may_override": designed(
        CLIPPY,
        DOC,
        extra={
            ".cargo/config.toml": '[build]\nrustflags = ["-D", "warnings"]\n',
            ".github/workflows/ci.yml": workflow(
                "cargo clippy --workspace --all-targets --all-features"
            ),
        },
    ),
    "designed_doc_excludes_a_package": designed(
        CLIPPY, f"{DENY_DOC} cargo doc --workspace --no-deps --exclude b"
    ),
    "designed_doc_of_binaries_only": designed(
        CLIPPY, f"{DENY_DOC} cargo doc --workspace --no-deps --bins"
    ),
    "designed_later_warn_undoes_the_deny": designed(
        f"{DENY} cargo clippy --workspace --all-targets --all-features -- -W warnings",
        DOC,
    ),
    "designed_feature_lanes_in_a_shell_conditional": designed(
        'if [ -n "$$FAST" ]; then cargo clippy --workspace --all-targets '
        "--features fast; else cargo clippy --workspace --all-targets "
        "--features slow; fi",
        DOC,
    ),
    "designed_makefile_export_does_not_reach_a_workflow_run": designed(
        CLIPPY,
        DOC,
        preamble="export RUSTFLAGS := -C debuginfo=0\n",
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo clippy --workspace --all-targets --all-features"
            )
        },
    ),
    "no_surface_but_an_unreadable_workflow": {
        "README.md": "# demo\n",
        ".github/workflows/ci.yml": "jobs: [unclosed\n",
    },
    "designed_workflow_clippy_without_deny_is_unknown": designed(
        CLIPPY,
        DOC,
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo clippy --workspace --all-targets --all-features"
            )
        },
    ),
    "designed_workflow_clippy_denies": designed(
        CLIPPY,
        DOC,
        extra={
            ".github/workflows/ci.yml": workflow(
                f"{DENY} cargo clippy --workspace --all-features"
            )
        },
    ),
    "designed_unresolved_flags": designed("cargo clippy $(CLIPPY_FLAGS)", DOC),
    "designed_make_c_leaves_the_walk_unresolved": designed(
        CLIPPY, DOC, "$(MAKE) -C nested lint"
    ),
    "designed_cd_into_a_directory_places_the_command": designed(
        "cd crates/a && " + CLIPPY, DOC
    ),
    # -- a workspace grown from a single crate ----------------------------------------
    "grown_clean": grown(CLIPPY, DOC),
    "grown_doc_selects_the_root_package_only": grown(
        CLIPPY, f"{DENY_DOC} cargo doc --no-deps"
    ),
    "grown_missing_feature_lane": grown(
        f"{DENY} cargo clippy --workspace --all-targets --features fast", DOC
    ),
    # -- a Rust extension inside a Python project ---------------------------------
    "extension_clean": extension(
        f"{DENY} cargo clippy {EXT} --all-targets --all-features",
        f"{DENY_DOC} cargo doc {EXT} --no-deps",
    ),
    "extension_cd_form": extension(
        f"cd rust_extension && {DENY} cargo clippy --all-targets --all-features",
        f"cd rust_extension && {DENY_DOC} cargo doc --no-deps",
    ),
    "extension_missing_lane": extension(
        f"{DENY} cargo clippy {EXT} --all-targets --features python",
        f"{DENY_DOC} cargo doc {EXT} --no-deps",
    ),
    "extension_no_doc_gate": extension(
        f"{DENY} cargo clippy {EXT} --all-targets --all-features"
    ),
    "extension_lanes_built_by_a_script": extension(
        'INPUT_LANES="$(LINT_LANES)" python scripts/lint_rust_lanes.py',
        preamble="LINT_LANES ?= none python test-util default all\n",
        extra={"scripts/lint_rust_lanes.py": LANE_SCRIPT},
    ),
    "extension_manifest_no_surface_holds": extension(
        f"{DENY} cargo clippy --manifest-path elsewhere/Cargo.toml --all-targets "
        "--all-features",
        f"{DENY_DOC} cargo doc {EXT} --no-deps",
    ),
    "extension_with_whitaker": extension(
        f"{DENY} cargo clippy {EXT} --all-targets --all-features",
        f"{DENY} whitaker --all -- {EXT} --all-targets --all-features",
        f"{DENY_DOC} cargo doc {EXT} --no-deps",
    ),
    "extension_whitaker_misses_targets": extension(
        f"{DENY} cargo clippy {EXT} --all-targets --all-features",
        f"{DENY} whitaker --all -- {EXT} --all-features",
        f"{DENY_DOC} cargo doc {EXT} --no-deps",
    ),
    # -- unreadable inputs ----------------------------------------------------------
    "unreadable_manifest": {
        **designed(CLIPPY, DOC),
        "crates/b/Cargo.toml": UNREADABLE,
    },
}


def lay_out(files: dict[str, str], checkout: Path) -> None:
    """Populate *checkout* with the scenario's files.

    Parameters
    ----------
    files:
        The scenario's files, by path relative to the checkout.
    checkout:
        An existing empty directory to lay the files out in.
    """
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def manifest_parameters() -> dict[str, object]:
    """Return the manifest defaults the rule runs with.

    Returns
    -------
    dict[str, object]
        The ``defaults`` of ``rule.yaml``.
    """
    manifest = YAML(typ="safe").load(MANIFEST_PATH.read_text(encoding="utf-8"))
    return dict(manifest["parameters"]["defaults"])


def build_fixture_envelope(files: dict[str, str]) -> LintCoverageEnvelope:
    """Return the production envelope for a scenario, with a stable path.

    Returns
    -------
    LintCoverageEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so the
        temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="lint-coverage-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(files, checkout)
        envelope = build_lint_coverage_envelope(checkout)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def main() -> None:
    """Regenerate ``data.json``, the bundle ``conftest verify --data`` consumes."""
    envelopes = {key: build_fixture_envelope(files) for key, files in SCENARIOS.items()}
    bundle = FIXTURES_DIR / "data.json"
    bundle.write_text(
        json.dumps(
            {"fixtures": envelopes, "parameters": manifest_parameters()}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
