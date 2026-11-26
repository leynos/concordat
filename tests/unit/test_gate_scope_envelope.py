"""Unit tests for the `policy-input/gate-workspace-coverage` envelope builder.

The Rego suite proves what the policy makes of each envelope; these tests prove
what the builder records: the workspace's members and default selection, what
each gate command selects, how CI matrices and Make variables are read, and
what the audit refuses to place instead of guessing.
"""

from __future__ import annotations

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
        ],
    )
    def test_whitakers_own_all_selects_libraries_not_packages(
        self, command: str, *, is_workspace: bool
    ) -> None:
        """Only a spelling after `--` reaches Cargo, so only it counts."""
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

    def test_a_manifest_path_moves_the_command(self) -> None:
        """`--manifest-path` selects another manifest than the root one."""
        (found,) = _read("cargo test --manifest-path sub/Cargo.toml")
        assert found["changes_directory"]

    @pytest.mark.parametrize(
        ("command", "is_resolved"),
        [
            ("cargo test --workspace $EXTRA", True),
            ("cargo test $FLAGS", False),
            ("cargo test --workspace --exclude $SKIP", False),
            ("cargo test -p $PACKAGE", False),
            ("cargo test --package=$PACKAGE --workspace", False),
        ],
    )
    def test_an_unresolved_word_matters_only_where_it_can_select(
        self, command: str, *, is_resolved: bool
    ) -> None:
        """A stray word cannot narrow a spelled-out workspace; a value flag can."""
        (found,) = _read(command)
        assert found["resolved"] is is_resolved

    @pytest.mark.parametrize(
        ("command", "tool"),
        [
            ("./scripts/run_whitaker.py --packages x", "whitaker"),
            ("scripts/lint_clippy.sh", "clippy"),
            ("python3 scripts/run_nextest.py", None),
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

    def test_a_line_naming_a_tool_that_cannot_be_read_is_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A tool behind an unset variable is carried, not dropped."""
        makefile = ".PHONY: lint\nlint:\n\t$(LINTER) --workspace # clippy\n"
        envelope = _build(tmp_path, _workspace_files(makefile=makefile))
        assert envelope["invocations"] == []
        assert envelope["unreadable"]


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
