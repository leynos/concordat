"""Regenerate the policy-input fixture envelopes for documentation-presence.

Each scenario lays out a synthetic checkout from the file contents written
below, then hands it to the production ``build_documentation_envelope`` so the
recorded envelopes are exactly what ``concordat artefact rule run`` would send
to Conftest. The Makefile facts therefore come from the pinned ``makeutil`` on
PATH. ``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify``, beside the manifest's default parameters.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/documentation-presence/fixtures/generate.py
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.documentation_envelope import (
    DocumentationEnvelope,
    build_documentation_envelope,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

FIXTURES_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = FIXTURES_DIR.parent / "rule.yaml"
ENVELOPES_DIR = FIXTURES_DIR / "envelopes"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."

DOCSTRING = '"""Documented."""\n'
PYTHON_FILES: typ.Final = {
    "pkg/__init__.py": DOCSTRING,
    "pkg/core.py": DOCSTRING,
    "tests/test_core.py": DOCSTRING,
    "scripts/tool.py": DOCSTRING,
    ".github/scripts/helper.py": DOCSTRING,
}

# The guard is the dev-env-rocky shape: capture the report, keep the tool's own
# exit status, then fail on any MISSED row the rounded percentage hid.
GUARDED_RECIPE: typ.Final = (
    '\t@report="$$($(INTERROGATE) $(FLAGS) $(INTERROGATE_TARGETS))"; \\\n'
    "\t  status=$$?; \\\n"
    "\t  printf '%s\\n' \"$$report\"; \\\n"
    '\t  test "$$status" -eq 0 || exit "$$status"; \\\n'
    "\t  if printf '%s\\n' \"$$report\" | grep -q '| *MISSED |'; then exit 1; fi\n"
)


DEFAULT_LINT: typ.Final = "lint: interrogate\n\truff check .\n"


@dataclasses.dataclass(frozen=True)
class Wiring:
    """The knobs of the reference Makefile a scenario turns."""

    flags: str = "-vv --fail-under 100"
    targets: str = "pkg scripts tests .github/scripts"
    recipe: str = GUARDED_RECIPE
    extra: str = ""
    lint: str = DEFAULT_LINT
    tool: str = "uv tool run --from 'interrogate==1.7.0' interrogate"

    def render(self) -> str:
        """Return the Makefile text: variables, `lint`, then the gate target.

        Returns
        -------
        str
            A Makefile whose `lint` reaches a guarded interrogate target unless
            the scenario changed that.
        """
        return (
            f"INTERROGATE ?= {self.tool}\n"
            f"INTERROGATE_TARGETS ?= {self.targets}\n"
            f"FLAGS = {self.flags}\n"
            f"{self.extra}\n"
            ".PHONY: lint interrogate\n"
            f"{self.lint}\n"
            f"interrogate:\n{self.recipe}"
        )


def makefile(**changes: str) -> str:
    """Return the reference Makefile with *changes* to its `Wiring` applied.

    Returns
    -------
    str
        The Makefile text.
    """
    return dataclasses.replace(Wiring(), **changes).render()


def pyproject(table: str = "") -> str:
    """Return a pyproject with an optional ``[tool.interrogate]`` table."""
    body = '[project]\nname = "demo"\nversion = "0.1.0"\n'
    return f"{body}\n[tool.interrogate]\n{table}" if table else body


@dataclasses.dataclass(frozen=True)
class Scenario:
    """The files one fixture checkout holds, by relative path.

    Attributes
    ----------
    files:
        Relative path to text. A scenario starts from ``BASE`` and layers
        these over it; a value of ``None`` removes the file.
    """

    files: cabc.Mapping[str, str | bytes | None] = dataclasses.field(
        default_factory=dict
    )


BASE: typ.Final = {
    "Makefile": makefile(),
    "pyproject.toml": pyproject(),
    **PYTHON_FILES,
}

NO_PYTHON: typ.Final = dict.fromkeys(PYTHON_FILES)

# Rust scenarios own the checkout outright: no Python, and a Makefile whose
# `lint` runs clippy, so a scenario changes only the `missing_docs` facts.
CLIPPY_MAKEFILE: typ.Final = (
    "CARGO ?= cargo\n.PHONY: lint\n"
    "lint:\n\t$(CARGO) clippy --workspace --all-targets -- -D warnings\n"
)
WORKSPACE_MANIFEST: typ.Final = (
    '[workspace]\nmembers = ["crates/core"]\nresolver = "3"\n\n'
    '[workspace.lints.rust]\nmissing_docs = "deny"\n'
)
MEMBER_MANIFEST: typ.Final = (
    '[package]\nname = "core"\nversion = "0.1.0"\nedition = "2024"\n\n'
    "[lints]\nworkspace = true\n"
)
LIB_ROOT: typ.Final = "//! The core crate.\n"


def crate_manifest(lints: str = "") -> str:
    """Return a single-crate manifest with an optional lint table.

    Returns
    -------
    str
        The Cargo.toml text.
    """
    package = '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2024"\n'
    return f"{package}\n{lints}" if lints else package


def rust_scenario(files: cabc.Mapping[str, str | bytes | None]) -> Scenario:
    """Return a Rust-only scenario laid over the clippy Makefile.

    Returns
    -------
    Scenario
        A scenario that removes the Python base and adds *files*.
    """
    return Scenario({
        **NO_PYTHON,
        "pyproject.toml": None,
        "Makefile": CLIPPY_MAKEFILE,
        **files,
    })


EXCEPTION_MAKEFILE: typ.Final = (
    ".PHONY: lint\nLANES ?= scripts/lint_lanes.sh\nlint:\n\tsh $(LANES)\n"
)
LANES_SCRIPT: typ.Final = (
    "#!/bin/sh\n# Run clippy once per feature lane.\n"
    "cargo clippy --workspace --features lane-a -- -D warnings\n"
)


def exception_guide(body: str) -> str:
    """Return a developers' guide holding a clippy-gate exception section.

    Returns
    -------
    str
        The Markdown text.
    """
    return f"# Developers' guide\n\n## Clippy gate exception\n\n{body}\n"


def exception_scenario(
    guide_body: str,
    *,
    script: str | bytes | None = LANES_SCRIPT,
    makefile: str = EXCEPTION_MAKEFILE,
) -> Scenario:
    """Return a Rust scenario whose lint reaches clippy only through a script.

    Returns
    -------
    Scenario
        A deny-compliant crate, *makefile*, an optional lane script and a guide.
    """
    return rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": makefile,
        "scripts/lint_lanes.sh": script,
        "docs/developers-guide.md": exception_guide(guide_body),
    })


NETSUKE_MAKEFILE: typ.Final = (
    ".PHONY: lint\nNETSUKE ?= netsuke\nlint:\n\t$(NETSUKE) build lint\n"
)
NETSUKE_MANIFEST: typ.Final = """\
netsuke_version: "1.0.0"

vars:
  cargo_flags: "--all-targets --all-features"

actions:
  - name: clippy-lint
    command: >-
      sh -e -c '"$${CARGO:-cargo}" clippy {{ cargo_flags }} -- -D warnings'

  - name: lint
    deps: clippy-lint
    command: "true"
"""


def netsuke_scenario(
    manifest: str | None = NETSUKE_MANIFEST,
    *,
    makefile: str = NETSUKE_MAKEFILE,
) -> Scenario:
    """Return a Rust scenario whose lint is a Netsuke target.

    Returns
    -------
    Scenario
        A deny-compliant crate, *makefile* and an optional `Netsukefile`.
    """
    return rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": makefile,
        "Netsukefile": manifest,
    })


SCENARIOS: typ.Final[dict[str, Scenario]] = {
    "compliant": Scenario(),
    "not_applicable": Scenario({
        **NO_PYTHON,
        "Makefile": "build:\n\tcc -o x x.c\n",
    }),
    "only_fixtures": Scenario({
        **NO_PYTHON,
        "Makefile": "build:\n\tcc -o x x.c\n",
        "tests/fixtures/sample.py": "x = 1\n",
    }),
    "vendored_python_ignored": Scenario({
        "vendor/dep/mod.py": "x = 1\n",
        "node_modules/pkg/tool.py": "x = 1\n",
    }),
    # DG-001: the gate exists and is reachable.
    "no_makefile": Scenario({"Makefile": None}),
    "no_lint_target": Scenario({
        "Makefile": (
            "INTERROGATE ?= interrogate\n"
            "build:\n\t$(INTERROGATE) -vv --fail-under 100 pkg\n"
        )
    }),
    "no_gate": Scenario({"Makefile": "lint:\n\truff check .\n"}),
    "gate_outside_lint": Scenario({
        "Makefile": makefile(lint="lint:\n\truff check .\n")
    }),
    "lint_unresolved_prerequisite": Scenario({
        "Makefile": "lint: $(CHECKS)\n\truff check .\n"
    }),
    "gate_through_sub_make": Scenario({
        "Makefile": makefile(lint="lint:\n\truff check .\n\t+$(MAKE) interrogate\n")
    }),
    "gate_unresolved": Scenario({
        "Makefile": makefile(extra="INTERROGATE = $(shell find-interrogate)\n")
    }),
    "gate_named_only_in_echo": Scenario({
        "Makefile": makefile(
            lint='lint:\n\t@echo "run make interrogate"\n\truff check .\n'
        )
    }),
    "gate_behind_environment_prefix": Scenario({
        "Makefile": makefile(
            tool=(
                "UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools "
                "uv tool run --from 'interrogate==1.7.0' interrogate"
            )
        )
    }),
    "gate_variable_twice": Scenario({
        "Makefile": makefile(extra="FLAGS = -vv --fail-under 100\n")
    }),
    # DG-001: the threshold.
    "threshold_low": Scenario({"Makefile": makefile(flags="-vv --fail-under 95")}),
    "threshold_missing": Scenario({"Makefile": makefile(flags="-vv")}),
    "threshold_not_a_number": Scenario({
        "Makefile": makefile(flags="-vv --fail-under high")
    }),
    "threshold_from_config": Scenario({
        "Makefile": makefile(flags="-vv"),
        "pyproject.toml": pyproject("fail-under = 100\n"),
    }),
    "threshold_from_config_low": Scenario({
        "Makefile": makefile(flags="-vv"),
        "pyproject.toml": pyproject("fail-under = 90\n"),
    }),
    "threshold_cli_beats_config": Scenario({
        "pyproject.toml": pyproject("fail-under = 50\n")
    }),
    # DG-001: relaxing flags.
    "ignore_flag": Scenario({
        "Makefile": makefile(flags="-vv --fail-under 100 --ignore-nested-functions")
    }),
    "ignore_short_flags": Scenario({"Makefile": makefile(flags="-vv -f 100 -nC")}),
    "ignore_regex_flag": Scenario({
        "Makefile": makefile(flags="-vv --fail-under 100 --ignore-regex '^main$$'")
    }),
    "ignore_config": Scenario({"pyproject.toml": pyproject("ignore-magic = true\n")}),
    "ignore_config_and_regex": Scenario({
        "pyproject.toml": pyproject('ignore-magic = true\nignore-regex = ["^main$"]\n')
    }),
    "ignore_config_false": Scenario({
        "pyproject.toml": pyproject("ignore-magic = false\nignore-regex = []\n")
    }),
    "ignore_overloaded_allowed": Scenario({
        "Makefile": makefile(
            flags="-vv --fail-under 100 --ignore-overloaded-functions"
        ),
        "pyproject.toml": pyproject("ignore-overloaded-functions = true\n"),
    }),
    # DG-001: exclusions.
    "exclude_tests": Scenario({
        "Makefile": makefile(flags="-vv --fail-under 100 --exclude tests")
    }),
    "exclude_tests_config": Scenario({
        "pyproject.toml": pyproject('exclude = ["tests"]\n')
    }),
    "exclude_fixtures_allowed": Scenario({
        "pyproject.toml": pyproject('exclude = ["tests/fixtures"]\n'),
        "tests/fixtures/sample.py": "x = 1\n",
    }),
    # DG-001: scope.
    "scope_partial": Scenario({"Makefile": makefile(targets="pkg")}),
    "scope_single_directory_missing": Scenario({
        "Makefile": makefile(targets="pkg scripts tests")
    }),
    "scope_dot": Scenario({"Makefile": makefile(targets=".")}),
    "scope_two_runs": Scenario({
        "Makefile": makefile(
            lint="lint: interrogate interrogate-tests\n\truff check .\n",
            extra=(
                "interrogate-tests:\n"
                + GUARDED_RECIPE.replace(
                    "$(INTERROGATE_TARGETS)", "scripts tests .github/scripts"
                )
            ),
            targets="pkg",
        )
    }),
    "scope_file_target": Scenario({
        "Makefile": makefile(targets="pkg scripts tests .github/scripts"),
        "conftest.py": DOCSTRING,
    }),
    "config_not_toml": Scenario({
        "Makefile": makefile(flags="-vv --fail-under 100 --config setup.cfg"),
        "setup.cfg": "[tool:interrogate]\nfail-under = 100\n",
    }),
    "config_missing": Scenario({
        "Makefile": makefile(flags="-vv --fail-under 100 --config nowhere.toml")
    }),
    "config_by_make_functions": Scenario({
        "Makefile": makefile(
            flags="-vv --config $(CONFIG)",
            extra=(
                "CONFIG = $(abspath $(dir $(lastword $(MAKEFILE_LIST)))"
                "pyproject.toml)\n"
            ),
        ),
        "pyproject.toml": pyproject("fail-under = 100\n"),
    }),
    # DG-002: the run is exact.
    "verbose_missing": Scenario({"Makefile": makefile(flags="--fail-under 100")}),
    "verbose_from_config": Scenario({
        "Makefile": makefile(flags="--fail-under 100"),
        "pyproject.toml": pyproject("verbose = 2\n"),
    }),
    "verbose_one": Scenario({"Makefile": makefile(flags="-v --fail-under 100")}),
    "verbose_split": Scenario({"Makefile": makefile(flags="-v -v --fail-under 100")}),
    "guard_result_ignored": Scenario({
        "Makefile": makefile(
            recipe=(
                "\t$(INTERROGATE) $(FLAGS) $(INTERROGATE_TARGETS) > report.txt\n"
                "\tgrep -q MISSED report.txt || true\n"
            )
        )
    }),
    "guard_negated": Scenario({
        "Makefile": makefile(
            recipe=(
                "\t$(INTERROGATE) $(FLAGS) $(INTERROGATE_TARGETS) > report.txt\n"
                "\t! grep -q MISSED report.txt\n"
            )
        )
    }),
    "guard_and_exit": Scenario({
        "Makefile": makefile(
            recipe=(
                "\t$(INTERROGATE) $(FLAGS) $(INTERROGATE_TARGETS) > report.txt\n"
                "\tgrep -q MISSED report.txt && exit 1; true\n"
            )
        )
    }),
    "unguarded": Scenario({
        "Makefile": makefile(
            recipe="\t$(INTERROGATE) $(FLAGS) $(INTERROGATE_TARGETS)\n"
        )
    }),
    "unguarded_and_quiet": Scenario({
        "Makefile": makefile(
            flags="--fail-under 100",
            recipe="\t$(INTERROGATE) $(FLAGS) $(INTERROGATE_TARGETS)\n",
        )
    }),
    # DG-003: every library crate denies `missing_docs`.
    "rust_compliant_workspace": rust_scenario({
        "Cargo.toml": WORKSPACE_MANIFEST,
        "crates/core/Cargo.toml": MEMBER_MANIFEST,
        "crates/core/src/lib.rs": LIB_ROOT,
    }),
    "rust_compliant_own_table": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
    }),
    "rust_compliant_root_attribute": rust_scenario({
        "Cargo.toml": crate_manifest(),
        "src/lib.rs": "//! Docs.\n#![deny(missing_docs)]\n",
    }),
    "rust_compliant_cfg_attr_not_test": rust_scenario({
        "Cargo.toml": crate_manifest(),
        "src/lib.rs": "#![cfg_attr(not(test), deny(missing_docs))]\n",
    }),
    "rust_compliant_expect_with_reason": rust_scenario({
        "Cargo.toml": crate_manifest(),
        "src/lib.rs": (
            "#![expect(\n"
            "    missing_docs,\n"
            '    reason = "derive macros emit undocumentable items"\n'
            ")]\n"
        ),
    }),
    "rust_binary_only": rust_scenario({
        "Cargo.toml": crate_manifest(),
        "src/main.rs": "fn main() {}\n",
    }),
    "rust_fixture_crate_ignored": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "tests/fixtures/undocumented/Cargo.toml": crate_manifest(),
        "tests/fixtures/undocumented/src/lib.rs": "pub fn f() {}\n",
    }),
    "rust_manifest_unset": rust_scenario({
        "Cargo.toml": crate_manifest(),
        "src/lib.rs": LIB_ROOT,
    }),
    "rust_workspace_not_inherited": rust_scenario({
        "Cargo.toml": WORKSPACE_MANIFEST,
        "crates/core/Cargo.toml": crate_manifest(),
        "crates/core/src/lib.rs": LIB_ROOT,
    }),
    "rust_one_member_unset": rust_scenario({
        "Cargo.toml": WORKSPACE_MANIFEST.replace(
            '["crates/core"]', '["crates/core", "crates/extra"]'
        ),
        "crates/core/Cargo.toml": MEMBER_MANIFEST,
        "crates/core/src/lib.rs": LIB_ROOT,
        "crates/extra/Cargo.toml": crate_manifest(),
        "crates/extra/src/lib.rs": LIB_ROOT,
    }),
    "rust_level_warn": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "warn"\n'),
        "src/lib.rs": LIB_ROOT,
    }),
    "rust_root_allow_beats_manifest": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": "#![allow(missing_docs)]\n",
    }),
    "rust_root_attribute_beats_manifest": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "warn"\n'),
        "src/lib.rs": "#![deny(missing_docs)]\n",
    }),
    "rust_expect_without_reason": rust_scenario({
        "Cargo.toml": crate_manifest(),
        "src/lib.rs": "#![expect(missing_docs)]\n",
    }),
    "rust_cfg_attr_other_condition": rust_scenario({
        "Cargo.toml": crate_manifest(),
        "src/lib.rs": '#![cfg_attr(feature = "docs", deny(missing_docs))]\n',
    }),
    "rust_item_expect_with_reason": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT + "mod schema;\n",
        "src/schema.rs": (
            "#![expect(\n"
            "    missing_docs,\n"
            '    reason = "diesel macros generate undocumented items"\n'
            ")]\n"
            "pub fn generated() {}\n"
        ),
    }),
    "rust_item_allow_without_reason": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT + "#[allow(missing_docs)]\npub mod hidden;\n",
        "src/hidden.rs": "pub fn undocumented() {}\n",
    }),
    "rust_item_expect_without_reason": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT + "pub mod nested;\n",
        "src/nested.rs": "#![expect(missing_docs)]\npub fn undocumented() {}\n",
    }),
    "rust_suppression_in_tests_ignored": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "tests/it.rs": "#![allow(missing_docs)]\n",
    }),
    "rust_manifest_invalid": rust_scenario({
        "Cargo.toml": "[package\n",
    }),
    "rust_root_missing": rust_scenario({
        "Cargo.toml": crate_manifest('[lib]\npath = "lib/mod.rs"\n'),
    }),
    # DG-004: `make lint` reaches clippy.
    "rust_no_makefile": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": None,
    }),
    "rust_no_lint_target": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": "build:\n\tcargo build\n",
    }),
    "rust_lint_without_clippy": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": ".PHONY: lint\nlint:\n\tcargo fmt --check\n",
    }),
    "rust_clippy_through_prerequisite": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": (
            ".PHONY: lint clippy\nlint: clippy\n\tcargo fmt --check\n"
            "clippy:\n\tcargo clippy -- -D warnings\n"
        ),
    }),
    "rust_clippy_unresolved": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": ".PHONY: lint\nlint:\n\t$(CLIPPY_CMD) --workspace\n",
    }),
    "rust_clippy_only_mentioned": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": (
            ".PHONY: lint\nlint:\n\trustup component add clippy\n"
            '\ttest -x "$$(command -v cargo-clippy)" || echo clippy missing\n'
        ),
    }),
    "rust_clippy_through_sub_make": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": (
            ".PHONY: lint clippy\nlint:\n\t$(MAKE) clippy\n"
            "clippy:\n\tcargo clippy -- -D warnings\n"
        ),
    }),
    "rust_suppression_text_in_string": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": (
            LIB_ROOT
            + "/// Generated code looks like `#[allow(missing_docs)]`.\n"
            + 'pub const SAMPLE: &str = "#[allow(missing_docs)]";\n'
        ),
    }),
    "rust_clippy_with_unresolved_flags": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": (
            ".PHONY: lint\nlint:\n"
            '\tRUSTFLAGS="-D warnings$(if $(X),-Zthreads=8)" cargo clippy\n'
        ),
    }),
    # DG-004 exception: a recorded wrapper script stands in for a clippy recipe line.
    "rust_exception_valid": exception_scenario(
        "Clippy runs through `scripts/lint_lanes.sh`: the PyO3 lanes need a "
        "toolchain that `cargo clippy` in a recipe cannot select."
    ),
    "rust_exception_script_missing": exception_scenario(
        "Clippy runs through `scripts/other_lanes.sh` for the PyO3 lanes.",
    ),
    "rust_exception_script_without_clippy": exception_scenario(
        "Clippy runs through `scripts/lint_lanes.sh` for the PyO3 lanes.",
        script="#!/bin/sh\ncargo fmt --check\n",
    ),
    "rust_exception_script_not_reached": exception_scenario(
        "Clippy runs through `scripts/lint_lanes.sh` for the PyO3 lanes.",
        makefile=".PHONY: lint\nlint:\n\tcargo fmt --check\n",
    ),
    "rust_exception_script_only_echoed": exception_scenario(
        "Clippy runs through `scripts/lint_lanes.sh` for the PyO3 lanes.",
        makefile=".PHONY: lint\nlint:\n\techo scripts/lint_lanes.sh\n",
    ),
    "rust_exception_guide_unreadable": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": ".PHONY: lint\nlint:\n\tcargo fmt --check\n",
        "docs/developers-guide.md": b"\xff\xfe## Clippy gate",
    }),
    "rust_exception_script_unreadable": exception_scenario(
        "Clippy runs through `scripts/lint_lanes.sh` for the PyO3 lanes.",
        script=b"\xff\xfe cargo clippy",
    ),
    "rust_exception_names_no_script": exception_scenario(
        "Clippy runs through a wrapper because of the PyO3 lanes.",
    ),
    "rust_exception_path_escape": exception_scenario(
        "Clippy runs through `../lint_lanes.sh` for the PyO3 lanes.",
    ),
    "rust_exception_unneeded": rust_scenario({
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
        "Makefile": CLIPPY_MAKEFILE,
        "docs/developers-guide.md": exception_guide(
            "Clippy runs through `scripts/gone.py`."
        ),
    }),
    # DG-004 through Netsuke: `netsuke build <target>` is followed into the Netsukefile.
    "rust_netsuke_clippy": netsuke_scenario(),
    "rust_netsuke_default_target": netsuke_scenario(
        NETSUKE_MANIFEST + "\ndefaults:\n  - lint\n",
        makefile=".PHONY: lint\nlint:\n\tnetsuke build\n",
    ),
    "rust_netsuke_variant_with_clippy": netsuke_scenario(
        'netsuke_version: "1.0.0"\n\nactions:\n'
        '  - name: lint\n    when: command_available("whitaker")\n'
        '    command: "cargo fmt --check"\n'
        '  - name: lint\n    when: not command_available("whitaker")\n'
        '    command: "cargo clippy -- -D warnings"\n'
    ),
    "rust_netsuke_target_without_clippy": netsuke_scenario(
        'netsuke_version: "1.0.0"\n\nactions:\n'
        '  - name: lint\n    command: "cargo fmt --check"\n'
    ),
    "rust_netsuke_clippy_only_echoed": netsuke_scenario(
        'netsuke_version: "1.0.0"\n\nactions:\n'
        '  - name: lint\n    command: "echo cargo clippy"\n'
    ),
    "rust_netsuke_manifest_missing": netsuke_scenario(None),
    "rust_netsuke_manifest_unparsable": netsuke_scenario("actions: [\n"),
    "rust_netsuke_target_undefined": netsuke_scenario(
        'netsuke_version: "1.0.0"\n\nactions:\n'
        '  - name: check\n    command: "cargo clippy"\n'
    ),
    "rust_netsuke_dependency_chain": netsuke_scenario(
        'netsuke_version: "1.0.0"\n\nactions:\n'
        "  - name: lint\n    deps:\n      - format\n      - clippy\n"
        '    command: "true"\n'
        '  - name: format\n    command: "cargo fmt --check"\n'
        '  - name: clippy\n    command: "cargo +nightly clippy"\n'
    ),
    "rust_and_python_compliant": Scenario({
        "Makefile": makefile(lint="lint: interrogate\n\tcargo clippy -- -D warnings\n"),
        "Cargo.toml": crate_manifest('[lints.rust]\nmissing_docs = "deny"\n'),
        "src/lib.rs": LIB_ROOT,
    }),
}


def lay_out(scenario: Scenario, checkout: Path) -> None:
    """Populate *checkout* with the base files, overlaid by *scenario*'s.

    Parameters
    ----------
    scenario:
        The scenario whose files compose the checkout.
    checkout:
        An existing empty directory to lay the files out in.
    """
    files = {**BASE, **scenario.files}
    for relative, text in files.items():
        if text is None:
            continue
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")


def build_fixture_envelope(scenario: Scenario) -> DocumentationEnvelope:
    """Return the production envelope for *scenario*, with a stable path.

    Returns
    -------
    DocumentationEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so the
        temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="documentation-presence-fixture-") as tmp:
        checkout = Path(tmp)
        lay_out(scenario, checkout)
        envelope = build_documentation_envelope(checkout)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def manifest_parameters() -> dict[str, object]:
    """Return the manifest defaults.

    Returns
    -------
    dict[str, object]
        The ``defaults`` of ``rule.yaml``.
    """
    manifest = YAML(typ="safe").load(MANIFEST_PATH.read_text(encoding="utf-8"))
    return dict(manifest["parameters"]["defaults"])


def main() -> None:
    """Regenerate every envelope and the bundled data document.

    Writes one JSON file per scenario under ``envelopes/`` and the bundle
    ``data.json`` that ``conftest verify --data`` consumes. Existing files are
    overwritten, so the pinned ``makeutil`` must be on PATH.
    """
    ENVELOPES_DIR.mkdir(exist_ok=True)
    envelopes = {
        key: build_fixture_envelope(scenario) for key, scenario in SCENARIOS.items()
    }
    for key, envelope in envelopes.items():
        target = ENVELOPES_DIR / f"{key}.json"
        target.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
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
