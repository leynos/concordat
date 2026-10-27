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

from concordat.errors import OperationalRuleError
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


@pytest.mark.parametrize(
    "comment",
    [
        "# now run; the tests",
        "# fetch first && then test",
        "# don't skip this",
        "# a | b",
        "# background & later",
    ],
    ids=["semicolon", "and", "apostrophe", "pipe", "background"],
)
def test_a_shell_comment_is_not_read_as_live_shell(comment: str) -> None:
    """A `#` comment is text the shell discards, not a command.

    Reading a comment as live shell both breaks the ordering proof — a `;` or
    `&&` inside it ends the command — and can swallow the lines after it, since
    an apostrophe in prose opens a quote the next apostrophe has to close.
    """
    script = f"cargo fetch --locked\n{comment}\ncargo test"
    assert gates(script) == [
        ("cargo fetch --locked", True),
        ("cargo test", False),
    ]


def test_a_comment_after_a_command_does_not_hide_it() -> None:
    """A trailing comment is stripped, so the command it trails stays whole."""
    assert gates("cargo fetch --locked  # warm the cache\ncargo test") == [
        ("cargo fetch --locked", True),
        ("cargo test", False),
    ]


def test_a_hash_inside_a_word_is_not_a_comment() -> None:
    """Only a `#` that starts a word opens a comment, as POSIX shells agree."""
    assert gates("echo a#b && cargo test") == [
        ("echo a#b", True),
        ("cargo test", False),
    ]


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


def test_a_global_option_before_the_subcommand_hides_neither_command(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo -q fetch` is still a fetch, and `cargo -q test` is still a test.

    Cargo accepts its global options in front of the subcommand. Reading the
    first non-toolchain word as the subcommand would miss both commands: the
    hidden fetch reports `absent` (a false NC-008), and the hidden test command
    removes the entry point entirely.
    """
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo -q fetch --locked\n\tcargo -q test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_global_locked_option_proves_the_fetch_is_locked(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo --locked fetch` is the same locked fetch as `cargo fetch --locked`."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo --locked fetch\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_value_carrying_global_option_does_not_become_the_subcommand(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo --config KEY=VALUE fetch` names `fetch`, not the config value."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo --config net.offline=true fetch --locked\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_global_option_before_nextest_keeps_the_run_subcommand(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo --color=always nextest run` still selects the nextest profile."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.ci.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
    )
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tcargo --color=always nextest run --profile ci\n",
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


@pytest.mark.parametrize(
    "prefix",
    [
        "env -u CARGO_NET_OFFLINE",
        "env --unset CARGO_NET_OFFLINE",
        "env -i",
        "env -C .",
        "env --",
    ],
)
def test_an_env_prefixed_locked_fetch_is_proof(
    prefix: str, tmp_path: pathlib.Path
) -> None:
    """`env` options do not decide which tool runs; the word after them does.

    whitaker's `fetch` target runs `env -u CARGO_NET_OFFLINE $(CARGO) fetch
    --locked`, which is the mitigation this check exists to reward. Reading the
    option as the command word hides the fetch and reports a cold cache.
    """
    put(
        tmp_path,
        MAKEFILE,
        f"test:\n\t{prefix} cargo fetch --locked\n\tcargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "before")]


def test_an_env_prefixed_test_command_is_still_a_test(tmp_path: pathlib.Path) -> None:
    """The same transparency applies to the test command itself."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tenv -u CARGO_NET_OFFLINE cargo test\n",
    )
    assert makefile_state(tmp_path) == [("test", "absent")]


def test_a_cargo_nextest_binary_selects_the_nextest_profile(
    tmp_path: pathlib.Path,
) -> None:
    """`cargo-nextest nextest run` is `cargo nextest run` under another name.

    whitaker binds `TEST_RUNNER ?= $(CARGO_NEXTEST) nextest run` and its
    `CARGO_NEXTEST` is the standalone binary, so the profile's setup scripts
    are the only evidence that could prove the cache warm.
    """
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo-nextest nextest run\n")
    assert makefile_state(tmp_path) == [("test", "before")]


@pytest.mark.parametrize("command", ["cargo ntr", "cargo-nextest ntr"])
def test_the_ntr_shortcut_is_the_same_test_command(
    command: str, tmp_path: pathlib.Path
) -> None:
    """`ntr` is nextest's own shortcut for `nextest run`."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.ci.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
    )
    put(tmp_path, MAKEFILE, f"test:\n\t{command} --profile ci\n")
    assert makefile_state(tmp_path) == [("test", "before")]


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


def test_an_unreadable_test_command_is_uncertain_not_absent(
    tmp_path: pathlib.Path,
) -> None:
    """A `test` target whose command cannot be read is not a target with no test.

    whitaker's `test` recipe puts an unresolved `$(if ...)` before its runner,
    so the command word is unknown. Reporting no entry point lets a workflow
    step that delegates to it inherit `absent`, which would be a false NC-008
    against the very repository that added the prefetch. The honest answer is
    that the target exists and its ordering cannot be read.
    """
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\t$(DYLINT_DRIVER_PATH_UNSET) $(TEST_RUNNER) --locked\n",
    )
    assert makefile_state(tmp_path) == [("test", "uncertain")]


def test_an_unreadable_test_target_names_its_reason(tmp_path: pathlib.Path) -> None:
    """The fact carries the sentence a reviewer needs, not just the state."""
    put(
        tmp_path,
        MAKEFILE,
        "test:\n\tset -eu; \\\n"
        "\t$(DYLINT_DRIVER_PATH_UNSET) RUSTFLAGS=$(RUST_FLAGS) $(TEST_RUNNER); \\\n"
        "\t$(MAKE) workflow-test\n",
    )
    problems: list[str] = []
    facts = collect_entry_points(tmp_path, problems)
    makefile = [fact for fact in facts if fact["kind"] == "makefile"]
    assert [(fact["name"], fact["state"]) for fact in makefile] == [
        ("test", "uncertain")
    ]
    assert makefile[0]["reason"]


def test_a_refused_makefile_leaves_a_delegating_step_uncertain(
    tmp_path: pathlib.Path,
) -> None:
    """A Makefile the loader refuses is not a Makefile that runs nothing.

    `make test` in a workflow inherits the Makefile's state. Treating a refused
    Makefile as an absent one reports the step as a cold entry point, and the
    policy then reports NC-008 from a Makefile the reader never saw. The
    Makefile itself still contributes no fact, so an unparseable file is not
    reported as an entry point it might not contain.
    """
    put(tmp_path, MAKEFILE, "test:\n\tcargo test\nendif\n")
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n      - run: make test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "uncertain")]


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


def test_a_setup_command_is_one_argv_not_a_shell_script(
    tmp_path: pathlib.Path,
) -> None:
    """Nextest executes the setup `command` directly, without a shell.

    `echo hi && cargo fetch --locked` runs `echo` with the remaining words as
    its arguments. No fetch happens, so reporting `before` would claim a warm
    cache from a command that cannot warm one.
    """
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "echo hi && cargo fetch --locked"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    assert makefile_state(tmp_path) == [("test", "uncertain")]


def test_a_setup_command_with_a_semicolon_is_not_a_fetch(
    tmp_path: pathlib.Path,
) -> None:
    """A `;` is a literal argument to the first word, not a separator."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked ; touch marker"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run\n")
    assert makefile_state(tmp_path) == [("test", "uncertain")]


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


def test_a_custom_profile_inherits_the_default_setup_scripts(
    tmp_path: pathlib.Path,
) -> None:
    """A custom profile inherits `default`, so `--profile ci` sees its fetch.

    Cargo-nextest resolves a custom profile's configuration through its
    `inherits` chain and, when it names no parent, through `default`. Reading
    only the selected profile's own `scripts` misses a fetch declared on
    `default` and reports a false cold cache.
    """
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n'
        "[profile.ci]\nretries = 2\n",
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run --profile ci\n")
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_profile_resolves_its_named_parent_chain(
    tmp_path: pathlib.Path,
) -> None:
    """`inherits` selects the parent, and the parent's parent is read too."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n'
        '[profile.base]\ninherits = "default"\n'
        '[profile.ci]\ninherits = "base"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run --profile ci\n")
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_profile_with_its_own_script_and_a_parent_script_is_before(
    tmp_path: pathlib.Path,
) -> None:
    """The chain's scripts are cumulative, and one fetch anywhere is enough."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n'
        '[[profile.ci.scripts]]\nfilter = "package(foo)"\nsetup = "warm"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run --profile ci\n")
    assert makefile_state(tmp_path) == [("test", "before")]


def test_a_profile_whose_parent_is_unknown_is_uncertain(
    tmp_path: pathlib.Path,
) -> None:
    """A parent nextest cannot resolve is a configuration it refuses."""
    put(
        tmp_path,
        NEXTEST_CONFIG,
        '[profile.ci]\ninherits = "missing"\n',
    )
    put(tmp_path, MAKEFILE, "test:\n\tcargo nextest run --profile ci\n")
    assert makefile_state(tmp_path) == [("test", "uncertain")]


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


def test_a_conditional_fetch_step_is_not_proof(tmp_path: pathlib.Path) -> None:
    """A fetch step that runs only on some runs warms nothing on the others.

    `if: runner.os == 'Linux'` skips the fetch entirely on the other runners,
    and the test step that follows then starts against a cold cache. Reading
    the step as proof reports `before` from a fetch that never ran.
    """
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        "        if: runner.os == 'Linux'\n"
        "      - run: cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_an_explicit_success_fetch_step_is_proof(tmp_path: pathlib.Path) -> None:
    """`if: success()` is GitHub's own default, so it still proves the order."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        "        if: success()\n"
        "      - run: cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "before")]


def test_an_upper_case_success_fetch_step_is_proof(tmp_path: pathlib.Path) -> None:
    """GitHub matches expression function names without regard to case.

    `Success()` is the same condition as `success()`, so a fetch step guarded
    by it still runs on every run and still warms the cache.
    """
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        "        if: Success()\n"
        "      - run: cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "before")]


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


def test_errexit_spelled_as_a_long_option_is_still_fail_fast(
    tmp_path: pathlib.Path,
) -> None:
    """`-o errexit` is the long spelling of `-e` and stops the script too.

    Scanning only for the letter `e` in a short option misses it, so a
    workflow written this way would be read as running the test after a
    failed fetch and reported absent when it is in fact gated.
    """
    _multi_line(
        tmp_path,
        "    runs-on: ubuntu-latest\n"
        "    defaults:\n      run:\n        shell: bash -o errexit {0}\n",
    )
    assert states(tmp_path) == [("workflow", "test", "before")]


def test_another_long_option_is_not_read_as_errexit(
    tmp_path: pathlib.Path,
) -> None:
    """`-o pipefail` sets an option by long name but not the one that stops.

    Without this case, treating any `-o` as fail-fast would pass the test
    above while wrongly claiming the fetch gates the test here.
    """
    _multi_line(
        tmp_path,
        "    runs-on: ubuntu-latest\n"
        "    defaults:\n      run:\n        shell: bash -o pipefail {0}\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


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


def test_a_non_fail_fast_workflow_default_is_not_dropped(
    tmp_path: pathlib.Path,
) -> None:
    """A workflow default that is *less* strict than the runner's still applies.

    Ubuntu's runner default shell is already fail-fast, so a workflow default
    of `bash -e {0}` looks right either way. `bash {0}` is the discriminating
    case: reading the workflow's `defaults` block as though it were itself
    that block finds nothing, falls back to the runner default, and wrongly
    claims the fetch gates the test.
    """
    put(
        tmp_path,
        WORKFLOW,
        "defaults:\n  run:\n    shell: bash {0}\n"
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: |\n"
        "          cargo fetch --locked\n          cargo test\n",
    )
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_a_windows_runner_does_not_fail_fast(tmp_path: pathlib.Path) -> None:
    """The Windows default is `pwsh`, which checks only the last native status."""
    _multi_line(tmp_path, "    runs-on: windows-latest\n")
    assert states(tmp_path) == [("workflow", "test", "absent")]


def test_an_unrecognised_string_runner_label_is_unknown(
    tmp_path: pathlib.Path,
) -> None:
    """`runs-on: my-build-box` names no platform, so its default shell is unknown.

    A self-hosted Windows runner with a custom label defaults to `pwsh`, which
    does not stop at a failed fetch. Reading the label as a POSIX runner would
    report the multi-line block as `before`, a false pass.
    """
    _multi_line(tmp_path, "    runs-on: my-build-box\n")
    assert states(tmp_path) == [("workflow", "test", "uncertain")]


def test_a_self_hosted_string_runner_label_is_unknown(
    tmp_path: pathlib.Path,
) -> None:
    """`runs-on: self-hosted` is not a platform label either."""
    _multi_line(tmp_path, "    runs-on: self-hosted\n")
    assert states(tmp_path) == [("workflow", "test", "uncertain")]


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


# --- containment -----------------------------------------------------------


def test_a_makefile_that_links_outside_the_checkout_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A linked `Makefile` would carry an outside target into the report.

    `_read_workflow` already refuses a workflow that resolves outside the
    checkout, but the Makefile and the nextest configuration are read before
    any containment check. A repository could aim either at a readable file
    elsewhere on the machine and have its test target reported as this
    repository's entry point.
    """
    outside = tmp_path / "outside-Makefile"
    outside.write_text("test:\n\tcargo test\n", encoding="utf-8")
    checkout = tmp_path / "repo"
    checkout.mkdir()
    (checkout / MAKEFILE).symlink_to(outside)

    problems: list[str] = []
    with pytest.raises(OperationalRuleError, match="outside the checkout"):
        collect_entry_points(checkout, problems)


def test_a_nextest_config_that_links_outside_the_checkout_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The nextest configuration is a policy input and is read through the guard.

    Its setup scripts decide whether an entry point is `before`, so reading it
    from outside the checkout would let an outside file change this
    repository's verdict.
    """
    outside = tmp_path / "outside-nextest.toml"
    outside.write_text(
        '[[profile.default.scripts]]\nfilter = "all()"\nsetup = "warm"\n'
        '[scripts.setup.warm]\ncommand = "cargo fetch --locked"\n',
        encoding="utf-8",
    )
    checkout = tmp_path / "repo"
    (checkout / ".config").mkdir(parents=True)
    (checkout / NEXTEST_CONFIG).symlink_to(outside)
    put(checkout, MAKEFILE, "test:\n\tcargo nextest run\n")

    problems: list[str] = []
    with pytest.raises(OperationalRuleError, match="outside the checkout"):
        collect_entry_points(checkout, problems)


def test_a_makefile_inside_the_checkout_is_still_read(tmp_path: pathlib.Path) -> None:
    """Containment refuses outside paths without refusing legitimate ones."""
    put(tmp_path, MAKEFILE, "test:\n\tcargo test\n")

    assert makefile_state(tmp_path) == [("test", "absent")]


# --- `if:` conditions on the test step -------------------------------------


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        # Always true once the fetch has failed, so the step runs anyway.
        ("${{ failure() || success() }}", "absent"),
        ("${{ github.ref == 'refs/heads/main' || always() }}", "absent"),
        ("${{ failure() }}", "absent"),
        # Expression function names are case-insensitive, so an upper-case
        # spelling names the same status function.
        ("${{ ALWAYS() }}", "absent"),
        ("${{ SUCCESS() }}", "before"),
        # Depends on a fact the reader does not have: the branch, or whether
        # the run is being cancelled.
        ("${{ success() || github.ref == 'refs/heads/main' }}", "uncertain"),
        ("${{ cancelled() || success() }}", "uncertain"),
        # A conjunction with `success()` cannot run after a failed fetch.
        ("${{ success() && runner.os == 'Linux' }}", "before"),
        # A status function's name inside a string literal is text the
        # condition compares, not a call, so the implicit guard stands.
        ("${{ github.event.name == 'always()' }}", "before"),
    ],
    ids=[
        "failure-or-success",
        "branch-or-always",
        "failure",
        "upper-always",
        "upper-success",
        "success-or-branch",
        "cancelled-or-success",
        "success-and-platform",
        "status-call-in-literal",
    ],
)
def test_a_test_step_is_judged_by_whether_it_can_run_after_a_failed_fetch(
    condition: str, expected: str, tmp_path: pathlib.Path
) -> None:
    """A status function in the `if:` replaces GitHub's implicit `success()`.

    GitHub adds its default `success()` check only when the condition names no
    status function. Once one appears, what the step allows follows from the
    expression: `failure() || success()` is true either way, while
    `success() || github.ref == 'refs/heads/main'` runs on `main` after a failed
    fetch and is undecidable elsewhere. Reporting any of them `before` claims a
    warm cache from a fetch that may never have run.
    """
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        f"      - run: cargo test\n        if: {condition}\n",
    )

    assert states(tmp_path) == [("workflow", "test", expected)]


def test_a_test_step_with_a_non_status_condition_stays_before(
    tmp_path: pathlib.Path,
) -> None:
    """A condition that names no status function still gets the implicit check.

    `runner.os == 'Linux'` may skip the step, but when the step does run GitHub
    has already established that every earlier step succeeded. Reporting it
    `absent` would discard a real fetch.
    """
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        "      - run: cargo test\n        if: runner.os == 'Linux'\n",
    )

    assert states(tmp_path) == [("workflow", "test", "before")]


def test_a_bare_success_condition_still_proves_the_order(
    tmp_path: pathlib.Path,
) -> None:
    """`success()` on its own is exactly the implicit check, spelled out."""
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked\n"
        "      - run: cargo test\n        if: ${{ success() }}\n",
    )

    assert states(tmp_path) == [("workflow", "test", "before")]


# --- expressions in the test step's own command ----------------------------


def test_a_same_step_fetch_does_not_prove_order_across_an_expression(
    tmp_path: pathlib.Path,
) -> None:
    """An expression can supply the `|| true` that hides a failed fetch.

    `cargo fetch --locked ${{ inputs.extra }} && cargo test` reads as gated once
    the expression is removed, but `inputs.extra` may be `|| true`, which makes
    the fetch's failure non-fatal and lets the test run anyway.
    """
    put(
        tmp_path,
        WORKFLOW,
        "jobs:\n  test:\n    steps:\n"
        "      - run: cargo fetch --locked ${{ inputs.extra }} && cargo test\n",
    )

    assert states(tmp_path) == [("workflow", "test", "uncertain")]
