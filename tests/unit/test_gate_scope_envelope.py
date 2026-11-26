"""Unit tests for the `policy-input/gate-workspace-coverage` envelope builder.

The Rego suite proves what the policy makes of each envelope; these tests prove
what the builder records: the workspace's members and default selection, what
each gate command selects, how CI matrices and Make variables are read, and
what the audit refuses to place instead of guessing.
"""

from __future__ import annotations

import subprocess
import typing as typ

import pytest

from concordat.rules.gate_scope_commands import invocations_in
from concordat.rules.gate_scope_envelope import (
    ENVELOPE_KIND,
    build_gate_scope_envelope,
)
from concordat.rules.interrogate_invocation import lex_segments

if typ.TYPE_CHECKING:
    import pathlib

    from concordat.rules.gate_scope_commands import Invocation
    from concordat.rules.gate_scope_envelope import GateScopeEnvelope

ROOT_PACKAGE = (
    '[package]\nname = "app"\nversion = "0.1.0"\n\n'
    '[workspace]\nmembers = ["crates/*"]\n'
)
MEMBER = '[package]\nname = "{name}"\nversion = "0.1.0"\n'


def _lay_out(checkout: pathlib.Path, files: dict[str, str]) -> None:
    """Write *files*, creating directories."""
    for relative, text in files.items():
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _workspace_files(
    *, makefile: str = "", extra: dict[str, str] | None = None
) -> dict:
    """Return a root-package workspace with members `b` and `c`."""
    files = {
        "Cargo.toml": ROOT_PACKAGE,
        "crates/b/Cargo.toml": MEMBER.format(name="b"),
        "crates/c/Cargo.toml": MEMBER.format(name="c"),
    }
    if makefile:
        files["Makefile"] = makefile
    return {**files, **(extra or {})}


def _build(tmp_path: pathlib.Path, files: dict[str, str]) -> GateScopeEnvelope:
    """Lay out a checkout and return its envelope."""
    _lay_out(tmp_path, files)
    return build_gate_scope_envelope(tmp_path)


def _read(command: str) -> list[Invocation]:
    """Return the invocations in one shell command line."""
    segments = lex_segments(command)
    assert segments is not None, f"the lexer must accept {command!r}"
    return invocations_in(segments, ("test", command))


def _selection(command: str) -> tuple[str, bool, list[str], list[str]]:
    """Return `(tool, workspace_flag, packages, excludes)` of the only invocation."""
    (found,) = _read(command)
    return found["tool"], found["workspace_flag"], found["packages"], found["excludes"]


class TestCommands:
    """What one command is, and what it selects."""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("cargo clippy --workspace", ("clippy", True, [], [])),
            ("cargo clippy --all --all-targets", ("clippy", True, [], [])),
            (
                "cargo +nightly clippy -p a --package b",
                ("clippy", False, ["a", "b"], []),
            ),
            ("cargo nextest run --workspace --exclude x", ("nextest", True, [], ["x"])),
            ("cargo test --doc", ("test-doc", False, [], [])),
            ("cargo test --workspace -- --nocapture", ("test", True, [], [])),
            ("cargo llvm-cov nextest --workspace", ("nextest", True, [], [])),
            ("cargo llvm-cov --workspace", ("test", True, [], [])),
            ("cargo test --package=a --exclude=b", ("test", False, ["a"], ["b"])),
        ],
    )
    def test_cargo_tools_read_their_selection(
        self, command: str, expected: tuple[str, bool, list[str], list[str]]
    ) -> None:
        """Cargo's own spellings of the selection are read as written."""
        assert _selection(command) == expected

    def test_a_flag_after_the_separator_is_not_cargos(self) -> None:
        """`--workspace` after `--` goes to the harness, so it selects nothing."""
        _tool, is_workspace, _packages, _excludes = _selection(
            "cargo test -- --workspace"
        )
        assert not is_workspace

    @pytest.mark.parametrize(
        ("command", "is_workspace"),
        [
            ("whitaker --all -- --workspace --all-targets", True),
            ("whitaker --all -- --all --all-targets", True),
            ("whitaker --all -- --all-targets --all-features", False),
            ("whitaker --all", False),
            ("whitaker --lib whitaker_suite -- --workspace --exclude b", True),
            ("cargo dylint --all -- --workspace", True),
            ("whitaker --all --workspace -- --all-targets", True),
            ("whitaker --all -p app -- --all-targets", False),
        ],
    )
    def test_whitakers_own_all_selects_libraries_not_packages(
        self, command: str, *, is_workspace: bool
    ) -> None:
        """Whitaker's `--all` selects libraries; `--workspace` counts on either side."""
        tool, flag, _packages, _excludes = _selection(command)
        assert tool == "whitaker"
        assert flag is is_workspace

    @pytest.mark.parametrize(
        "command",
        [
            "echo cargo test",
            "whitaker --version",
            "cargo clippy --help",
            "cargo build --workspace",
            "cargo llvm-cov report",
            "cargo nextest list",
            "command -v whitaker",
            "cargo fmt --all",
        ],
    )
    def test_commands_that_run_no_gate_tool_are_not_invocations(
        self, command: str
    ) -> None:
        """Printing a name, asking for help, or another subcommand is not a gate."""
        assert _read(command) == []

    def test_environment_prefixes_and_shell_keywords_are_skipped(self) -> None:
        """`env X=1 cargo test` and `then cargo test` run cargo test."""
        assert _selection("env RUSTFLAGS=-Dwarnings cargo test --workspace")[1]
        assert _selection("RUSTFLAGS=-Dwarnings cargo test --workspace")[1]
        assert _selection("if cargo test --workspace; then echo ok; fi")[1]

    def test_a_cd_moves_every_later_command(self) -> None:
        """A command after `cd sub` runs against another workspace."""
        found = _read("cd sub && cargo test --workspace && cargo clippy")
        assert [i["changes_directory"] for i in found] == [True, True]

    def test_cargos_global_directory_option_moves_the_command(self) -> None:
        """`cargo -C nested test` runs in `nested`, whatever follows."""
        (found,) = _read(
            "cargo +nightly -C nested -Z unstable-options test --workspace"
        )
        assert found["changes_directory"]
        (plain,) = _read("cargo +nightly test --workspace -C opt-level=1")
        assert not plain["changes_directory"]

    def test_a_manifest_path_moves_the_command(self) -> None:
        """`--manifest-path` selects another manifest than the root one."""
        (found,) = _read("cargo test --manifest-path sub/Cargo.toml")
        assert found["changes_directory"]

    @pytest.mark.parametrize(
        ("command", "is_resolved"),
        [
            ("cargo test --workspace $EXTRA", False),
            ("cargo test $FLAGS", False),
            ("whitaker --lib $LIBRARY -- --workspace", True),
            ("whitaker --all -- --workspace $EXTRA", False),
            ("cargo test --workspace --exclude $SKIP", False),
            ("cargo test -p $PACKAGE", False),
            ("cargo test --package=$PACKAGE --workspace", False),
            # a Make reference is substituted as text and a shell variable splits
            # into words, so after any option either may add `--exclude b`
            ("cargo nextest run --profile $(PROFILE) --all-targets", False),
            ("cargo nextest run -j $(JOBS) --all-features", False),
            ("cargo test --features $(FEATURES)", False),
            ("cargo nextest run --profile $PROFILE --all-targets", False),
            ("cargo test --workspace --color $COLOR", False),
            ("whitaker $FLAGS", False),
            ("cargo test --profile $PROFILE $EXTRA", False),
        ],
    )
    def test_an_unresolved_word_matters_only_where_it_can_select(
        self, command: str, *, is_resolved: bool
    ) -> None:
        """An unresolved word may supply `--exclude`, so it hides the selection.

        The one exception is anything before Whitaker's `--`, which is
        Whitaker's own.
        """
        (found,) = _read(command)
        assert found["resolved"] is is_resolved

    @pytest.mark.parametrize(
        ("command", "tool"),
        [
            ("./scripts/run_whitaker.py --packages x", "whitaker"),
            ("scripts/lint_clippy.sh", "clippy"),
            ("python3 scripts/run_nextest.py", None),
            ("./scripts/check_serial_nextest_matrix.py", None),
            ("./scripts/install_whitaker.sh", None),
        ],
    )
    def test_a_wrapper_script_named_for_a_tool_is_unresolved(
        self, command: str, tool: str | None
    ) -> None:
        """A script that wraps a gate tool is a fact the audit cannot place."""
        found = _read(command)
        if tool is None:
            assert found == []
            return
        (invocation,) = found
        assert invocation["tool"] == tool
        assert not invocation["resolved"]
        assert "wrapper script" in invocation["why"]


class TestWorkspace:
    """The root workspace's shape."""

    def test_a_root_package_workspace_lists_every_member(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The root package and the globbed members are the member set."""
        envelope = _build(tmp_path, _workspace_files())
        assert envelope["kind"] == ENVELOPE_KIND
        workspace = envelope["workspace"]
        assert workspace is not None
        assert workspace["kind"] == "root-package"
        assert workspace["root_package"] == "app"
        assert workspace["members"] == ["app", "b", "c"]
        assert workspace["default_members"] is None

    def test_path_dependencies_inside_the_root_are_members(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Cargo enrols a path dependency inside the workspace root."""
        files = {
            "Cargo.toml": '[workspace]\nmembers = ["crates/a"]\n',
            "crates/a/Cargo.toml": (
                '[package]\nname = "a"\nversion = "0.1.0"\n\n'
                '[dependencies]\nhelper = { path = "../helper" }\n'
            ),
            "crates/helper/Cargo.toml": MEMBER.format(name="helper"),
        }
        workspace = _build(tmp_path, files)["workspace"]
        assert workspace is not None
        assert workspace["kind"] == "virtual"
        assert workspace["members"] == ["a", "helper"]

    def test_default_members_are_recorded_by_package_name(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`default-members` paths are read to the packages they name."""
        files = _workspace_files()
        files["Cargo.toml"] = (
            '[workspace]\nmembers = ["crates/*"]\ndefault-members = ["crates/b"]\n'
        )
        workspace = _build(tmp_path, files)["workspace"]
        assert workspace is not None
        assert workspace["default_members"] == ["b"]

    def test_a_package_without_a_workspace_is_single(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No `[workspace]` table, no narrowing."""
        files = {"Cargo.toml": '[package]\nname = "solo"\nversion = "0.1.0"\n'}
        workspace = _build(tmp_path, files)["workspace"]
        assert workspace is not None
        assert (workspace["kind"], workspace["members"]) == ("single", ["solo"])

    def test_an_unreadable_manifest_is_recorded_not_guessed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A manifest that is not TOML leaves no workspace and a reason."""
        envelope = _build(tmp_path, {"Cargo.toml": "[package\n"})
        assert envelope["workspace"] is None
        assert any(
            "Cargo.toml: invalid TOML" in entry for entry in envelope["unreadable"]
        )

    def test_a_checkout_without_a_manifest_is_not_applicable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No root manifest, no audit."""
        envelope = _build(tmp_path, {"README.md": "# demo\n"})
        assert envelope["applicability"]["cargo_manifest"] is False
        assert envelope["workspace"] is None


class TestMakefile:
    """How the gate targets are followed and read."""

    def test_variables_and_prerequisites_are_followed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`lint` reaches its prerequisites; `?=` variables and addprefix expand."""
        makefile = (
            "CARGO ?= cargo\nWHITAKER ?= whitaker\n"
            "PKGS ?= --workspace $(addprefix --exclude ,b c)\n"
            ".PHONY: lint lint-clippy lint-whitaker test other\n"
            "lint: lint-clippy lint-whitaker\n"
            "lint-clippy:\n\t$(CARGO) clippy --workspace\n"
            "lint-whitaker:\n\t$(WHITAKER) --all -- $(PKGS)\n"
            "test:\n\t$(CARGO) test --workspace\n"
            "other:\n\tcargo clippy\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        found = {(i["tool"], i["workspace_flag"]) for i in envelope["invocations"]}
        assert found == {("clippy", True), ("whitaker", True), ("test", True)}
        whitaker = next(i for i in envelope["invocations"] if i["tool"] == "whitaker")
        assert whitaker["excludes"] == ["b", "c"]
        assert "other" not in envelope["makefile"]["targets"]
        assert envelope["makefile"]["is_resolved"]

    def test_a_probe_conditional_is_read_both_ways(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The runner chosen by a version probe is read as each runner."""
        makefile = (
            "RUNNER = $(if $(shell cargo nextest --version 2>/dev/null),"
            "nextest run,test)\n.PHONY: test\n"
            "test:\n\tcargo $(RUNNER) --workspace\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert {i["tool"] for i in envelope["invocations"]} == {"nextest", "test"}

    def test_a_delegated_make_clears_the_resolved_flag(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`make -C dir` leaves the Makefile, so the walk says it skipped it."""
        makefile = ".PHONY: lint\nlint:\n\t$(MAKE) -C crates/b lint\n\tcargo clippy\n"
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["makefile"]["is_resolved"] is False

    def test_a_command_behind_an_unset_variable_is_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A gate tool behind an unset variable is carried, not dropped."""
        makefile = ".PHONY: lint\nlint:\n\t$(LINTER) clippy --workspace\n"
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["invocations"] == []
        assert envelope["unreadable"]

    def test_a_cargo_subcommand_behind_an_unset_variable_is_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`cargo $(SUBCOMMAND) --workspace` may be any gate tool."""
        makefile = ".PHONY: test\ntest:\n\tcargo $(RUNNER) --workspace\n"
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["invocations"] == []
        assert envelope["unreadable"]

    @pytest.mark.parametrize(
        "definition",
        [
            "$(or $(shell command -v whitaker 2>/dev/null),whitaker)",
            "$(or $(wildcard $(HOME)/.local/bin/whitaker),whitaker)",
            "$(firstword $(wildcard $(LOCAL_BIN)/whitaker) whitaker)",
        ],
    )
    def test_a_conditional_whitaker_variable_is_the_whitaker_command(
        self, tmp_path: pathlib.Path, definition: str
    ) -> None:
        """`$(WHITAKER)` stands for the tool even when its value is conditional."""
        makefile = (
            f"WHITAKER ?= {definition}\n"
            ".PHONY: lint\nlint:\n\t$(WHITAKER) --all -- --workspace\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        (found,) = envelope["invocations"]
        assert (found["tool"], found["workspace_flag"]) == ("whitaker", True)

    def test_a_variable_that_only_begins_with_whitaker_is_an_argument(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`$(WHITAKER_PACKAGES)` is not the command."""
        makefile = (
            ".PHONY: lint\nlint:\n"
            "\twhitaker --all -- $(WHITAKER_PACKAGES) --all-targets\n"
        )
        (found,) = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
        assert found["tool"] == "whitaker"
        assert not found["resolved"]

    def test_a_parenthesised_string_does_not_stop_the_line_lexing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A message such as `(CI uses the action)` is text, not a substitution."""
        makefile = (
            ".PHONY: lint\nlint:\n"
            '\t@command -v whitaker >/dev/null 2>&1 || { echo "x (y)" >&2; exit 1; }\n'
            "\twhitaker --all -- --workspace\n"
        )
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["unreadable"] == []
        assert [i["tool"] for i in envelope["invocations"]] == ["whitaker"]

    def test_the_root_manifest_named_explicitly_stays_in_the_root_workspace(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`--manifest-path Cargo.toml` is the root manifest, not another one."""
        makefile = (
            ".PHONY: lint\nlint:\n"
            "\tcargo clippy --manifest-path Cargo.toml --workspace\n"
        )
        (found,) = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
        assert found["changes_directory"] is False

    @pytest.mark.parametrize(
        "line",
        [
            'echo "installing whitaker $(WHITAKER_INSTALLER_VERSION)"',
            'tool_pins="${tool_pins}-whitaker${WHITAKER_INSTALLER_VERSION}"',
            "whitaker-installer --version",
            "echo done # clippy",
        ],
    )
    def test_a_line_that_only_mentions_a_tool_is_not_unreadable(
        self, tmp_path: pathlib.Path, line: str
    ) -> None:
        """An identifier or a string that contains a tool name is not a command."""
        makefile = f".PHONY: lint\nlint:\n\t{line}\n"
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["invocations"] == []
        assert envelope["unreadable"] == []


class TestWorkflows:
    """How CI steps and matrices are read."""

    @staticmethod
    def _workflow(run: str, matrix: str = "") -> dict[str, str]:
        """Return a one-step workflow file."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            f"{matrix}    steps:\n      - run: {run}\n"
        )
        return {".github/workflows/ci.yml": text}

    def test_each_include_entry_is_a_command_of_its_own(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`${{ matrix.flags }}` is filled in from every `include` entry."""
        matrix = (
            "    strategy:\n      matrix:\n        include:\n"
            "          - flags: --workspace\n          - flags: --features sqlite\n"
        )
        files = _workspace_files(
            extra=self._workflow("cargo test ${{ matrix.flags }}", matrix)
        )
        envelope = _build(tmp_path, files)
        assert sorted(i["workspace_flag"] for i in envelope["invocations"]) == [
            False,
            True,
        ]

    def test_an_axis_and_an_include_are_merged(self, tmp_path: pathlib.Path) -> None:
        """An `include` entry extends each combination it does not contradict."""
        matrix = (
            "    strategy:\n      matrix:\n        os: [linux, mac]\n"
            "        include:\n          - os: linux\n            flags: --workspace\n"
        )
        files = _workspace_files(
            extra=self._workflow(
                "cargo test ${{ matrix.flags }} # ${{ matrix.os }}", matrix
            )
        )
        envelope = _build(tmp_path, files)
        commands = {i["command"] for i in envelope["invocations"]}
        assert any("--workspace" in command for command in commands)

    def test_an_expression_matrix_is_unreadable(self, tmp_path: pathlib.Path) -> None:
        """A matrix computed at run time cannot be expanded here."""
        matrix = (
            "    strategy:\n      matrix: ${{ fromJson(needs.plan.outputs.matrix) }}\n"
        )
        files = _workspace_files(extra=self._workflow("cargo test", matrix))
        envelope = _build(tmp_path, files)
        assert envelope["invocations"] == []
        assert any("matrix is computed" in entry for entry in envelope["unreadable"])

    def test_another_expression_in_the_selection_is_unresolved(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`${{ inputs.flags }}` cannot be filled in, so it may select packages."""
        files = _workspace_files(extra=self._workflow("cargo test ${{ inputs.flags }}"))
        (found,) = _build(tmp_path, files)["invocations"]
        assert found["resolved"] is False

    def test_a_composite_action_step_is_read(self, tmp_path: pathlib.Path) -> None:
        """Composite actions run gate commands too."""
        action = (
            "name: gate\nruns:\n  using: composite\n  steps:\n"
            "    - shell: bash\n      run: cargo nextest run --workspace\n"
        )
        files = _workspace_files(extra={".github/actions/gate/action.yml": action})
        (found,) = _build(tmp_path, files)["invocations"]
        assert (found["tool"], found["workspace_flag"]) == ("nextest", True)

    def test_a_multi_line_step_is_read_line_by_line(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Continuations are joined and each command is read."""
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: |\n          cargo clippy \\\n"
            "            --workspace\n          cargo test -p app\n"
        )
        envelope = _build(
            tmp_path, _workspace_files(extra={".github/workflows/ci.yml": text})
        )
        found = {
            (i["tool"], i["workspace_flag"], tuple(i["packages"]))
            for i in envelope["invocations"]
        }
        assert found == {("clippy", True, ()), ("test", False, ("app",))}


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
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
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

    @staticmethod
    def _step_workflow(script: str) -> dict[str, str]:
        """Return a workflow whose one step runs *script*."""
        body = "".join(f"          {line}\n" for line in script.splitlines())
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: |\n" + body
        )
        return {".github/workflows/ci.yml": text}

    def test_a_whole_workspace_probe_is_not_safe_beside_a_narrow_ci_command(
        self, tmp_path: pathlib.Path
    ) -> None:
        """CI's narrow `cargo test` may be all that runs if the probe picks nextest."""
        makefile = (
            ".PHONY: test\ntest:\n\tcargo $(if $(shell cargo nextest --version "
            "2>/dev/null),test --workspace,nextest run --workspace)\n"
        )
        files = _workspace_files(
            makefile=makefile, extra=self._step_workflow("cargo test -p app")
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
        files = _workspace_files(extra=self._step_workflow(script))
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
        files = _workspace_files(extra=self._step_workflow(script))
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
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
        assert [i["changes_directory"] for i in found] == [True]


class TestSeventhReview:
    """Behaviours an eighth review showed to be missing."""

    @staticmethod
    def _script(script: str) -> dict[str, str]:
        """Return a workflow whose one step runs *script*."""
        body = "".join(f"          {line}\n" for line in script.splitlines())
        text = (
            "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: |\n" + body
        )
        return {".github/workflows/ci.yml": text}

    def test_an_unknown_elif_after_an_if_false_starts_a_fork(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The `elif` and its `else` are alternatives."""
        script = (
            "if false; then\n  :\nelif test -f flag; then\n  cargo test -p app\n"
            "else\n  cargo test -p b\nfi"
        )
        files = _workspace_files(extra=self._script(script))
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
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
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
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
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
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
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
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
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
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
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
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
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
        found = _build(tmp_path, _workspace_files(makefile=makefile))["invocations"]
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


class TestExamples:
    """The prose examples a finding names."""

    def test_only_fenced_commands_are_examples(self, tmp_path: pathlib.Path) -> None:
        """A command in running prose is not an example; a prompt is stripped."""
        files = _workspace_files(
            extra={
                "AGENTS.md": (
                    "Run cargo clippy --all-targets first.\n\n"
                    "```sh\n$ cargo clippy --all-targets\n```\n"
                ),
                "docs/guide.md": "```sh\ncargo test --workspace\n```\n",
            }
        )
        examples = _build(tmp_path, files)["examples"]
        assert [(e["source"], e["workspace_flag"]) for e in examples] == [
            ("AGENTS.md:4", False),
            ("docs/guide.md:2", True),
        ]
