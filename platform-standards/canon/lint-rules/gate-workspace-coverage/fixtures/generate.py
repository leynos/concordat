"""Regenerate the policy-input fixtures for gate-workspace-coverage.

Each scenario lays out a synthetic checkout from the file contents written
below, then hands it to the production ``build_gate_scope_envelope`` so the
recorded envelopes are exactly what ``concordat artefact rule run`` would send
to Conftest. ``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify``, beside the manifest's default parameters.

The base workspace is a root package ``app`` whose ``crates/*`` members are
``b`` and ``c``: a bare command selects ``app`` alone, which is the narrowing
the rule exists to find.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/gate-workspace-coverage/fixtures/generate.py
"""

from __future__ import annotations

import json
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.gate_scope_envelope import (
    GateScopeEnvelope,
    build_gate_scope_envelope,
)

FIXTURES_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = FIXTURES_DIR.parent / "rule.yaml"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."

ROOT_PACKAGE = """\
[package]
name = "app"
version = "0.1.0"

[workspace]
members = ["crates/*"]
"""
VIRTUAL = """\
[workspace]
members = ["crates/*"]
"""
VIRTUAL_DEFAULTS = """\
[workspace]
members = ["crates/*"]
default-members = ["crates/b"]
"""
SINGLE = """\
[package]
name = "app"
version = "0.1.0"
"""
MEMBER = '[package]\nname = "{name}"\nversion = "0.1.0"\n'
UNREADABLE_MANIFEST = "[package\nname = \n"


def makefile(*, lint: list[str], test: list[str] | None = None) -> str:
    """Return a Makefile whose `lint` and `test` targets run the given commands."""
    lines = [".PHONY: lint test", "", "lint:"]
    lines.extend(f"\t{command}" for command in lint)
    lines.extend(["", "test:"])
    lines.extend(f"\t{command}" for command in (test or ["@echo no tests"]))
    return "\n".join(lines) + "\n"


def workflow(run: str, *, matrix: str = "") -> str:
    """Return a one-job workflow whose single step runs *run*."""
    return (
        "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        f"{matrix}    steps:\n      - run: |\n"
        + "".join(f"          {line}\n" for line in run.splitlines())
    )


def checkout_for(
    cargo: str | None = ROOT_PACKAGE,
    *,
    lint: list[str] | None = None,
    test: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return the files of one scenario: manifests, a Makefile, and extras."""
    files: dict[str, str] = {}
    if cargo is not None:
        files["Cargo.toml"] = cargo
        if "members" in cargo:
            files["crates/b/Cargo.toml"] = MEMBER.format(name="b")
            files["crates/c/Cargo.toml"] = MEMBER.format(name="c")
    files["Makefile"] = makefile(lint=lint or ["@echo lint"], test=test)
    return {**files, **(extra or {})}


SCENARIOS: dict[str, dict[str, str]] = {
    "no_cargo_manifest": {"README.md": "# demo\n"},
    "single_package": checkout_for(SINGLE, lint=["cargo clippy"], test=["cargo test"]),
    "no_tool_runs": checkout_for(),
    # -- cargo clippy ---------------------------------------------------------
    "clippy_workspace": checkout_for(lint=["cargo clippy --workspace --all-targets"]),
    "clippy_all_alias": checkout_for(lint=["cargo clippy --all --all-targets"]),
    "clippy_bare": checkout_for(lint=["cargo clippy --all-targets"]),
    "clippy_packages_complete": checkout_for(
        lint=["cargo clippy -p app -p b --package c"]
    ),
    "clippy_packages_incomplete": checkout_for(lint=["cargo clippy -p app -p b"]),
    "clippy_virtual_bare": checkout_for(VIRTUAL, lint=["cargo clippy"]),
    "clippy_default_members": checkout_for(VIRTUAL_DEFAULTS, lint=["cargo clippy"]),
    # -- cargo nextest run ----------------------------------------------------
    "nextest_workspace": checkout_for(test=["cargo nextest run --workspace"]),
    "nextest_bare": checkout_for(test=["cargo nextest run --all-features"]),
    "nextest_llvm_cov": checkout_for(test=["cargo llvm-cov nextest --workspace"]),
    # -- cargo test and its doctests ------------------------------------------
    "test_workspace": checkout_for(test=["cargo test --workspace"]),
    "test_bare": checkout_for(test=["cargo test --all-features"]),
    "test_doc_bare": checkout_for(test=["cargo test --workspace", "cargo test --doc"]),
    "test_doc_workspace": checkout_for(
        test=["cargo test --workspace", "cargo test --doc --workspace"]
    ),
    # -- Whitaker -------------------------------------------------------------
    "whitaker_workspace": checkout_for(
        lint=["whitaker --all -- --workspace --all-targets"]
    ),
    "whitaker_all_after_separator": checkout_for(
        lint=["whitaker --all -- --all --all-targets"]
    ),
    "whitaker_all_before_separator": checkout_for(
        lint=["whitaker --all -- --all-targets --all-features"]
    ),
    "whitaker_no_separator": checkout_for(lint=["whitaker --all"]),
    "whitaker_two_runs_cover": checkout_for(
        lint=[
            "whitaker --lib whitaker_suite -- --workspace --exclude b --exclude c",
            "whitaker --lib whitaker_suite -- -p b -p c --lib",
        ]
    ),
    "whitaker_exclusion_alone": checkout_for(
        lint=["whitaker --all -- --workspace --exclude c"]
    ),
    # -- nextest and cargo test are one tool, `tests` ---------------------------
    # The whitaker repository's shape: everything under nextest, one special
    # target under `cargo test`.
    "tests_two_runners_cover": checkout_for(
        test=["cargo nextest run --workspace --exclude c", "cargo test -p c --lib"]
    ),
    "tests_runners_leave_one_out": checkout_for(
        test=["cargo nextest run --workspace --exclude c", "cargo test -p b"]
    ),
    "tests_doc_run_does_not_cover": checkout_for(
        test=["cargo nextest run --workspace --exclude c", "cargo test --doc -p c"]
    ),
    # -- shapes of reaching the gate -------------------------------------------
    "variables_and_prerequisites": {
        "Cargo.toml": ROOT_PACKAGE,
        "crates/b/Cargo.toml": MEMBER.format(name="b"),
        "crates/c/Cargo.toml": MEMBER.format(name="c"),
        "Makefile": (
            "CARGO ?= cargo\nFLAGS ?= --workspace\nWHITAKER ?= whitaker\n"
            "WHITAKER_PACKAGES ?= --workspace $(addprefix --features ,sqlite)\n"
            ".PHONY: lint lint-clippy lint-whitaker test\n"
            "lint: lint-clippy lint-whitaker\n"
            "lint-clippy:\n\t$(CARGO) clippy $(FLAGS)\n"
            "lint-whitaker:\n\t$(WHITAKER) --all -- $(WHITAKER_PACKAGES)\n"
            "test:\n\t$(CARGO) test $(FLAGS)\n"
        ),
    },
    "unresolved_selection": checkout_for(test=["cargo test $(PACKAGES)"]),
    "unresolved_selection_beside_workspace": checkout_for(
        test=["cargo test --workspace $(EXTRA)"]
    ),
    "unresolved_profile_beside_workspace": checkout_for(
        test=["cargo nextest run --workspace --profile $(PROFILE)"]
    ),
    "unresolved_exclusion": checkout_for(
        test=["cargo test --workspace --exclude $(SKIPPED)"]
    ),
    "glob_exclusion": checkout_for(test=["cargo test --workspace --exclude '[bc]'"]),
    "glob_package_all": checkout_for(test=["cargo test -p 'a*' -p '[bc]'"]),
    "versioned_packages": checkout_for(
        test=["cargo test -p app@0.1.0 --package b@0.1.0 -p c"]
    ),
    "two_commands_one_line": checkout_for(
        lint=["cargo clippy --workspace && cargo test -p app"]
    ),
    "two_clippy_one_line": checkout_for(
        lint=["cargo clippy -p app -p b && cargo clippy -p c"]
    ),
    "whitaker_virtual_without_separator": checkout_for(
        VIRTUAL, lint=["whitaker --all --workspace"]
    ),
    "whitaker_unresolved_library": checkout_for(
        lint=["whitaker --lib $(LIBRARY) -- --workspace"]
    ),
    "wrapper_script": checkout_for(lint=["./scripts/run_whitaker.py --packages x"]),
    "changes_directory": checkout_for(test=["cd crates/b && cargo test"]),
    "other_manifest": checkout_for(
        test=["cargo test --manifest-path other/Cargo.toml"]
    ),
    "make_delegation": checkout_for(lint=["$(MAKE) -C crates/b lint", "cargo clippy"]),
    "make_delegation_alone": checkout_for(lint=["$(MAKE) -C crates/b lint"]),
    "ci_job_never_runs": checkout_for(
        test=["cargo test -p app"],
        extra={
            ".github/workflows/ci.yml": (
                "name: ci\non: push\njobs:\n  build:\n    if: ${{ false }}\n"
                "    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: cargo test --workspace\n"
            )
        },
    ),
    "ci_matrix_conditional_step": checkout_for(
        extra={
            ".github/workflows/ci.yml": (
                "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
                "    strategy:\n      matrix:\n        package: [app, b]\n"
                "    steps:\n      - if: matrix.package == 'app'\n"
                "        run: cargo test -p ${{ matrix.package }}\n"
            )
        },
    ),
    "conditional_prerequisite": {
        **checkout_for(),
        "Makefile": (
            ".PHONY: lint full\n\nifeq ($(FULL),1)\nlint: full\nendif\n"
            "lint:\n\t@echo lint\n\nfull:\n\tcargo clippy --workspace\n"
        ),
    },
    "probe_alternatives": {
        **checkout_for(),
        "Makefile": (
            ".PHONY: test\ntest:\n\tcargo test $(if $(shell cargo nextest --version "
            "2>/dev/null),-p app,-p b)\n"
        ),
    },
    "probe_branch_absent_in_one_reading": {
        **checkout_for(),
        "Makefile": (
            ".PHONY: test\ntest:\n\tcargo test -p app -p c\n"
            "\tcargo $(if $(shell cargo nextest --version 2>/dev/null),"
            "test -p b,nextest run --workspace)\n"
        ),
    },
    "probe_exclusive_tools": {
        **checkout_for(),
        "Makefile": (
            ".PHONY: test\ntest:\n\tcargo test -p app -p c\n"
            "\tcargo nextest run -p app -p c\n"
            "\tcargo $(if $(shell cargo nextest --version 2>/dev/null),"
            "test -p b,nextest run -p b)\n"
        ),
    },
    "shell_if_false_multiline": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test -p app -p c\nif false; then\n  cargo test --workspace\nfi"
            )
        }
    ),
    "cargo_global_option_then_variable": checkout_for(
        test=["cargo test -p app -p c", 'cargo +nightly "$$RUNNER" -p b']
    ),
    "ci_computed_matrix_variable_head": checkout_for(
        test=["cargo test -p app -p c"],
        extra={
            ".github/workflows/ci.yml": workflow(
                "${{ matrix.runner }} test -p b",
                matrix=(
                    "    strategy:\n"
                    "      matrix: ${{ fromJson(needs.plan.outputs.m) }}\n"
                ),
            )
        },
    ),
    "probe_runner_only": {
        **checkout_for(),
        "Makefile": (
            ".PHONY: test\ntest:\n\tcargo $(if $(shell cargo nextest --version "
            "2>/dev/null),nextest run,test) --workspace\n"
        ),
    },
    "cargo_subcommand_variable": checkout_for(
        test=["cargo test -p app -p c", "cargo $$RUNNER -p b"]
    ),
    "ci_computed_matrix_command": checkout_for(
        test=["cargo test -p app -p c"],
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo ${{ matrix.subcommand }} -p b",
                matrix=(
                    "    strategy:\n"
                    "      matrix: ${{ fromJson(needs.plan.outputs.m) }}\n"
                ),
            )
        },
    ),
    "shell_if_false": checkout_for(
        test=["cargo test -p app", "if false; then cargo test --workspace; fi"]
    ),
    "make_value_after_option": checkout_for(
        test=["cargo test --workspace --color $(COLOR)"]
    ),
    "shell_variable_command_head": checkout_for(
        test=["cargo test -p app", "$$RUNNER test -p b"]
    ),
    "keyword_prefix": checkout_for(
        test=["if cargo test --workspace; then echo ok; fi"]
    ),
    # -- CI -------------------------------------------------------------------
    "ci_workspace": checkout_for(
        extra={".github/workflows/ci.yml": workflow("cargo test --workspace")}
    ),
    "ci_bare": checkout_for(
        extra={".github/workflows/ci.yml": workflow("cargo clippy --all-targets")}
    ),
    "ci_matrix_complete": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test ${{ matrix.flags }}",
                matrix=(
                    "    strategy:\n      matrix:\n        include:\n"
                    "          - flags: --workspace\n"
                    "          - flags: --workspace --all-features\n"
                ),
            )
        }
    ),
    "ci_matrix_narrow_entry": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test ${{ matrix.flags }}",
                matrix=(
                    "    strategy:\n      matrix:\n        include:\n"
                    "          - flags: --features sqlite\n"
                    "          - flags: --features postgres\n"
                ),
            )
        }
    ),
    "ci_matrix_union_covers": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test ${{ matrix.flags }}",
                matrix=(
                    "    strategy:\n      matrix:\n        include:\n"
                    "          - flags: --workspace\n"
                    "          - flags: --features sqlite\n"
                ),
            )
        }
    ),
    "ci_matrix_exclude_removes_narrow": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test ${{ matrix.flags }}",
                matrix=(
                    "    strategy:\n      matrix:\n"
                    '        flags: ["--workspace", "-p app"]\n'
                    "        exclude:\n          - flags: -p app\n"
                ),
            )
        }
    ),
    "ci_matrix_include_override": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test ${{ matrix.flags }} # ${{ matrix.os }}",
                matrix=(
                    "    strategy:\n      matrix:\n        os: [linux]\n"
                    "        include:\n          - flags: -p app\n"
                    "          - flags: --workspace\n"
                ),
            )
        }
    ),
    "ci_working_directory": checkout_for(
        extra={
            ".github/workflows/ci.yml": (
                "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: cargo test --workspace\n"
                "        working-directory: nested\n"
            )
        }
    ),
    "ci_cd_on_earlier_line": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow("cd nested\ncargo test --workspace")
        }
    ),
    "ci_matrix_axis": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test --workspace --features ${{ matrix.backend }}",
                matrix=(
                    "    strategy:\n      matrix:\n"
                    "        backend: [sqlite, postgres]\n"
                ),
            )
        }
    ),
    "ci_matrix_expression": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test ${{ matrix.flags }}",
                matrix=(
                    "    strategy:\n"
                    "      matrix: ${{ fromJson(needs.plan.outputs.matrix) }}\n"
                ),
            )
        }
    ),
    "ci_unresolved_expression": checkout_for(
        extra={".github/workflows/ci.yml": workflow("cargo test ${{ inputs.flags }}")}
    ),
    "ci_composite_action": checkout_for(
        extra={
            ".github/actions/gate/action.yml": (
                "name: gate\nruns:\n  using: composite\n  steps:\n"
                "    - shell: bash\n      run: cargo nextest run\n"
            )
        }
    ),
    "ci_undecodable_workflow": checkout_for(
        extra={".github/workflows/ci.yml": "jobs: [unclosed\n"}
    ),
    # -- documentation examples -------------------------------------------------
    "docs_example_narrows": checkout_for(
        lint=["cargo clippy --workspace"],
        extra={
            "AGENTS.md": "# Agents\n\n```sh\ncargo clippy --all-targets\n```\n",
            "docs/guide.md": "Run:\n\n```sh\n$ cargo clippy --workspace\n```\n",
        },
    ),
    "docs_example_for_a_narrow_gate": checkout_for(
        lint=["cargo clippy --all-targets"],
        extra={"AGENTS.md": "```sh\ncargo clippy --all-targets\n```\n"},
    ),
    "ci_step_never_runs": checkout_for(
        test=["cargo test -p app"],
        extra={
            ".github/workflows/ci.yml": (
                "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - if: ${{ false }}\n"
                "        run: cargo test --workspace\n"
            )
        },
    ),
    "ci_computed_axis": checkout_for(
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test --workspace",
                matrix=(
                    "    strategy:\n      matrix:\n"
                    "        os: ${{ fromJson(needs.plan.outputs.os) }}\n"
                    "        exclude:\n          - os: mac\n"
                ),
            )
        }
    ),
    "shell_expansion_after_benign_option": checkout_for(
        test=["cargo test --workspace --color $$COLOR"]
    ),
    "whitaker_flags_variable": checkout_for(lint=["whitaker $$FLAGS"]),
    "known_command_beside_unreadable": checkout_for(
        lint=["cargo clippy --workspace && $(RUNNER) test -p app"]
    ),
    "unreadable_beside_narrow_gate": checkout_for(
        test=["cargo test -p app"],
        extra={
            ".github/workflows/ci.yml": workflow(
                "cargo test ${{ matrix.flags }}",
                matrix=(
                    "    strategy:\n"
                    "      matrix: ${{ fromJson(needs.plan.outputs.m) }}\n"
                ),
            )
        },
    ),
    "oneshell_cd": {
        **checkout_for(),
        "Makefile": (
            ".ONESHELL:\n.PHONY: test\ntest:\n\tcd crates/b\n\tcargo test --workspace\n"
        ),
    },
    "cargo_global_directory": checkout_for(test=["cargo -C nested test --workspace"]),
    "conditional_rule": {
        **checkout_for(),
        "Makefile": (
            "ifeq ($(MODE),fast)\nlint:\n\tcargo clippy\n"
            "else\nlint:\n\tcargo clippy --workspace\nendif\n"
        ),
    },
    "docs_example_different_narrowing": checkout_for(
        lint=["cargo clippy -p app -p b"],
        extra={"AGENTS.md": "```sh\ncargo clippy -p c\n```\n"},
    ),
    "docs_outside_fences_ignored": checkout_for(
        lint=["cargo clippy --workspace"],
        extra={"AGENTS.md": "Run cargo clippy --all-targets before you push.\n"},
    ),
    # -- unreadable inputs --------------------------------------------------------
    "unreadable_root_manifest": checkout_for(
        UNREADABLE_MANIFEST, lint=["cargo clippy"]
    ),
    "unreadable_member_manifest": checkout_for(
        lint=["cargo clippy"],
        extra={"crates/c/Cargo.toml": UNREADABLE_MANIFEST},
    ),
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


def build_fixture_envelope(files: dict[str, str]) -> GateScopeEnvelope:
    """Return the production envelope for a scenario, with a stable path.

    Returns
    -------
    GateScopeEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so the
        temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="gate-scope-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(files, checkout)
        envelope = build_gate_scope_envelope(checkout)
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
