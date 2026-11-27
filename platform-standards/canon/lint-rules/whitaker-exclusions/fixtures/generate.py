"""Regenerate the policy-input fixtures for whitaker-exclusions.

Each scenario lays out a synthetic checkout from the file contents written
below, then hands it to the production ``build_whitaker_exclusions_envelope`` so
the recorded envelopes are exactly what ``concordat artefact rule run`` would
send to Conftest. ``data.json`` bundles every envelope under a ``fixtures`` key
for ``conftest verify``, beside the manifest's default parameters.

The scenarios are laid out in the three shapes a Rust repository takes: a
workspace designed as one (``crates/*``), a workspace grown from a single crate
(a root package with members), and a Rust extension inside a Python project
(``rust_extension/``, shaped on femtologging's, whose ``dylint.toml`` excludes
three whole crates under one comment block).

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/whitaker-exclusions/fixtures/generate.py
"""

from __future__ import annotations

import json
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.whitaker_exclusions_envelope import (
    WhitakerExclusionsEnvelope,
    build_whitaker_exclusions_envelope,
)

FIXTURES_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = FIXTURES_DIR.parent / "rule.yaml"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."

UNREADABLE = "[package\nname = \n"
WORKSPACE = '[workspace]\nmembers = ["crates/*"]\nresolver = "3"\n'
PYPROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\n'


def package(name: str, extra: str = "") -> str:
    """Return a `[package]` table, then *extra*."""
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


def designed(dylint: str, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a virtual workspace with members `a` and `b` and this `dylint.toml`."""
    return {
        "Cargo.toml": WORKSPACE,
        "crates/a/Cargo.toml": package("a"),
        "crates/b/Cargo.toml": package("b"),
        "dylint.toml": dylint,
        **(extra or {}),
    }


def grown(dylint: str) -> dict[str, str]:
    """Return a root package that gained a member, with this `dylint.toml`."""
    return {
        "Cargo.toml": package("app", "\n") + WORKSPACE,
        "crates/b/Cargo.toml": package("b"),
        "dylint.toml": dylint,
    }


def extension(dylint: str, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return femtologging's shape: a standalone crate in `rust_extension/`."""
    return {
        "pyproject.toml": PYPROJECT,
        "rust_extension/Cargo.toml": package(
            "femtologging_rs",
            "\n[dev-dependencies]\n"
            '_femtologging_rs = { path = ".", package = "femtologging_rs" }\n'
            'serde = "1"\n',
        ),
        "rust_extension/src/lib.rs": "",
        "rust_extension/tests/file_handler_tests.rs": "",
        "rust_extension/tests/heavy/main.rs": "",
        "rust_extension/dylint.toml": dylint,
        **(extra or {}),
    }


PATHS_WITH_REASON = (
    "[no_std_fs_operations]\n"
    "# The loader opens documents at paths a caller supplies, so a capability\n"
    "# handle cannot be derived. Revisit when the loader takes a Dir.\n"
    'excluded_paths = ["loader"]\n'
)
REASON_ABOVE_TABLE = (
    "# The loader opens documents at paths a caller supplies, so a capability\n"
    "# handle cannot be derived. Revisit when the loader takes a Dir.\n"
    "[no_std_fs_operations]\n"
    'excluded_paths = ["loader"]\n'
)
FEMTO_DYLINT = (
    "[whitaker]\n"
    'experimental = ["macro_aware_cognitive_complexity"]\n\n'
    "[macro_aware_cognitive_complexity]\nmax_complexity = 9\n\n"
    "# Crates excluded from the no_std_fs_operations lint because they legitimately\n"
    "# require ambient std::fs access:\n"
    "# - femtologging_rs: the library's public contract mirrors CPython's logging.\n"
    "# - file_handler_tests: integration tests that stage tempdir fixtures.\n"
    "# - heavy: loom and proptest concurrency tests.\n"
    "[no_std_fs_operations]\n"
    'excluded_crates = [\n    "_femtologging_rs",\n    "file_handler_tests",\n'
    '    "heavy",\n]\n'
)

SCENARIOS: dict[str, dict[str, str]] = {
    "no_dylint_config": {"Cargo.toml": WORKSPACE, "crates/a/Cargo.toml": package("a")},
    "config_without_exclusions": designed(
        "[whitaker]\nexperimental = []\n\n[bumpy_road_function]\nmin_bump_lines = 15\n"
    ),
    # -- WX-001: reasons --------------------------------------------------------
    "designed_reason_above_key": designed(PATHS_WITH_REASON),
    "designed_reason_above_table": designed(REASON_ABOVE_TABLE),
    "designed_paths_without_reason": designed(
        '[no_std_fs_operations]\nexcluded_paths = ["loader"]\n'
    ),
    "designed_blank_line_ends_the_reason": designed(
        "[no_std_fs_operations]\n# The loader opens ambient paths.\n\n"
        'excluded_paths = ["loader"]\n'
    ),
    "designed_bare_hash_is_no_reason": designed(
        '[no_std_fs_operations]\n#\n#\nexcluded_paths = ["loader"]\n'
    ),
    "designed_other_exclusion_key_without_reason": designed(
        '[some_lint]\nexcluded_modules = ["m"]\n'
    ),
    "designed_root_level_key_with_reason": designed(
        '# Fixtures stage ambient files.\nexcluded_paths = ["fixtures"]\n'
    ),
    "grown_paths_without_reason": grown(
        '[no_std_fs_operations]\nexcluded_paths = ["loader"]\n'
    ),
    "grown_reason_above_key": grown(PATHS_WITH_REASON),
    "extension_paths_without_reason": extension(
        '[no_std_fs_operations]\nexcluded_paths = ["loader"]\n'
    ),
    # -- WX-002: whole governed crates ------------------------------------------
    "designed_governed_crate_excluded": designed(
        "[no_std_fs_operations]\n# Crate a stages ambient files.\n"
        'excluded_crates = ["a"]\n'
    ),
    "designed_governed_crate_excluded_by_rustc_name": designed(
        "[no_std_fs_operations]\n# Crate my-crate stages ambient files.\n"
        'excluded_crates = ["my_crate"]\n',
        extra={"crates/my-crate/Cargo.toml": package("my-crate")},
    ),
    "grown_governed_crate_excluded": grown(
        "[no_std_fs_operations]\n# The root crate is ambient.\n"
        'excluded_crates = ["app"]\n'
    ),
    "extension_femtologging_shape": extension(
        FEMTO_DYLINT,
    ),
    "designed_fixture_and_vendored_crates_excluded": designed(
        "[no_std_fs_operations]\n# Fixtures and a vendored harness are not ours.\n"
        'excluded_crates = ["bad", "dylint_testing"]\n',
        extra={
            "tests/fixtures/bad/Cargo.toml": package("bad"),
            "vendor/dylint_testing/Cargo.toml": package("dylint_testing"),
        },
    ),
    "designed_test_crate_by_file_name": designed(
        "[no_std_fs_operations]\n# Ambient tempdirs.\n"
        'excluded_crates = ["handler_tests"]\n',
        extra={"crates/a/tests/handler_tests.rs": ""},
    ),
    "designed_binary_crate_excluded": designed(
        "[no_std_fs_operations]\n# The entry point is ambient.\n"
        'excluded_crates = ["tool"]\n',
        extra={"crates/a/src/bin/tool.rs": ""},
    ),
    # -- WX-003: stale ----------------------------------------------------------
    "designed_stale_crate_exclusion": designed(
        '[no_std_fs_operations]\n# Removed crate.\nexcluded_crates = ["ghost"]\n'
    ),
    "designed_registry_dependency_is_not_a_crate": {
        **designed(
            '[no_std_fs_operations]\n# Third party.\nexcluded_crates = ["serde"]\n'
        ),
        "crates/a/Cargo.toml": package("a", '\n[dependencies]\nserde = "1"\n'),
    },
    # -- unreadable inputs --------------------------------------------------------
    "unreadable_dylint_config": designed("[whitaker\nexperimental = \n"),
    "unreadable_manifest_blocks_stale": designed(
        '[no_std_fs_operations]\n# Removed.\nexcluded_crates = ["ghost"]\n',
        extra={"crates/b/Cargo.toml": UNREADABLE},
    ),
    "dotted_key_is_not_located": designed(
        'no_std_fs_operations.excluded_crates = ["a"]\n'
    ),
    "exclusion_that_is_not_a_list": designed(
        '[no_std_fs_operations]\n# Reason.\nexcluded_crates = "a"\n'
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


def build_fixture_envelope(files: dict[str, str]) -> WhitakerExclusionsEnvelope:
    """Return the production envelope for a scenario, with a stable path.

    Returns
    -------
    WhitakerExclusionsEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so the
        temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="whitaker-exclusions-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(files, checkout)
        envelope = build_whitaker_exclusions_envelope(checkout)
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
