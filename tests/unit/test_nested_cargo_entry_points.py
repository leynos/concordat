"""Classify the prefetch state of each test entry point.

A nested cargo run is only safe from the shared package-cache lock when the
dependencies are already in the cache, and nothing proves that unless a
`cargo fetch --locked` runs first and is allowed to stop the job. This module's
tests therefore care most about the cases where order is *not* proven: a `;`
between commands, an ignored error, a step that runs whatever happened before
it.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.nested_cargo_entry_points import collect_entry_points
from concordat.rules.nested_cargo_scripts import split_commands

if typ.TYPE_CHECKING:
    import pathlib

MAKEFILE = "Makefile"
NEXTEST_CONFIG = ".config/nextest.toml"
WORKFLOW = ".github/workflows/ci.yml"


def put(root: pathlib.Path, relative: str, text: str) -> pathlib.Path:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def gates(script: str) -> list[tuple[str, bool]]:
    """Return each command of *script* with whether its success gates the next."""
    return [(command.text, command.gates_next) for command in split_commands(script)]


def states(root: pathlib.Path) -> list[tuple[str, str, str]]:
    """Return `(kind, name, state)` for every fact, in fact order."""
    problems: list[str] = []
    return [
        (fact["kind"], fact["name"], fact["state"])
        for fact in collect_entry_points(root, problems)
    ]


def makefile_state(root: pathlib.Path) -> list[tuple[str, str]]:
    """Return `(name, state)` for the Makefile's test entry point only."""
    return [(name, state) for kind, name, state in states(root) if kind == "makefile"]


# --- the connector splitter -------------------------------------------------


def test_only_and_and_sequencing_separate_proven_commands() -> None:
    """`;`, `||`, `|`, `&` and subshells do not prove one command ran first."""
    assert gates("cargo fetch --locked && cargo test") == [
        ("cargo fetch --locked", True),
        ("cargo test", False),
    ]


def test_a_semicolon_does_not_prove_order() -> None:
    """A `;` runs the next command whatever the first did."""
    assert gates("cargo fetch --locked ; cargo test") == [
        ("cargo fetch --locked", False),
        ("cargo test", False),
    ]


def test_a_fallback_does_not_prove_order() -> None:
    """`||` runs the second command when the first failed."""
    assert gates("cargo fetch --locked || cargo test") == [
        ("cargo fetch --locked", False),
        ("cargo test", False),
    ]


def test_a_pipe_does_not_prove_order() -> None:
    """A pipeline runs its stages at the same time, not one before another."""
    assert gates("cargo fetch --locked | cargo test") == [
        ("cargo fetch --locked", False),
        ("cargo test", False),
    ]


def test_a_background_command_does_not_prove_order() -> None:
    """A `&` returns immediately rather than waiting for the command to finish."""
    assert gates("cargo fetch --locked & cargo test") == [
        ("cargo fetch --locked", False),
        ("cargo test", False),
    ]


def test_a_subshell_does_not_prove_order() -> None:
    """A command in `( )` is not proven to precede what follows."""
    assert gates("(cargo fetch --locked) && cargo test") == [
        ("(cargo fetch --locked)", False),
        ("cargo test", False),
    ]


def test_a_connector_inside_a_quote_is_not_a_connector() -> None:
    """`echo "a && b"` is one command, not two."""
    assert gates('echo "a && b" && cargo test') == [
        ('echo "a && b"', True),
        ("cargo test", False),
    ]


@pytest.mark.parametrize(
    "script",
    [
        "cargo fetch --locked 2>&1 && cargo test",
        "cargo fetch --locked >&2 && cargo test",
        "cargo fetch --locked <&0 && cargo test",
        "cargo fetch --locked &> fetch.log && cargo test",
        "cargo fetch --locked &>> fetch.log && cargo test",
    ],
    ids=["both-streams", "to-stderr", "from-stdin", "append-both", "append-both-long"],
)
def test_a_redirection_ampersand_is_not_a_background_connector(script: str) -> None:
    """A `&` next to a redirection belongs to the redirection, not a `&`.

    Reading `2>&1` as a background command splits the fetch into fragments,
    so the fetch appears to gate nothing and a correctly ordered line is
    reported as an absent prefetch.
    """
    commands = gates(script)
    assert len(commands) == 2, commands
    assert commands[0][0].startswith("cargo fetch --locked"), commands
    assert commands[0][1] is True, commands


def test_newlines_are_fail_fast_sequencing() -> None:
    """Each line of a script completes before the next one starts."""
    assert gates("cargo fetch --locked\ncargo test") == [
        ("cargo fetch --locked", True),
        ("cargo test", False),
    ]


def test_a_continuation_does_not_split_a_command() -> None:
    """A backslash-newline is part of the command, not a line break."""
    continued = "cargo fetch \\\n    --locked"
    assert gates(continued) == [(continued, False)]


# --- the Makefile test target ----------------------------------------------


def test_a_prerequisite_prefetch_proves_the_order(tmp_path: pathlib.Path) -> None:
    """Make runs every prerequisite before the recipe that depends on it."""
    put(
        tmp_path,
        MAKEFILE,
        "fetch:\n\tcargo fetch --locked\n\ntest: fetch\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_transitive_prerequisite_prefetch_proves_the_order(
    tmp_path: pathlib.Path,
) -> None:
    """A prerequisite of a prerequisite still completes first."""
    put(
        tmp_path,
        MAKEFILE,
        "fetch:\n\tcargo fetch --locked\n\nprepare: fetch\n\t@true\n\n"
        "test: prepare\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_an_earlier_recipe_line_proves_the_order(tmp_path: pathlib.Path) -> None:
    """A recipe line runs to completion, and a failure stops the recipe."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo fetch --locked\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_prefetch_on_the_test_line_before_the_test_proves_the_order(
    tmp_path: pathlib.Path,
) -> None:
    """`&&` between two commands in one recipe line is still sequencing."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo fetch --locked && cargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_semicolon_before_the_test_is_not_proof(tmp_path: pathlib.Path) -> None:
    """A failed fetch followed by `;` still reaches the test."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo fetch --locked ; cargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_an_ignored_error_prefetch_is_not_proof(tmp_path: pathlib.Path) -> None:
    """`-cargo fetch` failing does not stop the recipe."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\t-cargo fetch --locked\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_a_silent_ignored_error_prefetch_is_not_proof(tmp_path: pathlib.Path) -> None:
    """`-` prefixed commands fail without stopping the recipe, however spelled."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\t-@cargo fetch --locked\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_a_prefetch_after_the_test_is_not_proof(tmp_path: pathlib.Path) -> None:
    """Order within a recipe is file order."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo test\n\tcargo fetch --locked\n",
    )
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_an_unlocked_fetch_is_not_proof(tmp_path: pathlib.Path) -> None:
    """`cargo fetch` without `--locked` may still update a manifest."""
    put(
        tmp_path,
        MAKEFILE,
        "fetch:\n\tcargo fetch\n\ntest: fetch\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_a_toolchain_qualified_locked_fetch_is_proof(tmp_path: pathlib.Path) -> None:
    """`cargo +nightly fetch --locked` is the same prefetch."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo +nightly fetch --locked\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_manifest_path_fetch_is_proof(tmp_path: pathlib.Path) -> None:
    """A manifest path selects the package, not the subcommand."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo fetch --locked --manifest-path Cargo.toml\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_cargo_variable_prefetch_is_proof_when_it_resolves(
    tmp_path: pathlib.Path,
) -> None:
    """`$(CARGO)` is the repository's cargo, and a plain assignment settles it."""
    put(
        tmp_path,
        MAKEFILE,
        "CARGO ?= cargo\n\ntest:\n\t$(CARGO) fetch --locked\n\t$(CARGO) test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_an_unresolved_cargo_variable_is_uncertain(tmp_path: pathlib.Path) -> None:
    """A variable the expansion cannot settle leaves both commands unknown."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\t$(CARGO) fetch --locked\n\t$(CARGO) test\n",
    )
    assert makefile_state(tmp_path) == [("test", "uncertain")]


def test_an_oneshell_recipe_is_uncertain(tmp_path: pathlib.Path) -> None:
    """With `.ONESHELL` a line may not stand alone as a command."""
    put(
        tmp_path,
        MAKEFILE,
        ".ONESHELL:\n\ntest:\n\tcargo fetch --locked\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "uncertain")]


def test_a_continued_recipe_line_is_read_after_its_continuation(
    tmp_path: pathlib.Path,
) -> None:
    r"""A backslash-newline is removed before the shell reads the command.

    whitaker's `test` recipe is one long `set -eu; \` chain, and the loader
    keeps every continuation in the recipe text. Treating the leading
    backslash-newline as part of the next command hides the assignment and the
    tool word that follow it, so the whole recipe reads as unreadable.
    """
    put(
        tmp_path,
        MAKEFILE,
        "CARGO ?= cargo\n"
        "RUST_FLAGS ?= -D warnings\n"
        "test:\n"
        "\tset -eu; \\\n"
        '\tRUSTFLAGS="$(RUST_FLAGS)" $(CARGO) nextest run --workspace; \\\n'
        "\techo done\n",
    )
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_a_recursive_make_prerequisite_is_uncertain(tmp_path: pathlib.Path) -> None:
    """`$(MAKE) fetch` may or may not run the prefetch before the recipe."""
    put(
        tmp_path,
        MAKEFILE,
        "fetch:\n\tcargo fetch --locked\n\ntest:\n\t$(MAKE) fetch\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "uncertain")]


def test_an_unparseable_makefile_is_a_problem(tmp_path: pathlib.Path) -> None:
    """A Makefile the loader refuses leaves the entry point unknown."""
    put(tmp_path, MAKEFILE, "test:\n\tcargo test\nendif\n")
    problems: list[str] = []
    collect_entry_points(tmp_path, problems)
    assert any("Makefile" in problem for problem in problems)


def test_a_makefile_without_a_test_target_reports_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """A repository with no `test` target has no Makefile entry point."""
    put(tmp_path, MAKEFILE, "check:\n\tcargo check\n")
    assert makefile_state(tmp_path) == []


def test_a_test_target_that_runs_no_test_command_reports_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """A `test` target that only builds is not an entry point for tests."""
    put(tmp_path, MAKEFILE, "test:\n\tcargo build\n")
    assert makefile_state(tmp_path) == []


# --- nextest -----------------------------------------------------------------


def test_a_nextest_setup_script_proves_the_order(tmp_path: pathlib.Path) -> None:
    """A `default`-profile script with an `all()` filter runs before every test."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_nextest_setup_command_may_be_a_list(tmp_path: pathlib.Path) -> None:
    """Nextest accepts a setup command as an argument list as well as a string."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = ["cargo", "fetch", "--locked"]\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_named_setup_script_without_a_definition_is_uncertain(
    tmp_path: pathlib.Path,
) -> None:
    """A name nothing defines is a broken config, not a proven-cold cache."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "missing"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    assert makefile_state(tmp_path) == [("test", "uncertain")]


def test_a_named_setup_script_that_does_not_fetch_is_absent(
    tmp_path: pathlib.Path,
) -> None:
    """A defined setup script that runs something else still proves no prefetch."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "touch"\n'
        '[scripts.setup.touch]\ncommand = "touch marker"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_a_nextest_script_with_another_filter_is_not_proof(
    tmp_path: pathlib.Path,
) -> None:
    """`filter = "package(foo)"` runs only for that package's tests."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "package(foo)"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_a_nextest_profile_selected_by_flag_is_honoured(
    tmp_path: pathlib.Path,
) -> None:
    """`--profile ci` selects the script that profile declares."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.ci.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run --profile ci\n")
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_nextest_profile_selected_by_short_flag_is_honoured(
    tmp_path: pathlib.Path,
) -> None:
    """`-P ci` is the same selection as `--profile ci`."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.ci.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run -P ci\n")
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_used_nextest_profile_is_its_own_entry_point(tmp_path: pathlib.Path) -> None:
    """The setup script that would fix an absent prefetch lives in the config."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "package(foo)"\nsetup = "touch"\n'
        '[scripts.setup.touch]\ncommand = "touch marker"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    assert states(tmp_path) == [
        ("makefile", "test", "absent"),
        ("nextest", "default", "absent"),
    ]


def test_an_unused_nextest_profile_is_not_an_entry_point(
    tmp_path: pathlib.Path,
) -> None:
    """A config no test command selects describes no entry point."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo test\n")
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_an_undecodable_nextest_config_is_a_problem(tmp_path: pathlib.Path) -> None:
    """A config that is not TOML leaves the setup scripts unknown."""
    put(tmp_path, NEXTEST_CONFIG, "[[profile.default.scripts]\n")
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    problems: list[str] = []
    collect_entry_points(tmp_path, problems)
    assert any(NEXTEST_CONFIG in problem for problem in problems)


def test_a_repository_without_a_nextest_config_gets_no_config_fact(
    tmp_path: pathlib.Path,
) -> None:
    """Theoremc has no `.config/nextest.toml`; naming a line of it would be a lie.

    The absent prefetch is still reported, by the entry point that runs the
    test command rather than by a configuration file that does not exist.
    """
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    assert states(tmp_path) == [("makefile", "test", "absent")]


# --- GitHub Actions ----------------------------------------------------------


def test_an_earlier_workflow_step_proves_the_order(tmp_path: pathlib.Path) -> None:
    """Steps run in order, and a failed step stops the job by default."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        "      - run: cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "before")]


def test_a_continue_on_error_prefetch_is_not_proof(tmp_path: pathlib.Path) -> None:
    """A step that is allowed to fail does not prove the next one saw success."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n        continue-on-error: true\n"
        "      - run: cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_an_always_step_is_not_proof(tmp_path: pathlib.Path) -> None:
    """`if: always()` runs whether or not the prefetch succeeded."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        "      - run: cargo test\n        if: always()\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_a_failure_step_is_not_proof(tmp_path: pathlib.Path) -> None:
    """`if: failure()` runs precisely when the prefetch did not succeed."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        "      - run: cargo test\n        if: failure()\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_a_prefetch_in_the_same_run_block_proves_the_order(
    tmp_path: pathlib.Path,
) -> None:
    """Multi-line `run` blocks share one fail-fast shell."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: |\n          cargo fetch --locked\n          cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "before")]


def _multi_line(tmp_path: pathlib.Path, job_body: str) -> None:
    """Write a workflow whose `test` job runs a fetch then a test on two lines."""
    put(
        tmp_path,
        WORKFLOW,
        f"jobs:\n  test:\n{job_body}"
        "    steps:\n"
        "      - run: |\n          cargo fetch --locked\n          cargo test\n",
    )


def test_a_multi_line_block_on_a_non_fail_fast_shell_is_absent(
    tmp_path: pathlib.Path,
) -> None:
    """`bash {0}` runs the test even when the fetch on the line above failed."""
    _multi_line(
        tmp_path,
        "    runs-on: ubuntu-latest\n"
        "    defaults:\n      run:\n        shell: bash {0}\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_a_multi_line_block_on_a_fail_fast_template_proves_the_order(
    tmp_path: pathlib.Path,
) -> None:
    """`bash -e {0}` stops the script, so the next line saw a successful fetch."""
    _multi_line(
        tmp_path,
        "    runs-on: ubuntu-latest\n"
        "    defaults:\n      run:\n        shell: bash -e {0}\n",
    )
    assert states(tmp_path) == [("workflow", "test", "before")]


def test_a_step_shell_overrides_the_job_default(tmp_path: pathlib.Path) -> None:
    """The step is the most specific declaration, as GitHub resolves it."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    defaults:\n      run:\n        shell: bash {0}\n"
        "    steps:\n      - shell: bash -e {0}\n        run: |\n"
        "          cargo fetch --locked\n          cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "before")]


def test_a_job_default_overrides_the_workflow_default(tmp_path: pathlib.Path) -> None:
    """A job's own default is more specific than the workflow's."""
    put(
        tmp_path,
        WORKFLOW,
        "defaults:\n  run:\n    shell: bash -e {0}\n"
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    defaults:\n      run:\n        shell: bash {0}\n"
        "    steps:\n      - run: |\n"
        "          cargo fetch --locked\n          cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_a_workflow_default_is_used_when_the_job_declares_none(
    tmp_path: pathlib.Path,
) -> None:
    """The workflow's `defaults.run.shell` reaches every job that has none."""
    put(
        tmp_path,
        WORKFLOW,
        "defaults:\n  run:\n    shell: bash -e {0}\n"
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: |\n"
        "          cargo fetch --locked\n          cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "before")]


def test_a_windows_runner_does_not_fail_fast(tmp_path: pathlib.Path) -> None:
    """The Windows default is `pwsh`, which checks only the last native status."""
    _multi_line(tmp_path, "    runs-on: windows-latest\n")
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_a_pwsh_shell_does_not_fail_fast(tmp_path: pathlib.Path) -> None:
    """`pwsh` continues after a failed native command."""
    _multi_line(
        tmp_path,
        "    runs-on: ubuntu-latest\n    defaults:\n      run:\n        shell: pwsh\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_an_unresolvable_shell_with_a_preceding_fetch_is_uncertain(
    tmp_path: pathlib.Path,
) -> None:
    """`runs-on: ${{ matrix.os }}` leaves the shell unproven, and the fetch idle."""
    _multi_line(tmp_path, "    runs-on: ${{ matrix.os }}\n")
    assert states(tmp_path) == [("workflow", "test", "uncertain")]


def test_an_unresolvable_shell_without_a_fetch_is_still_absent(
    tmp_path: pathlib.Path,
) -> None:
    """An unknown shell cannot make an unfetched cache look warm."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    runs-on: ${{ matrix.os }}\n    steps:\n"
        "      - run: |\n          cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_a_prior_step_proves_the_order_whatever_the_shell(
    tmp_path: pathlib.Path,
) -> None:
    """Step sequencing does not depend on which shell the run blocks share."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    runs-on: ${{ matrix.os }}\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        "      - run: |\n          cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "before")]


def test_a_prefetch_after_the_test_step_is_not_proof(tmp_path: pathlib.Path) -> None:
    """Order is the order the steps are written in."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo test\n"
        "      - run: cargo fetch --locked\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_a_make_test_step_inherits_the_makefile_state(tmp_path: pathlib.Path) -> None:
    """`make test` runs whatever the Makefile's own ordering proves."""
    put(tmp_path, MAKEFILE, "test:\n\tcargo fetch --locked\n\tcargo test\n")
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n      - run: make test\n",
    )
    assert states(tmp_path) == [
        ("makefile", "test", "before"),
        ("workflow", "test", "before"),
    ]


def test_a_make_test_step_inherits_an_absent_makefile_state(
    tmp_path: pathlib.Path,
) -> None:
    """The Makefile's own ordering is the only evidence `make test` has."""
    put(tmp_path, MAKEFILE, "test:\n\tcargo test\n")
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n      - run: make test\n",
    )
    assert states(tmp_path) == [
        ("makefile", "test", "absent"),
        ("workflow", "test", "absent"),
    ]


def test_a_make_test_step_without_a_makefile_is_absent(tmp_path: pathlib.Path) -> None:
    """`make test` with no Makefile runs nothing this audit can read."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n      - run: make test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_an_expression_valued_command_is_uncertain(tmp_path: pathlib.Path) -> None:
    """A command the reader cannot see is not a command it can order."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n      - run: ${{ matrix.command }}\n",
    )
    assert states(tmp_path) == [("workflow", "test", "uncertain")]


def test_a_workflow_that_does_not_decode_is_a_problem(tmp_path: pathlib.Path) -> None:
    """A YAML error leaves every step in the file unknown."""
    put(tmp_path, WORKFLOW, "jobs:\n  test:\n   steps:\n  - run: [\n")
    problems: list[str] = []
    collect_entry_points(tmp_path, problems)
    assert any(WORKFLOW in problem for problem in problems)


def test_a_nextest_step_inherits_the_nextest_state(tmp_path: pathlib.Path) -> None:
    """A workflow's `cargo nextest run` is only as ordered as the config is."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
    )
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n      - run: cargo nextest run\n",
    )
    assert states(tmp_path) == [
        ("nextest", "default", "before"),
        ("workflow", "test", "before"),
    ]


def test_a_second_job_is_its_own_entry_point(tmp_path: pathlib.Path) -> None:
    """Each job runs its own steps under its own ordering."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  build:\n    steps:\n      - run: cargo fetch --locked\n"
        "  test:\n    steps:\n      - run: cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cargo test", [("workflow", "test", "absent")]),
        (
            "cargo nextest run",
            [("nextest", "default", "absent"), ("workflow", "test", "absent")],
        ),
        ("make test", [("workflow", "test", "absent")]),
    ],
)
def test_each_test_command_kind_is_recognised(
    command: str, expected: list[tuple[str, str, str]], tmp_path: pathlib.Path
) -> None:
    """The three recognised test commands are the ones the plan names.

    `cargo nextest run` also names the nextest profile it selects, because that
    profile's setup scripts are what could prove the cache warm. That fact
    records the profile's configuration, so it needs a configuration file to
    point at.
    """
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "package(foo)"\nsetup = "touch"\n'
        '[scripts.setup.touch]\ncommand = "touch marker"\n',
    )
    put(tmp_path, WORKFLOW, f"jobs:\n  test:\n    steps:\n      - run: {command}\n")
    assert states(tmp_path) == expected
