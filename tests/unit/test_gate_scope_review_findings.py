"""Regression cases for the gate-workspace-coverage reader's early review rounds."""

from __future__ import annotations

import subprocess
import typing as typ

import pytest

from tests.unit.gate_scope_support import (
    build as _build,
)
from tests.unit.gate_scope_support import (
    workspace_files as _workspace_files,
)

if typ.TYPE_CHECKING:
    import pathlib


class TestThirdReview:
    """Behaviours a third review showed to be missing."""

    def test_a_probe_branch_that_one_reading_lacks_is_unplaced(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`test -p b` against `nextest run --workspace` is decided by the probe."""
        makefile = (
            ".PHONY: test\ntest:\n\tcargo $(if $(shell cargo nextest --version "
            "2>/dev/null),test -p b,nextest run --workspace)\n"
        )
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
        resolved = {i["tool"]: i["resolved"] for i in found}
        # `test -p b` covers only `b` when it runs, so the probe decides it;
        # `nextest run --workspace` covers everything whenever it runs
        assert resolved == {"test": False, "nextest": True}

    def test_a_cargo_subcommand_from_a_shell_variable_is_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`cargo "$RUNNER" -p b` may be `cargo test -p b`."""
        makefile = ".PHONY: test\ntest:\n\tcargo $$RUNNER -p b\n"
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["invocations"] == []
        assert envelope["unreadable"]

    def test_a_computed_matrix_running_cargo_is_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`cargo ${{ matrix.subcommand }}` in a computed matrix may be a gate tool."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    strategy:\n      matrix: ${{ fromJson(needs.plan.outputs.m) }}\n"
            "    steps:\n      - run: cargo ${{ matrix.subcommand }} -p b\n"
        )
        envelope = _build(
            tmp_path, _workspace_files(extra={".github/workflows/ci.yml": text})
        )
        assert any("matrix is computed" in entry for entry in envelope["unreadable"])

    @pytest.mark.parametrize(
        ("line", "tools"),
        [
            ("if false; then cargo test --workspace; fi", []),
            ("if false; then cargo test; else cargo clippy; fi", ["clippy"]),
            ("if true; then cargo test --workspace; fi", ["test"]),
            ("if cargo test --workspace; then echo ok; fi", ["test"]),
        ],
    )
    def test_a_known_false_shell_condition_skips_its_branch(
        self, tmp_path: pathlib.Path, line: str, tools: list[str]
    ) -> None:
        """Only the constant false is known; the `else` branch of it runs."""
        makefile = f".PHONY: test\ntest:\n\t{line}\n"
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
        assert [i["tool"] for i in found] == tools


class TestSecondReview:
    """Behaviours a second review showed to be missing."""

    def test_a_job_that_never_runs_is_not_a_command(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`if: false` on a job switches off every step in it."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    if: ${{ false }}\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: cargo test --workspace\n"
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        assert _build(tmp_path, files)["invocations"] == []

    def test_a_step_conditional_on_the_matrix_is_unplaced(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`if: matrix.package == 'app'` runs for one combination, not both."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    strategy:\n      matrix:\n        package: [app, b]\n"
            "    steps:\n      - if: matrix.package == 'app'\n"
            "        run: cargo test -p ${{ matrix.package }}\n"
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        found = _build(tmp_path, files)["invocations"]
        assert found
        assert not any(i["resolved"] for i in found)

    def test_a_prerequisite_a_make_condition_adds_is_unplaced(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`lint: full` under `ifeq` leaves `full`'s commands conditional."""
        makefile = (
            ".PHONY: lint full\n\nifeq ($(FULL),1)\nlint: full\nendif\n"
            "lint:\n\t@echo lint\n\nfull:\n\tcargo clippy --workspace\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert [(i["tool"], i["resolved"]) for i in envelope["invocations"]] == [
            ("clippy", False)
        ]

    def test_a_prerequisite_also_reached_unconditionally_is_placed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A target some unconditional path reaches runs whatever the condition."""
        makefile = (
            ".PHONY: lint full\n\nifeq ($(FULL),1)\nlint: full\nendif\n"
            "lint: full\n\nfull:\n\tcargo clippy --workspace\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert [i["resolved"] for i in envelope["invocations"]] == [True]

    def test_probe_readings_that_select_differently_are_alternatives(
        self, tmp_path: pathlib.Path
    ) -> None:
        """One run takes one branch, so `-p app` and `-p b` are not a union."""
        makefile = (
            ".PHONY: test\ntest:\n\tcargo test $(if $(shell cargo nextest "
            "--version 2>/dev/null),-p app,-p b)\n"
        )
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
        assert len(found) == 2
        assert not any(i["resolved"] for i in found)

    def test_probe_readings_that_select_alike_stay_placed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A probe that only changes the runner leaves each tool's selection alone."""
        makefile = (
            ".PHONY: test\ntest:\n\tcargo $(if $(shell cargo nextest --version "
            "2>/dev/null),nextest run,test) --workspace\n"
        )
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
        assert {i["tool"] for i in found} == {"nextest", "test"}
        assert all(i["resolved"] for i in found)

    def test_a_shell_variable_command_head_is_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`$RUNNER test -p b` may be cargo, so it is reported, not dropped."""
        makefile = ".PHONY: test\ntest:\n\tcargo test -p app\n\t$$RUNNER test -p b\n"
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert [i["tool"] for i in envelope["invocations"]] == ["test"]
        assert envelope["unreadable"]


class TestReviewFindings:
    """Behaviours a review of the first version showed to be missing."""

    def test_a_step_that_never_runs_is_not_a_command(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`if: false` switches the step off; any other condition is kept."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - if: ${{ false }}\n"
            "        run: cargo test --workspace\n"
            "      - if: github.event_name == 'push'\n        run: cargo clippy\n"
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        found = _build(tmp_path, files)["invocations"]
        assert [i["tool"] for i in found] == ["clippy"]

    def test_a_computed_matrix_axis_leaves_the_matrix_unexpanded(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An axis from an expression cannot be dropped: `exclude` may match it."""
        matrix = (
            "    strategy:\n      matrix:\n"
            "        os: ${{ fromJson(needs.plan.outputs.os) }}\n"
            "        exclude:\n          - os: mac\n"
        )
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            f"{matrix}    steps:\n      - run: cargo test --workspace\n"
        )
        envelope = _build(
            tmp_path, _workspace_files(extra={".github/workflows/ci.yml": text})
        )
        assert envelope["invocations"] == []
        assert any("matrix is computed" in entry for entry in envelope["unreadable"])

    def test_a_conditional_recipe_is_unplaced(self, tmp_path: pathlib.Path) -> None:
        """A recipe under `ifeq` may or may not run, so it is not read as run."""
        makefile = (
            "ifeq ($(MODE),fast)\nlint:\n\tcargo clippy\n"
            "else\nlint:\n\tcargo clippy --workspace\nendif\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["invocations"]
        assert not any(i["resolved"] for i in envelope["invocations"])

    def test_oneshell_carries_a_cd_to_the_recipe_lines_after_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Under `.ONESHELL` the recipe is one shell; without it each line is."""
        recipe = ".PHONY: test\ntest:\n\tcd crates/b\n\tcargo test --workspace\n"
        plain = _build(tmp_path, _workspace_files(makefile=recipe))
        assert [i["changes_directory"] for i in plain["invocations"]] == [False]
        one_shell = _build(
            tmp_path / "oneshell",
            _workspace_files(makefile=f".ONESHELL:\n{recipe}"),
        )
        assert [i["changes_directory"] for i in one_shell["invocations"]] == [True]

    def test_a_known_command_does_not_hide_an_unreadable_one_beside_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Each command on a line is judged alone."""
        makefile = (
            ".PHONY: lint\nlint:\n\tcargo clippy --workspace && $(RUNNER) test -p app\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert [i["tool"] for i in envelope["invocations"]] == ["clippy"]
        assert envelope["unreadable"]

    def test_two_commands_on_one_line_are_two_invocations(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`a && b` on one line records both, not only the first."""
        makefile = (
            ".PHONY: lint\nlint:\n\tcargo clippy --workspace && cargo test -p app\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert [i["tool"] for i in envelope["invocations"]] == ["clippy", "test"]

    def test_the_repository_name_is_the_origin_slug(
        self, tmp_path: pathlib.Path
    ) -> None:
        """An exemption names `owner/name`, so the envelope carries the origin's."""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:leynos/example.git"],  # noqa: S607
            cwd=tmp_path,
            check=True,
        )
        envelope = _build(tmp_path, _workspace_files())
        assert envelope["repository"]["name"] == "leynos/example"

    def test_a_working_directory_moves_every_command_of_the_step(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`working-directory` is another directory, so the root is not covered."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: cargo test --workspace\n"
            "        working-directory: nested\n"
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        (found,) = _build(tmp_path, files)["invocations"]
        assert found["changes_directory"]

    def test_a_default_working_directory_moves_the_job(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`defaults.run.working-directory` applies to steps that set none."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    defaults:\n      run:\n        working-directory: nested\n"
            "    steps:\n      - run: cargo test --workspace\n"
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        (found,) = _build(tmp_path, files)["invocations"]
        assert found["changes_directory"]

    def test_a_cd_on_an_earlier_line_moves_the_later_lines(
        self, tmp_path: pathlib.Path
    ) -> None:
        """One step is one shell: `cd nested` moves what follows."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: |\n          cd nested\n"
            "          cargo test --workspace\n"
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        (found,) = _build(tmp_path, files)["invocations"]
        assert found["changes_directory"]

    @pytest.mark.parametrize(
        ("matrix", "expected"),
        [
            # exclude removes a combination before include runs
            (
                (
                    'flags: ["--workspace", "-p app"]\n        exclude:\n'
                    "          - flags: -p app\n"
                ),
                ["--workspace"],
            ),
            # a later include overwrites a value an earlier one added
            (
                (
                    "os: [linux]\n        include:\n          - flags: -p app\n"
                    "          - flags: --workspace\n"
                ),
                ["--workspace"],
            ),
            # an include that contradicts an original value is its own job
            (
                (
                    "os: [linux]\n        include:\n          - os: mac\n"
                    "            flags: --workspace\n"
                ),
                ["", "--workspace"],
            ),
            # no axes: each include is a combination of its own
            (
                "include:\n          - flags: --workspace\n          - flags: -p app\n",
                ["--workspace", "-p app"],
            ),
        ],
    )
    def test_matrix_exclude_and_include_follow_github(
        self, tmp_path: pathlib.Path, matrix: str, expected: list[str]
    ) -> None:
        """Combinations are the product, less `exclude`, with `include` applied."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            f"    strategy:\n      matrix:\n        {matrix}"
            "    steps:\n      - run: cargo test ${{ matrix.flags }}\n"
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        envelope = _build(tmp_path, files)
        commands = sorted(
            i["command"].removeprefix("cargo test").strip()
            for i in envelope["invocations"]
        )
        # an unfilled `matrix.flags` is the unresolved mark, shown as nothing
        assert sorted(c.replace("\x01", "") for c in commands) == sorted(expected)
