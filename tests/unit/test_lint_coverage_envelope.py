"""Unit tests for the `policy-input/gate-lint-coverage` envelope builder.

The Rego suite proves what the policy makes of each envelope; these tests prove
what the builder records: the Cargo surfaces and what they declare, which
surface each lint command targets, what the Makefile exports, and what it refuses
to guess.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.lint_coverage_envelope import (
    ENVELOPE_KIND,
    build_lint_coverage_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

MANIFEST: typ.Final = '[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'
APP: typ.Final = MANIFEST.format(name="app", extra="")
WORKFLOW_HEAD: typ.Final = (
    "name: ci\non: push\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
)


def _write(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write *files* under *checkout*, creating directories."""
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _gate(*lines: str, preamble: str = "") -> str:
    """Return a Makefile whose `lint` target runs *lines*."""
    body = "".join(f"\t{line}\n" for line in lines)
    return f"{preamble}.PHONY: lint\nlint:\n{body}"


def _app(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write a one-package checkout (`app` at the root) plus *files*."""
    _write(checkout, {"Cargo.toml": APP, **files})


def _runs(checkout: pathlib.Path) -> list[tuple[str, str | None]]:
    """Return `(tool, surface)` for each run of the checkout."""
    runs = build_lint_coverage_envelope(checkout)["runs"]
    return [(r["tool"], r["surface"]) for r in runs]


def test_the_envelope_names_its_kind(tmp_path: pathlib.Path) -> None:
    """The policy refuses a document that is not this kind."""
    envelope = build_lint_coverage_envelope(tmp_path)
    assert envelope["kind"] == ENVELOPE_KIND
    assert (envelope["surfaces"], envelope["runs"]) == ([], [])


class TestSurfaces:
    """What a workspace or a standalone package declares."""

    def test_a_workspace_is_one_surface_with_every_members_features(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Features are the members' own, named `package/feature`, `default` apart."""
        _write(
            tmp_path,
            {
                "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n',
                "a/Cargo.toml": MANIFEST.format(
                    name="a", extra='[features]\ndefault = ["x"]\nx = []\n'
                ),
                "a/src/lib.rs": "",
                "b/Cargo.toml": MANIFEST.format(name="b", extra="[features]\ny = []\n"),
            },
        )
        [surface] = build_lint_coverage_envelope(tmp_path)["surfaces"]
        assert surface["manifest"] == "Cargo.toml"
        assert surface["packages"] == ["a", "b"]
        assert surface["features"] == ["a/x", "b/y"]
        assert (surface["has_default"], surface["has_library"]) == (True, True)

    def test_a_package_qualifier_is_dropped_only_in_a_one_package_surface(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`-F app/fast` is `fast` for one package, and stays `app/fast` otherwise."""
        _app(
            tmp_path,
            {
                "Makefile": _gate("cargo clippy -F app/fast -F other/fast"),
                "src/lib.rs": "",
            },
        )
        [run] = build_lint_coverage_envelope(tmp_path)["runs"]
        assert run["features"] == ["fast", "other/fast"]

    def test_a_cd_inside_a_shell_conditional_unplaces_the_commands_after_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The `cd` may not have happened, so a later run has no known surface."""
        _app(
            tmp_path,
            {"Makefile": _gate('if [ -n "$$X" ]; then cd sub; fi; cargo clippy')},
        )
        [run] = build_lint_coverage_envelope(tmp_path)["runs"]
        assert (run["is_placed"], run["surface"]) == (False, None)

    def test_a_lane_inside_a_shell_conditional_is_unresolved(
        self, tmp_path: pathlib.Path
    ) -> None:
        """It may be one of several exclusive lanes, not a lane of the whole gate."""
        _app(
            tmp_path,
            {
                "Makefile": _gate(
                    'if [ -n "$$FAST" ]; then cargo clippy; else cargo clippy -F x; fi'
                )
            },
        )
        runs = build_lint_coverage_envelope(tmp_path)["runs"]
        assert runs
        assert all(run["is_resolved"] is False for run in runs)
        assert all("conditional" in run["why"] for run in runs)

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("cargo clippy", ["app"]),
            ("cargo clippy --workspace", ["app", "b"]),
            ("cargo clippy --workspace --exclude b", ["app"]),
            ("cargo clippy -p b", ["b"]),
            ("cargo clippy --workspace --exclude 'ap?'", ["b"]),
            ("cargo clippy -p 'b*'", ["b"]),
            ("cargo clippy --manifest-path b/Cargo.toml", ["b"]),
        ],
    )
    def test_a_run_selects_the_packages_cargo_would_in_a_grown_workspace(
        self, tmp_path: pathlib.Path, command: str, expected: list[str]
    ) -> None:
        """A bare command at a root package selects that package, not the members."""
        _write(
            tmp_path,
            {
                "Cargo.toml": f'{APP}\n[workspace]\nmembers = ["b"]\n',
                "b/Cargo.toml": MANIFEST.format(name="b", extra=""),
                "Makefile": _gate(command),
            },
        )
        [run] = build_lint_coverage_envelope(tmp_path)["runs"]
        assert run["selected"] == expected

    @pytest.mark.parametrize(
        ("root", "expected"),
        [
            ('[workspace]\nmembers = ["a"]\n', "1"),
            ('[workspace]\nmembers = ["a"]\nresolver = "2"\n', "2"),
            ('[workspace]\nmembers = ["a"]\nresolver = "3"\n', "3"),
            (f'{APP}edition = "2021"\n[workspace]\nmembers = ["a"]\n', "2"),
            (f'{APP}edition = "2024"\n[workspace]\nmembers = ["a"]\n', "3"),
            (f'{APP}[workspace]\nmembers = ["a"]\n', "1"),
            (
                (
                    f'{APP}edition.workspace = true\n[workspace]\nmembers = ["a"]\n'
                    '[workspace.package]\nedition = "2021"\n'
                ),
                "2",
            ),
            (
                f'{APP}edition.workspace = true\n[workspace]\nmembers = ["a"]\n',
                "1",
            ),
        ],
    )
    def test_the_feature_resolver_is_read_from_the_root(
        self, tmp_path: pathlib.Path, root: str, expected: str
    ) -> None:
        """`resolver` says so, else a root package's edition, else resolver 1."""
        _write(
            tmp_path,
            {"Cargo.toml": root, "a/Cargo.toml": MANIFEST.format(name="a", extra="")},
        )
        [surface] = build_lint_coverage_envelope(tmp_path)["surfaces"]
        assert surface["resolver"] == expected

    def test_a_virtual_workspace_selects_every_member_and_default_members_narrow_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`default-members` names a bare command's packages; a glob is unresolved."""
        files = {
            "a/Cargo.toml": MANIFEST.format(name="a", extra=""),
            "b/Cargo.toml": MANIFEST.format(name="b", extra=""),
            "Makefile": _gate("cargo clippy"),
        }
        _write(
            tmp_path / "plain",
            {**files, "Cargo.toml": '[workspace]\nmembers = ["a", "b"]\n'},
        )
        _write(
            tmp_path / "narrow",
            {
                **files,
                "Cargo.toml": (
                    '[workspace]\nmembers = ["a", "b"]\ndefault-members = ["a"]\n'
                ),
            },
        )
        _write(
            tmp_path / "glob",
            {
                **files,
                "Cargo.toml": (
                    '[workspace]\nmembers = ["a", "b"]\ndefault-members = ["*"]\n'
                ),
            },
        )
        selected = {
            name: build_lint_coverage_envelope(tmp_path / name)["runs"][0]
            for name in ("plain", "narrow", "glob")
        }
        assert selected["plain"]["selected"] == ["a", "b"]
        assert selected["narrow"]["selected"] == ["a"]
        assert selected["glob"]["is_resolved"] is False

    def test_a_package_outside_every_workspace_is_its_own_surface(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Femtologging's `rust_extension/` stands beside a Python project."""
        _write(
            tmp_path,
            {
                "pyproject.toml": '[project]\nname = "demo"\n',
                "rust_extension/Cargo.toml": MANIFEST.format(name="ext", extra=""),
            },
        )
        [surface] = build_lint_coverage_envelope(tmp_path)["surfaces"]
        assert surface["manifest"] == "rust_extension/Cargo.toml"
        assert surface["has_library"] is False

    def test_a_library_is_a_lib_table_or_a_lib_rs(self, tmp_path: pathlib.Path) -> None:
        """Either marks a library; a binary-only crate has neither."""
        _write(
            tmp_path,
            {"Cargo.toml": MANIFEST.format(name="app", extra="[lib]\nname = 'x'\n")},
        )
        [surface] = build_lint_coverage_envelope(tmp_path)["surfaces"]
        assert surface["has_library"] is True


class TestRuns:
    """Which surface each command targets."""

    def test_a_command_at_the_root_targets_the_root_surface(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No manifest named means the root manifest."""
        _app(tmp_path, {"Makefile": _gate("cargo clippy --all-targets")})
        assert _runs(tmp_path) == [("clippy", "Cargo.toml")]

    def test_manifest_path_and_cd_place_the_command_on_the_extension(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Both spellings reach `rust_extension/`."""
        _write(
            tmp_path,
            {
                "rust_extension/Cargo.toml": MANIFEST.format(name="ext", extra=""),
                "Makefile": _gate(
                    "cargo clippy --manifest-path rust_extension/Cargo.toml",
                    "cd rust_extension && cargo doc --no-deps",
                ),
            },
        )
        assert _runs(tmp_path) == [
            ("clippy", "rust_extension/Cargo.toml"),
            ("doc", "rust_extension/Cargo.toml"),
        ]

    def test_a_command_for_a_manifest_no_surface_holds_is_unplaced(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The policy reports it as indeterminate."""
        _app(
            tmp_path,
            {"Makefile": _gate("cargo clippy --manifest-path elsewhere/Cargo.toml")},
        )
        assert _runs(tmp_path) == [("clippy", None)]

    def test_a_target_the_lint_target_does_not_reach_is_not_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Only recipes `make lint` reaches are the gate."""
        _app(
            tmp_path,
            {
                "Makefile": (
                    ".PHONY: lint docs\nlint:\n\tcargo clippy\n\ndocs:\n\tcargo doc\n"
                )
            },
        )
        assert _runs(tmp_path) == [("clippy", "Cargo.toml")]

    def test_a_workflow_step_is_a_run_and_a_false_branch_is_not(
        self, tmp_path: pathlib.Path
    ) -> None:
        """CI steps count, read as the shell reads them."""
        _app(
            tmp_path,
            {
                ".github/workflows/ci.yml": (
                    f"{WORKFLOW_HEAD}    steps:\n      - run: |\n"
                    "          cargo clippy\n"
                    "          if false; then cargo doc; fi\n"
                )
            },
        )
        assert _runs(tmp_path) == [("clippy", "Cargo.toml")]

    @pytest.mark.parametrize(
        ("head", "step_directory", "expected"),
        [
            ("", "        working-directory: ext\n", [("clippy", "ext/Cargo.toml")]),
            ("", "        working-directory: ./ext/\n", [("clippy", "ext/Cargo.toml")]),
            (
                "defaults:\n  run:\n    working-directory: ext\n",
                "",
                [("clippy", "ext/Cargo.toml")],
            ),
            ("", "        working-directory: .\n", [("clippy", "Cargo.toml")]),
            ("", "        working-directory: ${{ matrix.dir }}\n", [("clippy", None)]),
            ("", "        working-directory: ../out\n", [("clippy", None)]),
            (
                "",
                "        working-directory: ${{ env.BASE }}/..\n",
                [("clippy", None)],
            ),
            ("", "        working-directory: ' ext '\n", [("clippy", None)]),
            ("", "        working-directory: ' . '\n", [("clippy", None)]),
            ("", "        working-directory: link/../ext\n", [("clippy", None)]),
            ("", "        working-directory: ext/..\n", [("clippy", None)]),
            ("", "        working-directory: ''\n", [("clippy", "Cargo.toml")]),
            ("", "        working-directory: /abs\n", [("clippy", None)]),
        ],
    )
    def test_a_working_directory_places_a_workflow_step_on_its_surface(
        self,
        tmp_path: pathlib.Path,
        head: str,
        step_directory: str,
        expected: list[tuple[str, str | None]],
    ) -> None:
        """A plain relative `working-directory` is followed; anything else is not."""
        _app(
            tmp_path,
            {
                "ext/Cargo.toml": MANIFEST.format(name="ext", extra=""),
                ".github/workflows/ci.yml": (
                    f"name: ci\non: push\n{head}jobs:\n  lint:\n"
                    "    runs-on: ubuntu-latest\n    steps:\n"
                    f"      - run: cargo clippy\n{step_directory}"
                ),
            },
        )
        assert _runs(tmp_path) == expected

    @pytest.mark.parametrize(
        ("directory", "command", "expected"),
        [
            ("ext", "cd .. && cargo clippy", "Cargo.toml"),
            ("${{ env.BASE }}", "cd .. && cargo clippy", None),
            ("ext", "cd ${{ env.BASE }}/.. && cargo clippy", None),
            ("ext", "cd $BASE/.. && cargo clippy", None),
        ],
    )
    def test_a_cd_in_a_step_moves_from_its_working_directory(
        self,
        tmp_path: pathlib.Path,
        directory: str,
        command: str,
        expected: str | None,
    ) -> None:
        """`cd ..` from `ext` is the root; an unknown start or target is unknown."""
        _app(
            tmp_path,
            {
                "ext/Cargo.toml": MANIFEST.format(name="ext", extra=""),
                ".github/workflows/ci.yml": (
                    "name: ci\non: push\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
                    f"    steps:\n      - run: {command}\n"
                    f"        working-directory: {directory}\n"
                ),
            },
        )
        assert _runs(tmp_path) == [("clippy", expected)]

    def test_a_cd_up_from_a_symbolic_link_is_unplaced(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`cd ..` from `link` leaves the link target's parent, not the root."""
        _app(
            tmp_path,
            {
                "nested/deep/placeholder": "",
                ".github/workflows/ci.yml": (
                    "name: ci\non: push\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
                    "    steps:\n      - run: cd .. && cargo clippy\n"
                    "        working-directory: link\n"
                ),
            },
        )
        (tmp_path / "link").symlink_to("nested/deep")
        assert _runs(tmp_path) == [("clippy", None)]

    def test_a_climbing_manifest_path_from_a_symbolic_link_is_unplaced(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`--manifest-path ../Cargo.toml` from `link` is not the root manifest."""
        _app(
            tmp_path,
            {
                "nested/deep/placeholder": "",
                "nested/Cargo.toml": MANIFEST.format(name="nested", extra=""),
                ".github/workflows/ci.yml": (
                    "name: ci\non: push\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: cargo clippy --manifest-path ../Cargo.toml\n"
                    "        working-directory: link\n"
                ),
            },
        )
        (tmp_path / "link").symlink_to("nested/deep")
        assert _runs(tmp_path) == [("clippy", None)]

    def test_a_step_that_runs_for_some_matrix_entries_only_is_unresolved(
        self, tmp_path: pathlib.Path
    ) -> None:
        """It may not run on the entry the audit is asked about."""
        _app(
            tmp_path,
            {
                ".github/workflows/ci.yml": (
                    f"{WORKFLOW_HEAD}    strategy:\n      matrix:\n        os: [a, b]\n"
                    "    steps:\n      - if: matrix.os == 'a'\n"
                    "        run: cargo clippy\n"
                )
            },
        )
        runs = build_lint_coverage_envelope(tmp_path)["runs"]
        assert runs, "one run per matrix entry"
        assert all(run["is_resolved"] is False for run in runs)
        assert all("matrix" in run["why"] for run in runs)

    @pytest.mark.parametrize(
        "command",
        [
            "cargo clippy",
            "make lint",
            "./scripts/lint.sh",
            "python tools/gate.py",
            "bash scripts/check",
            "scripts/check",
            "uv run lanes",
        ],
    )
    def test_a_computed_matrix_job_that_may_lint_is_unreadable(
        self, tmp_path: pathlib.Path, command: str
    ) -> None:
        """A computed-matrix job may lint through make or a script."""
        _app(
            tmp_path,
            {
                ".github/workflows/ci.yml": (
                    f"{WORKFLOW_HEAD}    strategy:\n"
                    "      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}\n"
                    f"    steps:\n      - run: {command}\n"
                )
            },
        )
        envelope = build_lint_coverage_envelope(tmp_path)
        assert envelope["runs"] == []
        assert any("matrix is computed" in note for note in envelope["unreadable"])

    def test_a_computed_matrix_job_that_lints_nothing_is_not_noted(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Only a job that may lint is a gap in the audit."""
        _app(
            tmp_path,
            {
                ".github/workflows/ci.yml": (
                    f"{WORKFLOW_HEAD}    strategy:\n"
                    "      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}\n"
                    "    steps:\n      - run: echo hello\n"
                )
            },
        )
        assert build_lint_coverage_envelope(tmp_path)["unreadable"] == []


class TestEnvironment:
    """What the Makefile and the Cargo configuration say about warnings."""

    @pytest.mark.parametrize(
        ("preamble", "expected"),
        [
            # Exported flags are expanded; an assignment that is not exported is
            # not seen by a command.
            (
                "export RUSTFLAGS := -D warnings\nRUSTDOCFLAGS := -D warnings\n",
                {"rustflags": "-D warnings", "rustdocflags": None, "is_resolved": True},
            ),
            # A value built with a Make function is not guessed.
            (
                "export RUSTFLAGS = $(shell echo -D warnings)\n",
                {"rustflags": None, "rustdocflags": None, "is_resolved": False},
            ),
            # Nothing exported.
            ("", {"rustflags": None, "rustdocflags": None, "is_resolved": True}),
        ],
    )
    def test_exported_flags_are_expanded_or_left_unresolved(
        self, tmp_path: pathlib.Path, preamble: str, expected: dict[str, object]
    ) -> None:
        """`export RUSTFLAGS := ...` reaches every command."""
        _app(tmp_path, {"Makefile": _gate("cargo clippy", preamble=preamble)})
        assert build_lint_coverage_envelope(tmp_path)["make_environment"] == expected

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            (
                '[build]\nrustflags = ["-D", "warnings"]\n',
                {"denies_warnings": True, "is_readable": True},
            ),
            (None, {"denies_warnings": False, "is_readable": True}),
        ],
    )
    def test_a_cargo_config_that_denies_warnings_or_is_absent(
        self,
        tmp_path: pathlib.Path,
        config: str | None,
        expected: dict[str, object],
    ) -> None:
        """`build.rustflags` with `-D warnings`; an absent file says nothing."""
        files = {".cargo/config.toml": config} if config is not None else {}
        _app(tmp_path, files)
        assert build_lint_coverage_envelope(tmp_path)["cargo_config"] == expected
