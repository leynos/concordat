"""Unit tests for the `policy-input/gate-lint-coverage` envelope builder.

The Rego suite proves what the policy makes of each envelope; these tests prove
what the builder records: the Cargo surfaces and what they declare, which
surface each lint command targets, what the Makefile exports, and what it refuses
to guess.
"""

from __future__ import annotations

import typing as typ

from concordat.rules.lint_coverage_envelope import (
    ENVELOPE_KIND,
    build_lint_coverage_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

MANIFEST: typ.Final = '[package]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


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
        """Features are the union across the members, `default` apart."""
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
        assert surface["features"] == ["x", "y"]
        assert (surface["has_default"], surface["has_library"]) == (True, True)

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

    def _surface_of_runs(self, checkout: pathlib.Path) -> list[tuple[str, str | None]]:
        """Return `(tool, surface)` for each run of the checkout."""
        runs = build_lint_coverage_envelope(checkout)["runs"]
        return [(r["tool"], r["surface"]) for r in runs]

    def test_a_command_at_the_root_targets_the_root_surface(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No manifest named means the root manifest."""
        _write(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "Makefile": _gate("cargo clippy --all-targets"),
            },
        )
        assert self._surface_of_runs(tmp_path) == [("clippy", "Cargo.toml")]

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
        assert self._surface_of_runs(tmp_path) == [
            ("clippy", "rust_extension/Cargo.toml"),
            ("doc", "rust_extension/Cargo.toml"),
        ]

    def test_a_command_for_a_manifest_no_surface_holds_is_unplaced(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The policy reports it as indeterminate."""
        _write(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "Makefile": _gate("cargo clippy --manifest-path elsewhere/Cargo.toml"),
            },
        )
        assert self._surface_of_runs(tmp_path) == [("clippy", None)]

    def test_a_target_the_lint_target_does_not_reach_is_not_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Only recipes `make lint` reaches are the gate."""
        _write(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "Makefile": (
                    ".PHONY: lint docs\nlint:\n\tcargo clippy\n\ndocs:\n\tcargo doc\n"
                ),
            },
        )
        assert self._surface_of_runs(tmp_path) == [("clippy", "Cargo.toml")]

    def test_a_workflow_step_is_a_run_and_a_false_branch_is_not(
        self, tmp_path: pathlib.Path
    ) -> None:
        """CI steps count, read as the shell reads them."""
        _write(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                ".github/workflows/ci.yml": (
                    "name: ci\non: push\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
                    "    steps:\n      - run: |\n          cargo clippy\n"
                    "          if false; then cargo doc; fi\n"
                ),
            },
        )
        assert self._surface_of_runs(tmp_path) == [("clippy", "Cargo.toml")]

    def test_a_step_that_runs_for_some_matrix_entries_only_is_unresolved(
        self, tmp_path: pathlib.Path
    ) -> None:
        """It may not run on the entry the audit is asked about."""
        _write(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                ".github/workflows/ci.yml": (
                    "name: ci\non: push\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
                    "    strategy:\n      matrix:\n        os: [a, b]\n"
                    "    steps:\n      - if: matrix.os == 'a'\n"
                    "        run: cargo clippy\n"
                ),
            },
        )
        runs = build_lint_coverage_envelope(tmp_path)["runs"]
        assert runs, "one run per matrix entry"
        assert all(run["is_resolved"] is False for run in runs)
        assert all("matrix" in run["why"] for run in runs)


class TestEnvironment:
    """What the Makefile and the Cargo configuration say about warnings."""

    def test_exported_flags_are_expanded(self, tmp_path: pathlib.Path) -> None:
        """`export RUSTFLAGS := ...` reaches every command."""
        _write(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "Makefile": _gate(
                    "cargo clippy",
                    preamble=(
                        "export RUSTFLAGS := -D warnings\nRUSTDOCFLAGS := -D warnings\n"
                    ),
                ),
            },
        )
        environment = build_lint_coverage_envelope(tmp_path)["make_environment"]
        # RUSTDOCFLAGS is assigned but not exported, so a command does not see it.
        assert environment["rustflags"] == "-D warnings"
        assert environment["rustdocflags"] is None
        assert environment["is_resolved"] is True

    def test_an_exported_value_the_audit_cannot_expand_is_unresolved(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A `$(shell ...)` value is not guessed."""
        _write(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                "Makefile": _gate(
                    "cargo clippy",
                    preamble="export RUSTFLAGS = $(shell echo -D warnings)\n",
                ),
            },
        )
        assert (
            build_lint_coverage_envelope(tmp_path)["make_environment"]["is_resolved"]
            is False
        )

    def test_a_cargo_config_that_denies_warnings_in_every_source(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`build.rustflags` with `-D warnings`."""
        _write(
            tmp_path,
            {
                "Cargo.toml": MANIFEST.format(name="app", extra=""),
                ".cargo/config.toml": '[build]\nrustflags = ["-D", "warnings"]\n',
            },
        )
        assert build_lint_coverage_envelope(tmp_path)["cargo_config"] == {
            "denies_warnings": True,
            "is_readable": True,
        }

    def test_no_cargo_config_denies_nothing(self, tmp_path: pathlib.Path) -> None:
        """An absent configuration is readable and says nothing."""
        _write(tmp_path, {"Cargo.toml": MANIFEST.format(name="app", extra="")})
        assert build_lint_coverage_envelope(tmp_path)["cargo_config"] == {
            "denies_warnings": False,
            "is_readable": True,
        }
