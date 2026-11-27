"""Regression cases for the gate-workspace-coverage reader's shell and probe handling.

Each class pins behaviours a review of the reader showed to be missing: probe
alternatives, shell conditions and shell state across lines.
"""

from __future__ import annotations

import typing as typ

import pytest

from tests.unit.gate_scope_support import (
    build as _build,
)
from tests.unit.gate_scope_support import (
    makefile_invocations as _makefile_invocations,
)
from tests.unit.gate_scope_support import (
    read as _read,
)
from tests.unit.gate_scope_support import (
    step_workflow as _step_workflow,
)
from tests.unit.gate_scope_support import (
    workspace_files as _workspace_files,
)

if typ.TYPE_CHECKING:
    import pathlib


class TestFourthReview:
    """Behaviours a fourth review showed to be missing."""

    def test_probe_tools_that_exclude_each_other_are_left_to_the_probe(
        self, tmp_path: pathlib.Path
    ) -> None:
        """One run takes `test -p b` or `nextest run -p b`, never both."""
        makefile = (
            ".PHONY: test\ntest:\n\tcargo test -p app\n\tcargo nextest run -p app\n"
            "\tcargo $(if $(shell cargo nextest --version 2>/dev/null),"
            "test -p b,nextest run -p b)\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        assert {i["tool"] for i in found} == {"test", "nextest"}
        assert not any(i["resolved"] for i in found)

    def test_a_multiline_if_false_in_one_step_skips_its_body(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The state of an open `if false` carries across the step's lines."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: |\n          cargo test -p app\n"
            "          if false; then\n            cargo test --workspace\n"
            "          fi\n"
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        found = _build(tmp_path, files)["invocations"]
        assert [i["workspace_flag"] for i in found] == [False]

    def test_a_subcommand_variable_after_cargos_global_options_is_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`cargo +nightly "$RUNNER" -p b` hides its subcommand past `+nightly`."""
        makefile = '.PHONY: test\ntest:\n\tcargo +nightly "$$RUNNER" -p b\n'
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["invocations"] == []
        assert envelope["unreadable"]

    def test_a_computed_matrix_with_a_variable_command_head_is_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`${{ matrix.runner }} test -p b` may be `cargo test -p b`."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    strategy:\n      matrix: ${{ fromJson(needs.plan.outputs.m) }}\n"
            "    steps:\n      - run: ${{ matrix.runner }} test -p b\n"
        )
        envelope = _build(
            tmp_path, _workspace_files(extra={".github/workflows/ci.yml": text})
        )
        assert any("matrix is computed" in entry for entry in envelope["unreadable"])


class TestFifthReview:
    """Behaviours a fifth review showed to be missing."""

    def test_a_whole_workspace_probe_is_not_safe_beside_a_narrow_ci_command(
        self, tmp_path: pathlib.Path
    ) -> None:
        """CI's narrow `cargo test` may be all that runs if the probe picks nextest."""
        makefile = (
            ".PHONY: test\ntest:\n\tcargo $(if $(shell cargo nextest --version "
            "2>/dev/null),test --workspace,nextest run --workspace)\n"
        )
        files = _workspace_files(
            makefile=makefile, extra=_step_workflow("cargo test -p app")
        )
        found = _build(tmp_path, files)["invocations"]
        probe_branch = [i for i in found if i["tool"] == "test" and i["workspace_flag"]]
        assert probe_branch
        assert not any(i["resolved"] for i in probe_branch)

    def test_the_else_of_an_if_true_does_not_run(self, tmp_path: pathlib.Path) -> None:
        """Only the `then` of a constant-true condition runs."""
        script = (
            "if true; then\n  cargo test -p app\nelse\n  cargo test --workspace\nfi"
        )
        files = _workspace_files(extra=_step_workflow(script))
        found = _build(tmp_path, files)["invocations"]
        assert [i["workspace_flag"] for i in found] == [False]

    def test_an_elif_after_an_if_false_is_judged_on_its_own_condition(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`elif true` runs, so the `else` after it does not."""
        script = (
            "if false; then\n  :\nelif true; then\n  cargo test --workspace\n"
            "else\n  cargo test -p app\nfi"
        )
        files = _workspace_files(extra=_step_workflow(script))
        found = _build(tmp_path, files)["invocations"]
        assert [i["workspace_flag"] for i in found] == [True]


class TestSixthReview:
    """Behaviours a sixth review showed to be missing."""

    def test_the_branches_of_an_unknown_condition_are_alternatives(
        self, tmp_path: pathlib.Path
    ) -> None:
        """One run takes `-p a` or `-p b`, so neither is coverage on its own."""
        body = (
            '          if [ "$USE_A" = 1 ]; then\n            cargo test -p app\n'
            "          else\n            cargo test -p b\n          fi\n"
        )
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: |\n" + body
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        found = _build(tmp_path, files)["invocations"]
        assert len(found) == 2
        assert not any(i["resolved"] for i in found)

    def test_an_unknown_condition_without_an_else_keeps_its_command_placed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A lone `then` is taken to hold on some run, as a step's `if` is."""
        body = (
            '          if [ -n "$X" ]; then\n            cargo test -p app\n'
            "          fi\n"
        )
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: |\n" + body
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        found = _build(tmp_path, files)["invocations"]
        assert [i["resolved"] for i in found] == [True]

    def test_a_cd_in_one_probe_reading_moves_a_later_line_with_one_reading(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Under `.ONESHELL` the probe may have run `cd nested`."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n"
            "\t$(if $(shell cargo nextest --version 2>/dev/null),cd .,cd nested)\n"
            "\tcargo test --workspace\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        assert [i["changes_directory"] for i in found] == [True]


class TestSeventhReview:
    """Behaviours an eighth review showed to be missing."""

    def test_an_unknown_elif_after_an_if_false_starts_a_fork(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The `elif` and its `else` are alternatives."""
        script = (
            "if false; then\n  :\nelif test -f flag; then\n  cargo test -p app\n"
            "else\n  cargo test -p b\nfi"
        )
        files = _workspace_files(extra=_step_workflow(script))
        found = _build(tmp_path, files)["invocations"]
        assert len(found) == 2
        assert not any(i["resolved"] for i in found)

    def test_a_later_else_marks_an_earlier_oneshell_branch(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Under `.ONESHELL` the fork is known only at the `else`."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n\tif test -f flag; then\n"
            "\t  cargo test -p app\n\telse\n\t  :\n\tfi\n\tcargo test -p b\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        by_packages = {tuple(i["packages"]): i["resolved"] for i in found}
        assert by_packages == {("app",): False, ("b",): True}

    def test_an_open_if_false_survives_a_change_in_probe_readings(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The probe's two readings sit inside a branch that does not run."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n\tif false; then\n"
            "\t  cargo $(if $(shell cargo nextest --version 2>/dev/null),"
            "test -p app,nextest run -p app)\n\tfi\n\tcargo test --workspace\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        assert [i["workspace_flag"] for i in found] == [True]


class TestEighthReview:
    """Behaviours a ninth review showed to be missing."""

    def test_a_probe_in_an_if_condition_makes_its_body_indeterminate(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The probe may make the condition `false`, so `-p app` may not run."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n"
            "\tif $(if $(shell cargo nextest --version 2>/dev/null),true,false);"
            " then\n\t  cargo test -p app\n\tfi\n\tcargo test -p b\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        by_packages = {tuple(i["packages"]): i["resolved"] for i in found}
        assert by_packages == {("app",): False, ("b",): True}

    def test_an_unknown_elif_with_nothing_after_it_is_not_a_fork(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`if false; elif test; then X; fi` runs X on some run and has no rival."""
        body = (
            "          if false; then\n            :\n"
            "          elif test -f flag; then\n            cargo test --workspace\n"
            "          fi\n"
        )
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: |\n" + body
        )
        files = _workspace_files(extra={".github/workflows/ci.yml": text})
        found = _build(tmp_path, files)["invocations"]
        assert [i["resolved"] for i in found] == [True]


class TestNinthReview:
    """Behaviours a tenth review showed to be missing."""

    def test_independent_probes_with_equal_reading_counts_are_not_paired(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The first probe's `cd nested` may pair with the second's `cargo test`."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n"
            "\t$(if $(shell cargo nextest --version 2>/dev/null),cd .,cd nested)\n"
            "\t$(if $(shell rustc --version 2>/dev/null),cargo test --workspace,"
            "echo skipped)\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        assert [i["changes_directory"] for i in found] == [True]

    def test_a_known_false_outer_branch_stays_skipped_beside_a_probe_condition(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`if false` around a probe-decided `if` runs nothing at all."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n\tif false; then\n"
            "\t  if $(if $(shell cargo nextest --version 2>/dev/null),true,false);"
            " then\n\t    cargo test -p app\n\t  fi\n\tfi\n"
            "\tcargo test --workspace\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        assert [i["workspace_flag"] for i in found] == [True]
        assert [i["resolved"] for i in found] == [True]


class TestTenthReview:
    """Behaviours an eleventh review showed to be missing."""

    def test_a_branch_no_probe_reading_can_run_stays_skipped(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Both readings leave the `elif false` body inactive."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n"
            "\tif $(if $(shell cargo nextest --version 2>/dev/null),true,false);"
            " then :; elif false; then\n\t  cargo test -p app\n\tfi\n"
            "\tcargo test --workspace\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        assert [i["workspace_flag"] for i in found] == [True]
        assert [i["resolved"] for i in found] == [True]


class TestEleventhReview:
    """Behaviours a twelfth review showed to be missing."""

    def test_an_else_stays_reachable_when_readings_disagree_on_taken(
        self, tmp_path: pathlib.Path
    ) -> None:
        """If the probe is false, the `else` runs; its command is indeterminate."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n"
            "\tif $(if $(shell cargo nextest --version 2>/dev/null),true,false);"
            " then :; elif false; then\n\t  :\n\telse\n"
            "\t  cargo test --workspace\n\tfi\n\tcargo test -p app\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        by_flag = {i["workspace_flag"]: i["resolved"] for i in found}
        assert by_flag == {True: False, False: True}


class TestTwelfthReview:
    """Behaviours a thirteenth review showed to be missing."""

    def test_a_variable_command_in_a_skipped_branch_is_not_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`if false` never runs `cargo $$RUNNER`, so it hides nothing."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n\tif false; then\n"
            "\t  cargo $$RUNNER -p b\n\tfi\n\tcargo test --workspace\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["unreadable"] == []
        assert [i["workspace_flag"] for i in envelope["invocations"]] == [True]


class TestThirteenthReview:
    """Behaviours a fourteenth review showed to be missing."""

    @staticmethod
    def _oneshell(body: str) -> str:
        """Return a `.ONESHELL` Makefile whose `test` recipe is *body*."""
        lines = "".join(f"\t{line}\n" for line in body.splitlines())
        return f".ONESHELL:\n.PHONY: test\ntest:\n{lines}"

    @pytest.mark.parametrize(
        "body",
        [
            "if cargo $$RUNNER -p b; then :; fi",
            "if false; then :; elif cargo $$RUNNER -p b; then :; fi",
        ],
    )
    def test_a_variable_subcommand_in_a_reachable_if_test_is_unreadable(
        self, tmp_path: pathlib.Path, body: str
    ) -> None:
        """The test of an `if` or `elif` runs, so its hidden command is reported."""
        makefile = self._oneshell(body + "\ncargo test -p app")
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["unreadable"]

    def test_an_if_test_is_not_one_of_its_alternative_branches(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`if cargo test --workspace; then :; else :; fi` always runs the test."""
        makefile = self._oneshell("if cargo test --workspace; then\n  :\nelse\n  :\nfi")
        found = _makefile_invocations(tmp_path, makefile)
        assert [i["resolved"] for i in found] == [True]


class TestFourteenthReview:
    """Behaviours a fifteenth review showed to be missing."""

    def test_an_elif_test_an_earlier_unknown_branch_can_skip_is_indeterminate(
        self, tmp_path: pathlib.Path
    ) -> None:
        """If `flag` exists the `elif` test never runs, so it covers nothing alone."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n\tif test -f flag; then\n"
            "\t  :\n\telif cargo test --workspace; then\n\t  :\n\tfi\n"
            "\tcargo test -p app\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        by_flag = {i["workspace_flag"]: i["resolved"] for i in found}
        assert by_flag == {True: False, False: True}

    def test_an_unresolved_lint_library_word_may_hide_a_package_selector(
        self,
    ) -> None:
        """`--lib $LIB` with `LIB='suite -p app'` selects `app`."""
        found = _read('whitaker --lib "$LIB" -- --all-targets')
        assert [i["resolved"] for i in found] == [False]


class TestFifteenthReview:
    """Behaviours a sixteenth review showed to be missing."""

    def test_whitaker_options_for_cargo_without_a_separator_select_nothing(
        self,
    ) -> None:
        """`whitaker --all --workspace` exits with an error, so it is no coverage."""
        (found,) = _read("whitaker --all --workspace")
        assert found["resolved"] is False
        assert "no `--`" in found["why"]

    def test_a_bare_whitaker_all_is_still_a_command(self) -> None:
        """`whitaker --all` has no options for Cargo, so it is the bare command."""
        (found,) = _read("whitaker --all")
        assert found["resolved"] is True

    def test_an_elif_test_after_constant_false_is_not_a_fork_of_its_else(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The test always runs, whatever follows it."""
        makefile = (
            ".ONESHELL:\n.PHONY: test\ntest:\n\tif false; then\n\t  :\n"
            "\telif cargo test --workspace; then\n\t  :\n\telse\n\t  :\n\tfi\n"
        )
        found = _makefile_invocations(tmp_path, makefile)
        assert [i["resolved"] for i in found] == [True]


class TestNonSelectingCommands:
    """Commands that name a gate tool without selecting packages."""

    @pytest.mark.parametrize(
        "line",
        [
            '"$cargo_dylint" dylint --version',
            "report=\"$(cargo nextest show-config test-groups | sed 's/\\x1b\\[//')\"",
        ],
    )
    def test_a_query_of_the_tool_hides_nothing(
        self, tmp_path: pathlib.Path, line: str
    ) -> None:
        """Asking a tool its version or configuration is not a gate command."""
        files = _workspace_files(extra=_step_workflow(line))

        assert _build(tmp_path, files)["unreadable"] == []

    def test_a_variable_cargo_subcommand_with_a_version_flag_hides_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`cargo "$GATE" --version` asks Cargo for its version."""
        files = _workspace_files(extra=_step_workflow('cargo "$GATE" --version'))

        assert _build(tmp_path, files)["unreadable"] == []

    def test_a_help_flag_after_the_separator_does_not_hide_a_tool(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Cargo passes a flag after `--` to the test binary, so it asks nothing."""
        files = _workspace_files(extra=_step_workflow('cargo "$GATE" test -- --help'))

        assert _build(tmp_path, files)["unreadable"], (
            "a variable subcommand is still unreadable when --help follows `--`"
        )
