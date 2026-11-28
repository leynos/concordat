"""Unit tests for reading one simple command as a lint-coverage run."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.lint_coverage_commands import parse_command, script_run

if typ.TYPE_CHECKING:
    from concordat.rules.lint_coverage_commands import Run


def _run(command: str, directory: str | None = "") -> Run:
    """Parse *command*, split on spaces, as one simple command at the root."""
    run = parse_command(command.split(), "Makefile:1 (lint)", directory)
    assert run is not None, command
    return run


class TestTools:
    """Which commands are lint runs."""

    @pytest.mark.parametrize(
        ("command", "tool"),
        [
            ("cargo clippy --workspace", "clippy"),
            ("cargo +nightly clippy --all-targets", "clippy"),
            ("@cargo doc --no-deps", "doc"),
            ("whitaker --all -- --workspace", "whitaker"),
            ("cargo dylint --all", "whitaker"),
            ("RUSTFLAGS=x cargo clippy", "clippy"),
            ("env FOO=1 cargo clippy", "clippy"),
        ],
    )
    def test_a_lint_command_is_recognised(self, command: str, tool: str) -> None:
        """Clippy, `cargo doc`, Whitaker and `cargo dylint`."""
        assert _run(command)["tool"] == tool

    @pytest.mark.parametrize(
        "command",
        ["cargo test", "cargo build", "echo cargo clippy", "whitaker", "cargo"],
    )
    def test_anything_else_is_not(self, command: str) -> None:
        """A word in an `echo`, a bare `whitaker` and other subcommands run none."""
        assert parse_command(command.split(), "x", "") is None


class TestFlags:
    """What a command checks."""

    def test_all_targets_by_flag_or_by_the_full_list(self) -> None:
        """`--all-targets`, or every one of lib, bins, tests, benches, examples."""
        assert _run("cargo clippy --all-targets")["all_targets"] is True
        full = "cargo clippy --lib --bins --tests --benches --examples"
        assert _run(full)["all_targets"] is True
        assert _run("cargo clippy --lib --tests")["all_targets"] is False
        assert _run("cargo clippy")["all_targets"] is False

    def test_features_are_split_and_qualified_names_keep_their_package(self) -> None:
        """`--features a,b`, `-F p/c`, `--features=d` and `--no-default-features`."""
        run = _run(
            "cargo clippy --features a,b -F pkg/c --features=d --no-default-features"
        )
        assert run["features"] == ["a", "b", "d", "pkg/c"]
        assert run["no_default_features"] is True
        assert run["all_features"] is False
        assert _run("cargo clippy --all-features")["all_features"] is True

    def test_the_selection_flags(self) -> None:
        """`--workspace`, `--all` and `-p` are recorded for `cargo doc`."""
        run = _run("cargo doc --no-deps -p a --package b")
        assert (run["workspace"], run["packages"], run["no_deps"]) == (
            False,
            ["a", "b"],
            True,
        )
        assert _run("cargo doc --all")["workspace"] is True

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("cargo doc --workspace --no-deps", False),
            ("cargo doc --workspace --exclude b --no-deps", True),
            ("cargo doc --workspace --exclude=b --no-deps", True),
            ("cargo doc --workspace --bins --no-deps", True),
            ("cargo doc --workspace --example demo --no-deps", True),
            ("cargo doc --workspace --lib --no-deps", False),
        ],
    )
    def test_a_command_that_leaves_packages_or_targets_out_narrows(
        self, command: str, *, expected: bool
    ) -> None:
        """`--exclude` and `--bins` mean the command does not cover the surface."""
        assert _run(command)["narrows"] is expected

    def test_flags_after_the_separator_do_not_count_as_cargos(self) -> None:
        """`-- --all-targets` goes to Clippy's driver, not to Cargo."""
        assert _run("cargo clippy -- --all-targets")["all_targets"] is False

    def test_whitakers_own_options_are_not_cargos(self) -> None:
        """`--all` and `--lib NAME` select lint libraries; the rest is Cargo's."""
        run = _run("whitaker --all --lib suite -- --all-targets --all-features")
        assert (run["all_targets"], run["all_features"], run["workspace"]) == (
            True,
            True,
            False,
        )


class TestWarnings:
    """What the command line says about denying warnings."""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("cargo clippy -- -D warnings", True),
            ("cargo clippy -- -Dwarnings", True),
            ("cargo clippy -- --deny warnings", True),
            ("cargo clippy -- -D warnings -W warnings", False),
            ("cargo clippy -- -D warnings --allow warnings", False),
            ("cargo clippy -- -W warnings -D warnings", True),
            ("cargo clippy", None),
            ("cargo clippy -- -A clippy::all", None),
        ],
    )
    def test_clippy_reads_the_flags_after_the_separator(
        self, command: str, *, expected: bool | None
    ) -> None:
        """A space inside an assignment is one shell word in the real reader."""
        assert _run(command)["deny_warnings"] is expected

    def test_an_assignment_word_is_read_whole(self) -> None:
        """`RUSTFLAGS="-D warnings"` is one word once the shell has lexed it."""
        run = parse_command(
            ["RUSTFLAGS=-D warnings", "cargo", "clippy"], "Makefile:1 (lint)", ""
        )
        assert run is not None
        assert run["deny_warnings"] is True

    def test_an_assignment_without_the_deny_says_not(self) -> None:
        """The command assigned the variable and did not deny warnings."""
        run = parse_command(
            ["RUSTFLAGS=-C opt-level=0", "cargo", "clippy"], "Makefile:1 (lint)", ""
        )
        assert run is not None
        assert run["deny_warnings"] is False

    def test_doc_reads_rustdocflags_not_rustflags(self) -> None:
        """`cargo doc` runs rustdoc, which reads RUSTDOCFLAGS."""
        denied = parse_command(["RUSTDOCFLAGS=-D warnings", "cargo", "doc"], "x", "")
        other = parse_command(["RUSTFLAGS=-D warnings", "cargo", "doc"], "x", "")
        assert denied is not None
        assert other is not None
        assert denied["deny_warnings"] is True
        assert other["deny_warnings"] is None


class TestPlacement:
    """Which manifest the command targets, and whether that is known."""

    def test_a_command_at_the_root_names_no_manifest(self) -> None:
        """The policy reads it as the root manifest."""
        run = _run("cargo clippy")
        assert (run["manifest"], run["is_placed"], run["is_resolved"]) == (
            None,
            True,
            True,
        )

    def test_manifest_path_names_the_surface(self) -> None:
        """`--manifest-path` is relative to the directory the command runs in."""
        run = _run("cargo clippy --manifest-path rust_extension/Cargo.toml")
        assert run["manifest"] == "rust_extension/Cargo.toml"
        nested = _run("cargo clippy --manifest-path Cargo.toml", directory="sub")
        assert nested["manifest"] == "sub/Cargo.toml"

    def test_a_manifest_path_that_climbs_from_a_directory_is_unplaced(self) -> None:
        """`..` may step out of a link, so only the root's own `..` is followed."""
        climbing = _run("cargo clippy --manifest-path ../Cargo.toml", directory="link")
        assert (climbing["is_placed"], climbing["manifest"]) == (False, None)
        at_root = _run("cargo clippy --manifest-path a/../b/Cargo.toml", directory="")
        assert at_root["manifest"] == "b/Cargo.toml"

    def test_a_directory_moved_to_names_its_manifest(self) -> None:
        """After `cd rust_extension`, the command targets that crate."""
        assert _run("cargo clippy", directory="rust_extension")["manifest"] == (
            "rust_extension/Cargo.toml"
        )

    def test_a_directory_the_audit_could_not_follow_is_unplaced(self) -> None:
        """A computed `cd` leaves the surface open."""
        run = _run("cargo clippy", directory=None)
        assert (run["is_placed"], run["is_resolved"]) == (False, False)

    def test_an_unresolved_word_among_the_arguments_is_unresolved(self) -> None:
        """It may stand for a flag, so the run is not judged."""
        run = parse_command(["cargo", "clippy", "$(FLAGS)"], "x", "")
        assert run is not None
        assert (run["is_placed"], run["is_resolved"]) == (True, False)
        assert "could not resolve" in run["why"]


class TestScripts:
    """A script that builds the lanes."""

    @staticmethod
    def _tools(_path: str) -> set[str]:
        """Pretend every script runs Clippy."""
        return {"clippy"}

    @pytest.mark.parametrize(
        "command",
        [
            "python scripts/lanes.py",
            "uv run --script scripts/lanes.py",
            "uv run --python 3.13 scripts/lanes.py",
            "bash scripts/lanes.sh",
            "./scripts/lanes.sh",
            "INPUT_LANES=a_b python scripts/lanes.py",
        ],
    )
    def test_a_command_that_runs_a_script_is_an_unresolved_run(
        self, command: str
    ) -> None:
        """The script is not read; its lane inputs are carried."""
        runs = script_run(command.split(), "Makefile:2 (lint)", "", self._tools)
        assert [(r["tool"], r["is_resolved"], r["is_placed"]) for r in runs] == [
            ("clippy", False, False)
        ]

    @pytest.mark.parametrize(
        "command",
        [
            "uv tool run ruff format scripts/lanes.py",
            "python -m pytest scripts/test_lanes.py",
            "ruff check scripts/lanes.py",
            "cat scripts/lanes.py",
        ],
    )
    def test_a_tool_that_only_names_a_script_runs_none_of_its_lanes(
        self, command: str
    ) -> None:
        """Formatting, linting and testing a script are not running it."""
        assert script_run(command.split(), "x", "", self._tools) == []

    def test_the_lane_inputs_are_carried(self) -> None:
        """Only inputs with LANE in the name."""
        words = ["INPUT_LANES=none all", "OTHER=x", "python", "scripts/lanes.py"]
        [run] = script_run(words, "x", "", self._tools)
        assert run["lanes"] == ["INPUT_LANES=none all"]

    def test_a_script_naming_no_tool_stands_for_nothing(self) -> None:
        """A script whose text runs neither Clippy nor Whitaker is not a lint run."""
        assert script_run(["python", "x.py"], "x", "", lambda _p: set()) == []
