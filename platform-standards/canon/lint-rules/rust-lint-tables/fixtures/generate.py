"""Regenerate the policy-input fixtures for rust-lint-tables.

Each scenario lays out a synthetic checkout from the file contents written
below, then hands it to the production ``build_lint_tables_envelope`` so the
recorded envelopes are exactly what ``concordat artefact rule run`` would send
to Conftest. ``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify``, beside the manifest's default parameters and the derived
baseline (``data/baseline.json``), which the runner loads as ``data.baseline``.

The scenarios are grouped by the three shapes the rule must hold up under:

* a workspace designed as one (a virtual workspace with ``[workspace.lints]``);
* a workspace grown from a single crate (a root package that later gained
  members);
* a Rust extension inside a Python project (``rust_extension/Cargo.toml`` with
  no root Cargo workspace), shaped on femtologging's.

Each has a clean variant, the defects the rule names, and unreadable inputs.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/rust-lint-tables/fixtures/generate.py
"""

from __future__ import annotations

import json
import re
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.lint_tables_envelope import (
    LintTablesEnvelope,
    build_lint_tables_envelope,
)

FIXTURES_DIR = Path(__file__).resolve().parent
RULE_DIR = FIXTURES_DIR.parent
MANIFEST_PATH = RULE_DIR / "rule.yaml"
BASELINE_JSON = RULE_DIR / "data" / "baseline.json"
BASELINE_TOML = RULE_DIR / "data" / "baseline.toml"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."

# The paste-ready workspace tables, and the same tables as package-level ones.
WORKSPACE_LINTS = BASELINE_TOML.read_text(encoding="utf-8")
PACKAGE_LINTS = WORKSPACE_LINTS.replace("[workspace.lints.", "[lints.")
INHERIT = "[lints]\nworkspace = true\n"


def without(text: str, *lints: str) -> str:
    """Return *text* with the lines that set any of *lints* removed."""
    pattern = re.compile(rf"^(?:{'|'.join(map(re.escape, lints))})\s*=")
    return (
        "\n".join(line for line in text.splitlines() if not pattern.match(line)) + "\n"
    )


def with_level(text: str, lint: str, level: str) -> str:
    """Return *text* with *lint* set to *level*."""
    pattern = re.compile(rf"^{re.escape(lint)}(\s*)=\s*\"[a-z]+\"", re.MULTILINE)
    return pattern.sub(f'{lint} = "{level}"', text)


def package(name: str, extra: str = "") -> str:
    """Return a `[package]` table, then *extra*."""
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


VIRTUAL = '[workspace]\nmembers = ["crates/*"]\nresolver = "3"\n\n'
ROOT_PACKAGE_WORKSPACE = '[workspace]\nmembers = ["crates/*"]\nresolver = "3"\n\n'
UNREADABLE = "[package\nname = \n"
PYPROJECT = (
    '[build-system]\nrequires = ["maturin>=1,<2"]\nbuild-backend = "maturin"\n\n'
    '[project]\nname = "demo"\nversion = "0.1.0"\n'
)
PYO3 = '\n[dependencies]\npyo3 = { version = "0.30", features = ["macros"] }\n'
# Femtologging's extension on 27 November: two Clippy and two rustc lints.
FEMTO_TABLES = (
    '[lints.clippy]\ndisallowed_methods = "deny"\n'
    'missing_docs_in_private_items = "deny"\n\n'
    '[lints.rust]\nmissing_docs = "deny"\n'
    'unexpected_cfgs = { level = "warn", check-cfg = ["cfg(loom)"] }\n'
)

MIRI_MAKEFILE = ".PHONY: lint test\nlint:\n\t@echo lint\ntest:\n\tcargo miri test\n"
PLAIN_MAKEFILE = ".PHONY: lint test\nlint:\n\t@echo lint\ntest:\n\t@echo test\n"
WHITAKER_MAKEFILE = (
    ".PHONY: lint test\nlint:\n"
    "\twhitaker --all -- --workspace --all-targets\ntest:\n\t@echo test\n"
)
WHITAKER_BARE_MAKEFILE = (
    ".PHONY: lint test\nlint:\n\twhitaker --all\ntest:\n\t@echo test\n"
)
MACRO_AWARE_DYLINT = (
    '[whitaker]\nexperimental = ["macro_aware_cognitive_complexity"]\n\n'
    "[macro_aware_cognitive_complexity]\nmax_complexity = 9\n"
)
THRESHOLD_CLIPPY = "cognitive-complexity-threshold = 9\n"
OTHER_CLIPPY = "too-many-arguments-threshold = 4\n"


def members(*names: str, lints: str | None = INHERIT) -> dict[str, str]:
    """Return `crates/<name>/Cargo.toml` for each name, each opting in to *lints*."""
    return {
        f"crates/{name}/Cargo.toml": package(name, f"\n{lints}" if lints else "")
        for name in names
    }


def workflow(run: str, *, matrix: str = "") -> str:
    """Return a one-job workflow whose single step runs *run*."""
    return (
        "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        f"{matrix}    steps:\n      - run: |\n"
        + "".join(f"          {line}\n" for line in run.splitlines())
    )


def boundary(*, level: str = "deny", makefile: str = MIRI_MAKEFILE) -> dict[str, str]:
    """Return a single crate `ffi` whose own tables set `unsafe_code` to *level*."""
    return {
        "Cargo.toml": package(
            "ffi", f"\n{with_level(PACKAGE_LINTS, 'unsafe_code', level)}"
        ),
        "Makefile": makefile,
    }


SCENARIOS: dict[str, dict[str, str]] = {
    "no_cargo_manifest": {"README.md": "# demo\n"},
    "fixture_crate_is_not_governed": {
        **{"Cargo.toml": package("app", f"\n{PACKAGE_LINTS}")},
        "tests/fixtures/bad/Cargo.toml": package("bad"),
        "vendor/dep/Cargo.toml": package("dep"),
    },
    # -- a workspace designed as one ---------------------------------------------
    "designed_clean": {
        "Cargo.toml": VIRTUAL + WORKSPACE_LINTS,
        **members("a", "b"),
    },
    "designed_stricter_levels_and_table_form": {
        "Cargo.toml": VIRTUAL
        + with_level(
            with_level(WORKSPACE_LINTS, "unwrap_used", "forbid"),
            "expect_used",
            "forbid",
        ).replace("\ndbg_macro ", '\ndbg_macro = { level = "deny", priority = 0 }\n#'),
        **members("a"),
    },
    "designed_member_not_inheriting": {
        "Cargo.toml": VIRTUAL + WORKSPACE_LINTS,
        **members("a"),
        "crates/b/Cargo.toml": package("b"),
    },
    "designed_member_with_weak_own_tables": {
        "Cargo.toml": VIRTUAL + WORKSPACE_LINTS,
        **members("a"),
        "crates/b/Cargo.toml": package("b", '\n[lints.clippy]\nunwrap_used = "warn"\n'),
    },
    "designed_workspace_misses_new_lints": {
        "Cargo.toml": VIRTUAL
        + without(
            WORKSPACE_LINTS, "missing_assert_message", "missing_docs_in_private_items"
        ),
        **members("a", "b"),
    },
    "designed_weakened_level": {
        "Cargo.toml": VIRTUAL + with_level(WORKSPACE_LINTS, "unwrap_used", "warn"),
        **members("a"),
    },
    "designed_pedantic_priority_lost": {
        "Cargo.toml": VIRTUAL + WORKSPACE_LINTS.replace(", priority = -1", ""),
        **members("a"),
    },
    "designed_unsafe_allowed": {
        "Cargo.toml": VIRTUAL + with_level(WORKSPACE_LINTS, "unsafe_code", "warn"),
        **members("a"),
    },
    "designed_unsafe_unset": {
        "Cargo.toml": VIRTUAL + without(WORKSPACE_LINTS, "unsafe_code"),
        **members("a"),
    },
    "designed_inherits_nothing": {
        "Cargo.toml": VIRTUAL.rstrip() + "\n",
        **members("a"),
    },
    "designed_nested_workspace_clean": {
        "Cargo.toml": VIRTUAL + WORKSPACE_LINTS,
        **members("a"),
        "tools/Cargo.toml": '[workspace]\nmembers = ["xtask"]\n\n' + WORKSPACE_LINTS,
        "tools/xtask/Cargo.toml": package("xtask", f"\n{INHERIT}"),
    },
    "designed_nested_workspace_member_bare": {
        "Cargo.toml": VIRTUAL + WORKSPACE_LINTS,
        **members("a"),
        "tools/Cargo.toml": '[workspace]\nmembers = ["xtask"]\n\n' + WORKSPACE_LINTS,
        "tools/xtask/Cargo.toml": package("xtask"),
    },
    # -- a workspace grown from a single crate -----------------------------------
    "grown_clean": {
        "Cargo.toml": package("app", f"\n{INHERIT}\n")
        + ROOT_PACKAGE_WORKSPACE
        + WORKSPACE_LINTS,
        **members("b"),
    },
    "grown_root_own_tables_member_bare": {
        "Cargo.toml": package("app", f"\n{PACKAGE_LINTS}\n") + ROOT_PACKAGE_WORKSPACE,
        **members("b", lints=None),
    },
    "grown_root_not_opted_in": {
        "Cargo.toml": package("app") + "\n" + ROOT_PACKAGE_WORKSPACE + WORKSPACE_LINTS,
        **members("b"),
    },
    "grown_default_members_hide_a_member": {
        "Cargo.toml": package("app", f"\n{INHERIT}\n")
        + '[workspace]\nmembers = ["crates/*"]\ndefault-members = ["."]\n\n'
        + WORKSPACE_LINTS,
        **members("b", lints=None),
    },
    "grown_single_crate_own_tables": {
        "Cargo.toml": package("app", f"\n{PACKAGE_LINTS}"),
    },
    "grown_single_crate_no_tables": {
        "Cargo.toml": package("app"),
    },
    # -- a Rust extension inside a Python project ---------------------------------
    "extension_clean": {
        "pyproject.toml": PYPROJECT,
        "rust_extension/Cargo.toml": package("ext", PYO3 + "\n" + PACKAGE_LINTS),
    },
    "extension_with_test_macro_workspace_clean": {
        "pyproject.toml": PYPROJECT,
        "rust_extension/Cargo.toml": package("ext", f"\n{INHERIT}" + PYO3)
        + '\n[workspace]\nmembers = ["test_macros"]\nresolver = "3"\n\n'
        + WORKSPACE_LINTS,
        "rust_extension/test_macros/Cargo.toml": package(
            "ext_test_macros", f"\n{INHERIT}"
        ),
    },
    "extension_no_tables": {
        "pyproject.toml": PYPROJECT,
        "rust_extension/Cargo.toml": package("ext", PYO3),
    },
    "extension_femtologging_shape": {
        "pyproject.toml": PYPROJECT,
        "rust_extension/Cargo.toml": package("ext", PYO3 + "\n" + FEMTO_TABLES),
    },
    "extension_unsafe_unset": {
        "pyproject.toml": PYPROJECT,
        "rust_extension/Cargo.toml": package(
            "ext", PYO3 + "\n" + without(PACKAGE_LINTS, "unsafe_code")
        ),
    },
    "extension_test_macros_member_bare": {
        "pyproject.toml": PYPROJECT,
        "rust_extension/Cargo.toml": package("ext", f"\n{INHERIT}" + PYO3)
        + '\n[workspace]\nmembers = ["test_macros"]\n\n'
        + WORKSPACE_LINTS,
        "rust_extension/test_macros/Cargo.toml": package("ext_test_macros"),
    },
    # -- unsafe_code and boundary crates -----------------------------------------
    "boundary_with_miri": boundary(),
    "boundary_without_miri": boundary(makefile=PLAIN_MAKEFILE),
    "boundary_forbids_unsafe": boundary(level="forbid"),
    "boundary_with_miri_in_workflow": {
        **boundary(makefile=PLAIN_MAKEFILE),
        ".github/workflows/ci.yml": workflow("cargo +nightly miri test"),
    },
    "boundary_miri_only_named_in_echo": boundary(
        makefile=".PHONY: test\ntest:\n\t@echo cargo-miri is not run here\n"
    ),
    "boundary_gate_unreadable": {
        **boundary(makefile=PLAIN_MAKEFILE),
        ".github/workflows/ci.yml": workflow(
            "cargo miri test",
            matrix=(
                "    strategy:\n      matrix: ${{ fromJson(needs.plan.outputs.m) }}\n"
            ),
        ),
    },
    # -- cognitive complexity: Clippy's gate or Whitaker's -----------------------
    "cognitive_by_clippy": {
        "Cargo.toml": package("app", f"\n{PACKAGE_LINTS}"),
        "clippy.toml": THRESHOLD_CLIPPY,
    },
    "cognitive_by_whitaker": {
        "Cargo.toml": package(
            "app", f"\n{without(PACKAGE_LINTS, 'cognitive_complexity')}"
        ),
        "dylint.toml": MACRO_AWARE_DYLINT,
        "clippy.toml": OTHER_CLIPPY,
        "Makefile": WHITAKER_MAKEFILE,
    },
    "cognitive_whitaker_not_whole_workspace": {
        "Cargo.toml": package(
            "app", f"\n{without(PACKAGE_LINTS, 'cognitive_complexity')}"
        ),
        "dylint.toml": MACRO_AWARE_DYLINT,
        "clippy.toml": OTHER_CLIPPY,
        "Makefile": WHITAKER_BARE_MAKEFILE,
    },
    "cognitive_whitaker_two_runs_cover": {
        "Cargo.toml": package("app", f"\n{INHERIT}\n")
        + ROOT_PACKAGE_WORKSPACE
        + without(WORKSPACE_LINTS, "cognitive_complexity"),
        **members("b"),
        "dylint.toml": MACRO_AWARE_DYLINT,
        "clippy.toml": OTHER_CLIPPY,
        "Makefile": (
            ".PHONY: lint test\nlint:\n"
            "\twhitaker --all -- --workspace --exclude b --all-targets\n"
            "\twhitaker --all -- -p b --all-targets\ntest:\n\t@echo test\n"
        ),
    },
    "cognitive_whitaker_packages_miss_a_member": {
        "Cargo.toml": package("app", f"\n{INHERIT}\n")
        + ROOT_PACKAGE_WORKSPACE
        + without(WORKSPACE_LINTS, "cognitive_complexity"),
        **members("b"),
        "dylint.toml": MACRO_AWARE_DYLINT,
        "clippy.toml": OTHER_CLIPPY,
        "Makefile": (
            ".PHONY: lint test\nlint:\n"
            "\twhitaker --all -- -p app --all-targets\ntest:\n\t@echo test\n"
        ),
    },
    "cognitive_whitaker_line_unreadable": {
        "Cargo.toml": package(
            "app", f"\n{without(PACKAGE_LINTS, 'cognitive_complexity')}"
        ),
        "dylint.toml": MACRO_AWARE_DYLINT,
        "clippy.toml": OTHER_CLIPPY,
        "Makefile": PLAIN_MAKEFILE,
        ".github/workflows/ci.yml": workflow("& $whitaker --all '--' --all-targets"),
    },
    "cognitive_whitaker_threshold_left": {
        "Cargo.toml": package(
            "app", f"\n{without(PACKAGE_LINTS, 'cognitive_complexity')}"
        ),
        "dylint.toml": MACRO_AWARE_DYLINT,
        "clippy.toml": THRESHOLD_CLIPPY,
        "Makefile": WHITAKER_MAKEFILE,
    },
    "cognitive_whitaker_ceiling_too_high": {
        "Cargo.toml": package(
            "app", f"\n{without(PACKAGE_LINTS, 'cognitive_complexity')}"
        ),
        "dylint.toml": MACRO_AWARE_DYLINT.replace(
            "max_complexity = 9", "max_complexity = 12"
        ),
        "clippy.toml": OTHER_CLIPPY,
        "Makefile": WHITAKER_MAKEFILE,
    },
    "cognitive_whitaker_float_ceiling": {
        "Cargo.toml": package(
            "app", f"\n{without(PACKAGE_LINTS, 'cognitive_complexity')}"
        ),
        "dylint.toml": MACRO_AWARE_DYLINT.replace(
            "max_complexity = 9", "max_complexity = 9.0"
        ),
        "clippy.toml": OTHER_CLIPPY,
        "Makefile": WHITAKER_MAKEFILE,
    },
    "cognitive_unset_and_no_optin": {
        "Cargo.toml": package(
            "app", f"\n{without(PACKAGE_LINTS, 'cognitive_complexity')}"
        ),
    },
    # -- PyO3 dependency is recorded ---------------------------------------------
    "pyo3_renamed_dependency": {
        "Cargo.toml": package(
            "ext",
            '\n[dependencies]\npy = { package = "pyo3", version = "0.30" }\n\n'
            + without(PACKAGE_LINTS, "missing_const_for_fn"),
        ),
    },
    # -- unreadable inputs --------------------------------------------------------
    "unreadable_root_manifest": {"Cargo.toml": UNREADABLE},
    "unreadable_member_manifest": {
        "Cargo.toml": VIRTUAL + WORKSPACE_LINTS,
        **members("a"),
        "crates/b/Cargo.toml": UNREADABLE,
    },
    "unreadable_extension_manifest": {
        "pyproject.toml": PYPROJECT,
        "rust_extension/Cargo.toml": UNREADABLE,
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


def build_fixture_envelope(files: dict[str, str]) -> LintTablesEnvelope:
    """Return the production envelope for a scenario, with a stable path.

    Returns
    -------
    LintTablesEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so the
        temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="lint-tables-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(files, checkout)
        envelope = build_lint_tables_envelope(checkout)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def main() -> None:
    """Regenerate ``data.json``, the bundle ``conftest verify --data`` consumes."""
    envelopes = {key: build_fixture_envelope(files) for key, files in SCENARIOS.items()}
    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
    bundle = FIXTURES_DIR / "data.json"
    bundle.write_text(
        json.dumps(
            {
                "fixtures": envelopes,
                "parameters": manifest_parameters(),
                **baseline,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
