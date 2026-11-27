"""Unit tests for the `policy-input/rust-lint-tables` envelope builder.

The Rego suite proves what the policy makes of each envelope; these tests prove
what the builder records: how a package's lint tables are resolved the way Cargo
resolves them, which workspace governs a package, what the gate facts are, and
what it refuses to guess.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.lint_tables_envelope import (
    ENVELOPE_KIND,
    build_lint_tables_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

WORKSPACE_LINTS: typ.Final = '[workspace.lints.clippy]\nunwrap_used = "deny"\n'
PACKAGE_LINTS: typ.Final = '[lints.clippy]\nunwrap_used = "deny"\n'
INHERIT: typ.Final = "[lints]\nworkspace = true\n"


def _package(name: str, extra: str = "") -> str:
    """Return a `[package]` table followed by *extra*."""
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


def _write(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write *files* under *checkout*, creating directories."""
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _packages(
    checkout: pathlib.Path, files: dict[str, str]
) -> dict[str, dict[str, typ.Any]]:
    """Build the envelope for *files* and return its packages by name."""
    _write(checkout, files)
    envelope = build_lint_tables_envelope(checkout)
    return {p["name"]: typ.cast("dict[str, typ.Any]", p) for p in envelope["packages"]}


def test_the_envelope_names_its_kind_and_schema(tmp_path: pathlib.Path) -> None:
    """The policy refuses a document that is not this kind."""
    envelope = build_lint_tables_envelope(tmp_path)
    assert envelope["kind"] == ENVELOPE_KIND
    assert envelope["schema_version"] == 1
    assert envelope["packages"] == []


class TestStates:
    """How lint tables reach a package."""

    def test_a_member_that_opts_in_takes_the_workspace_tables(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`[lints] workspace = true` resolves to `[workspace.lints]`."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": '[workspace]\nmembers = ["a"]\n' + WORKSPACE_LINTS,
                "a/Cargo.toml": _package("a", INHERIT),
            },
        )
        assert found["a"]["state"] == "inherited"
        assert found["a"]["workspace"] == "Cargo.toml"
        assert found["a"]["effective"]["clippy"]["unwrap_used"] == {
            "level": "deny",
            "priority": None,
        }

    def test_a_member_with_no_tables_in_a_workspace_that_has_them_is_not_opted_in(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The workspace table alone is not inherited."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": '[workspace]\nmembers = ["a"]\n' + WORKSPACE_LINTS,
                "a/Cargo.toml": _package("a"),
            },
        )
        assert found["a"]["state"] == "not_opted_in"
        assert found["a"]["effective"]["clippy"] == {}

    def test_the_root_package_is_a_member_that_must_opt_in(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A grown workspace's root package is judged like any member."""
        found = _packages(
            tmp_path,
            {"Cargo.toml": _package("app", "\n[workspace]\n" + WORKSPACE_LINTS)},
        )
        assert found["app"]["state"] == "not_opted_in"

    def test_opting_in_without_a_workspace_table_is_dangling(
        self, tmp_path: pathlib.Path
    ) -> None:
        """There is nothing to inherit, so the package has no tables."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": '[workspace]\nmembers = ["a"]\n',
                "a/Cargo.toml": _package("a", INHERIT),
            },
        )
        assert found["a"]["state"] == "dangling"
        assert found["a"]["effective"]["clippy"] == {}

    def test_own_tables_apply_when_the_package_has_them(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A standalone crate with `[lints.*]` compiles under them."""
        found = _packages(tmp_path, {"Cargo.toml": _package("app", PACKAGE_LINTS)})
        assert found["app"]["state"] == "own"
        assert found["app"]["workspace"] is None

    def test_no_tables_anywhere_is_none(self, tmp_path: pathlib.Path) -> None:
        """The state the femtologging extension had before its lint tables."""
        found = _packages(tmp_path, {"Cargo.toml": _package("app")})
        assert found["app"]["state"] == "none"

    def test_level_and_priority_are_read_from_the_table_form(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`{ level, priority }` is as valid as a bare level."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": _package(
                    "app",
                    '[lints.clippy]\npedantic = { level = "warn", priority = -1 }\n',
                )
            },
        )
        assert found["app"]["effective"]["clippy"]["pedantic"] == {
            "level": "warn",
            "priority": -1,
        }


class TestWorkspaces:
    """Which workspace governs a package."""

    def test_a_standalone_extension_is_governed_without_a_root_workspace(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`rust_extension/Cargo.toml` in a Python project is a package."""
        found = _packages(
            tmp_path,
            {
                "pyproject.toml": '[project]\nname = "demo"\n',
                "rust_extension/Cargo.toml": _package("ext", PACKAGE_LINTS),
            },
        )
        assert found["ext"]["manifest"] == "rust_extension/Cargo.toml"
        assert found["ext"]["directory"] == "rust_extension"

    def test_a_nested_workspace_governs_its_own_members(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A workspace below the root is a workspace, not only the root one."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": _package(
                    "app", INHERIT + "\n[workspace]\n" + WORKSPACE_LINTS
                ),
                "tools/Cargo.toml": '[workspace]\nmembers = ["x"]\n' + WORKSPACE_LINTS,
                "tools/x/Cargo.toml": _package("x", INHERIT),
            },
        )
        assert found["x"]["workspace"] == "tools/Cargo.toml"
        assert found["x"]["state"] == "inherited"

    def test_a_crate_outside_the_members_is_standalone(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A crate a workspace does not list does not inherit from it."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": '[workspace]\nmembers = ["a"]\n' + WORKSPACE_LINTS,
                "a/Cargo.toml": _package("a", INHERIT),
                "other/Cargo.toml": _package("other", INHERIT),
            },
        )
        assert found["other"]["workspace"] is None
        assert found["other"]["state"] == "dangling"

    def test_default_members_do_not_remove_a_member_from_the_audit(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Every member is governed, whatever a bare command selects."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": (
                    '[workspace]\nmembers = ["a", "b"]\ndefault-members = ["a"]\n'
                    + WORKSPACE_LINTS
                ),
                "a/Cargo.toml": _package("a", INHERIT),
                "b/Cargo.toml": _package("b"),
            },
        )
        assert set(found) == {"a", "b"}
        assert found["b"]["state"] == "not_opted_in"

    @pytest.mark.parametrize("tree", ["fixtures", "vendor", "target", "examples"])
    def test_fixture_and_vendored_trees_are_not_governed(
        self, tmp_path: pathlib.Path, tree: str
    ) -> None:
        """A crate that is test data must not fail the rule guarding the real ones."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                f"{tree}/bad/Cargo.toml": _package("bad"),
            },
        )
        assert set(found) == {"app"}


class TestExternalMembers:
    """A member may sit outside its workspace root's directory."""

    def test_a_member_the_workspace_lists_by_a_parent_path_belongs_to_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`members = ["../crate"]` is a member, though not below the root."""
        found = _packages(
            tmp_path,
            {
                "ws/Cargo.toml": '[workspace]\nmembers = ["../crate"]\n'
                + WORKSPACE_LINTS,
                "crate/Cargo.toml": _package("outside", INHERIT),
            },
        )
        assert found["outside"]["workspace"] == "ws/Cargo.toml"
        assert found["outside"]["state"] == "inherited"

    def test_a_member_that_names_its_workspace_belongs_to_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`package.workspace` says which root, whatever the root lists."""
        found = _packages(
            tmp_path,
            {
                "ws/Cargo.toml": "[workspace]\nmembers = []\n" + WORKSPACE_LINTS,
                "crate/Cargo.toml": _package(
                    "outside", 'workspace = "../ws"\n' + INHERIT
                ),
            },
        )
        assert found["outside"]["workspace"] == "ws/Cargo.toml"
        assert found["outside"]["state"] == "inherited"

    def test_a_package_naming_a_workspace_that_is_not_there_is_standalone(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Nothing is guessed about a root the checkout does not hold."""
        found = _packages(
            tmp_path,
            {
                "crate/Cargo.toml": _package(
                    "lone", 'workspace = "../missing"\n' + INHERIT
                )
            },
        )
        assert found["lone"]["workspace"] is None
        assert found["lone"]["state"] == "dangling"


class TestPyo3:
    """Whether a package depends on PyO3."""

    @pytest.mark.parametrize(
        "dependencies",
        [
            '[dependencies]\npyo3 = "0.30"\n',
            '[dependencies]\npy = { package = "pyo3", version = "0.30" }\n',
            '[dev-dependencies]\npyo3 = "0.30"\n',
            "[target.'cfg(unix)'.dependencies]\npyo3 = \"0.30\"\n",
        ],
    )
    def test_a_pyo3_dependency_is_recorded(
        self, tmp_path: pathlib.Path, dependencies: str
    ) -> None:
        """Direct, renamed, dev and target-specific dependencies all count."""
        found = _packages(tmp_path, {"Cargo.toml": _package("ext", dependencies)})
        assert found["ext"]["depends_on_pyo3"] is True

    def test_a_package_without_pyo3_is_not_marked(self, tmp_path: pathlib.Path) -> None:
        """The divergence set applies to extensions only."""
        found = _packages(tmp_path, {"Cargo.toml": _package("lib")})
        assert found["lib"]["depends_on_pyo3"] is False


class TestConfiguration:
    """The two files that decide how cognitive complexity is gated."""

    def test_the_nearest_files_at_or_above_the_package_apply(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Clippy and Dylint load the nearest file, not a merge of several."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                "clippy.toml": "cognitive-complexity-threshold = 9\n",
                "dylint.toml": (
                    '[whitaker]\nexperimental = ["macro_aware_cognitive_complexity"]\n'
                    "[macro_aware_cognitive_complexity]\nmax_complexity = 9\n"
                ),
            },
        )
        config = found["app"]["config"]
        assert config["clippy"] == {
            "path": "clippy.toml",
            "cognitive_threshold": 9,
        }
        assert config["dylint"]["experimental"] == ["macro_aware_cognitive_complexity"]
        assert config["dylint"]["max_complexity_is_integer"] is True

    def test_a_float_ceiling_is_not_an_integer(self, tmp_path: pathlib.Path) -> None:
        """Whitaker rejects 9.0, which JSON cannot tell from 9."""
        found = _packages(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                "dylint.toml": (
                    "[macro_aware_cognitive_complexity]\nmax_complexity = 9.0\n"
                ),
            },
        )
        assert found["app"]["config"]["dylint"]["max_complexity_is_integer"] is False

    def test_absent_files_are_recorded_as_absent(self, tmp_path: pathlib.Path) -> None:
        """No file means no threshold and no opt-in."""
        found = _packages(tmp_path, {"Cargo.toml": _package("app", PACKAGE_LINTS)})
        assert found["app"]["config"]["clippy"] == {
            "path": None,
            "cognitive_threshold": None,
        }
        assert found["app"]["config"]["dylint"]["experimental"] is None


class TestGate:
    """The Miri and Whitaker facts."""

    def test_a_miri_target_the_gate_reaches_is_found(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`make test` running `cargo miri test` makes Miri reachable."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                "Makefile": ".PHONY: test\ntest:\n\tcargo +nightly miri test\n",
            },
        )
        miri = build_lint_tables_envelope(tmp_path)["miri"]
        assert miri["is_reachable"] is True
        assert miri["sources"] == ["Makefile:3 (test)"]

    @pytest.mark.parametrize(
        ("recipe", "expected"),
        [
            ("cargo miri test", True),
            ("@cargo +nightly miri nextest run", True),
            ("MIRIFLAGS=-Zmiri-strict-provenance cargo miri test", True),
            ("env MIRIFLAGS=x cargo miri test", True),
            ("if true; then cargo miri test; fi", True),
            ('if [ -n "$X" ]; then cargo miri test; fi', True),
            ("if false; then cargo miri test; fi", False),
            ("if false; then cargo miri test; else cargo miri nextest run; fi", True),
            ('echo "cargo miri test"', False),
            ("printf '%s' 'cargo miri test'", False),
            ("cargo test --features miri-helpers", False),
            ("cargo-miri-setup", False),
            ("cargo miri-ish", False),
        ],
    )
    def test_only_a_command_that_runs_cargo_miri_counts(
        self, tmp_path: pathlib.Path, recipe: str, *, expected: bool
    ) -> None:
        """Quoted text and look-alike words are not runs."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                "Makefile": f".PHONY: test\ntest:\n\t{recipe}\n",
            },
        )
        miri = build_lint_tables_envelope(tmp_path)["miri"]
        assert miri["is_reachable"] is expected, recipe

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("if false; then\n            cargo miri test\n          fi\n", False),
            ("if true; then\n            cargo miri test\n          fi\n", True),
            (
                (
                    "if false; then\n            true\n          fi\n"
                    "          cargo miri test\n"
                ),
                True,
            ),
        ],
    )
    def test_shell_control_flow_across_a_workflow_step_is_read(
        self, tmp_path: pathlib.Path, body: str, *, expected: bool
    ) -> None:
        """A block that cannot run hides its commands, on the lines after it too."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                ".github/workflows/ci.yml": (
                    "name: ci\non: push\njobs:\n  miri:\n    runs-on: ubuntu-latest\n"
                    f"    steps:\n      - run: |\n          {body}"
                ),
            },
        )
        miri = build_lint_tables_envelope(tmp_path)["miri"]
        assert miri["is_reachable"] is expected, body

    @pytest.mark.parametrize(
        "preamble",
        [".ONESHELL:\n", "S := .ONESHELL\n$(S):\n", "S = .ONESHELL\n$(S):\n"],
    )
    def test_oneshell_carries_an_if_false_across_recipe_lines(
        self, tmp_path: pathlib.Path, preamble: str
    ) -> None:
        """`.ONESHELL`, spelled directly or through a variable, makes one shell."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                "Makefile": (
                    f"{preamble}.PHONY: test\ntest:\n\tif false; then\n"
                    "\t  cargo miri test\n\tfi\n"
                ),
            },
        )
        miri = build_lint_tables_envelope(tmp_path)["miri"]
        assert miri["is_reachable"] is False

    def test_a_target_name_that_may_expand_to_oneshell_leaves_miri_open(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An unresolved target name could be `.ONESHELL`; no line is credited."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                "Makefile": ("$(MODE):\n.PHONY: test\ntest:\n\tcargo miri test\n"),
            },
        )
        miri = build_lint_tables_envelope(tmp_path)["miri"]
        assert (miri["is_reachable"], miri["is_resolved"]) == (False, False)

    def test_each_recipe_line_is_its_own_shell_without_oneshell(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A recipe of whole lines runs each on its own, and each counts."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                "Makefile": (
                    ".PHONY: test\ntest:\n\tif false; then cargo miri test; fi\n"
                    "\tcargo miri test\n"
                ),
            },
        )
        miri = build_lint_tables_envelope(tmp_path)["miri"]
        assert miri["sources"] == ["Makefile:4 (test)"]

    def test_a_miri_target_outside_the_gate_is_not_reachable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A target no gate target reaches proves nothing about the gate."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                "Makefile": (
                    ".PHONY: test miri\ntest:\n\t@echo test\nmiri:\n\tcargo miri test\n"
                ),
            },
        )
        miri = build_lint_tables_envelope(tmp_path)["miri"]
        assert miri["is_reachable"] is False
        assert miri["is_resolved"] is True

    def test_a_miri_step_in_a_workflow_is_found(self, tmp_path: pathlib.Path) -> None:
        """CI counts as the gate."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                ".github/workflows/ci.yml": (
                    "name: ci\non: push\njobs:\n  miri:\n    runs-on: ubuntu-latest\n"
                    "    steps:\n      - run: cargo miri test\n"
                ),
            },
        )
        assert build_lint_tables_envelope(tmp_path)["miri"]["is_reachable"] is True

    def test_a_computed_matrix_that_names_miri_leaves_the_answer_unresolved(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A step the audit cannot fill in might run Miri; it must not say no."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                ".github/workflows/ci.yml": (
                    "name: ci\non: push\njobs:\n  miri:\n    runs-on: ubuntu-latest\n"
                    "    strategy:\n"
                    "      matrix: ${{ fromJson(needs.plan.outputs.m) }}\n"
                    "    steps:\n      - run: cargo miri test\n"
                ),
            },
        )
        miri = build_lint_tables_envelope(tmp_path)["miri"]
        assert miri["is_reachable"] is False
        assert miri["is_resolved"] is False

    def test_whitaker_commands_carry_what_they_select(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The macro-aware credit depends on a whole-workspace Whitaker run."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                "Makefile": (
                    ".PHONY: lint\nlint:\n"
                    "\twhitaker --all -- --workspace --all-targets\n"
                ),
            },
        )
        runs = build_lint_tables_envelope(tmp_path)["whitaker_runs"]
        assert [(r["is_resolved"], r["is_workspace"]) for r in runs] == [(True, True)]


class TestUnreadable:
    """Nothing is guessed."""

    def test_an_invalid_manifest_is_recorded_not_skipped(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The policy reports it as indeterminate."""
        _write(
            tmp_path,
            {
                "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n' + WORKSPACE_LINTS,
                "a/Cargo.toml": _package("a", INHERIT),
                "b/Cargo.toml": "[package\nname = \n",
            },
        )
        envelope = build_lint_tables_envelope(tmp_path)
        assert [p["name"] for p in envelope["packages"]] == ["a"]
        assert len(envelope["unreadable"]) == 1
        assert envelope["unreadable"][0].startswith("b/Cargo.toml: invalid TOML")

    def test_unplaceable_gate_lines_are_kept_apart_from_manifests(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A PowerShell Whitaker call cannot be read; it is not a manifest defect."""
        _write(
            tmp_path,
            {
                "Cargo.toml": _package("app", PACKAGE_LINTS),
                ".github/workflows/ci.yml": (
                    "name: ci\non: push\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: \"& $whitaker --all '--' --all-targets\"\n"
                ),
            },
        )
        envelope = build_lint_tables_envelope(tmp_path)
        assert envelope["unreadable"] == []
        assert len(envelope["gate_unreadable"]) == 1
