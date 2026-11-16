"""Read where a cargo command builds, and whether its arguments compile.

These are the reader's own cases, written against the recorded call list rather
than a whole checkout: the replay order (last write wins), the three places
cargo takes a target from, the binding resolution that turns a name into a
temporary directory, and the subcommand table behind NC-010's trigger.
"""

from __future__ import annotations

import pytest

from concordat.rules.nested_cargo_spawn import compiles_state
from concordat.rules.nested_cargo_target import (
    DEDICATED,
    INHERITED,
    PRIVATE,
    SHARED,
    UNKNOWN,
    classify_value,
    local_bindings,
    read_target,
)

TEMP_SOURCE = "let scratch = tempfile::tempdir().unwrap();\n"


def env(name: str, value: str) -> tuple[str, str]:
    """Return a recorded `.env(name, value)` call."""
    return ("env", f'"{name}", {value}')


@pytest.mark.parametrize(
    ("expression", "source", "state"),
    [
        ("scratch.path()", TEMP_SOURCE, PRIVATE),
        ("&tempfile::tempdir().unwrap().path()", "", PRIVATE),
        ('std::env::temp_dir().join("scenario")', "", PRIVATE),
        ("dir.join(Uuid::new_v4().to_string())", "", PRIVATE),
        (
            "target.as_str()",
            'let target = scratch.path().join("t");\n' + TEMP_SOURCE,
            PRIVATE,
        ),
        ("dir.path()", "fn f(dir: &TempDir) {}\n", PRIVATE),
        (
            'manifest.join("target/ui")',
            'let manifest = Utf8Path::new(env!("CARGO_MANIFEST_DIR"));\n',
            DEDICATED,
        ),
        (
            'root.join("../../target")',
            'let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));\n',
            SHARED,
        ),
        ('std::env::var("CARGO_TARGET_DIR").unwrap()', "", SHARED),
        ("metadata.target_directory", "", SHARED),
        ("scenario_target_dir(ctx)?", "", UNKNOWN),
        (
            "target_dir.as_str()",
            "let target_dir = scenario_target_dir(ctx)?;\n",
            UNKNOWN,
        ),
    ],
)
def test_a_target_value_is_classified_by_where_it_comes_from(
    expression: str, source: str, state: str
) -> None:
    """Only a visible origin decides; anything else is unknown, never a guess."""
    assert classify_value(expression, local_bindings(source)).state == state


def test_a_string_literal_that_spells_a_variable_is_not_substituted() -> None:
    """`"scratch"` is a path component, not the temporary directory of that name."""
    reading = classify_value('root.join("scratch")', local_bindings(TEMP_SOURCE))
    assert reading.state == UNKNOWN


def test_nothing_set_is_inherited() -> None:
    """A command that picks no target builds where its parent builds."""
    assert read_target([("arg", '"build"')], "").state == INHERITED


def test_the_last_write_to_the_variable_wins() -> None:
    """`Command::env` keeps the value given last for a name."""
    calls = [
        env("CARGO_TARGET_DIR", "scratch.path()"),
        env("CARGO_TARGET_DIR", "shared"),
    ]
    assert (
        read_target(
            calls, TEMP_SOURCE + 'let shared = std::env::var("CARGO_TARGET_DIR");\n'
        ).state
        == SHARED
    )


def test_env_clear_undoes_an_earlier_assignment() -> None:
    """A removal is an operation, so it replaces the earlier write."""
    calls = [env("CARGO_TARGET_DIR", "scratch.path()"), ("env_clear", "")]
    assert read_target(calls, TEMP_SOURCE).state == INHERITED


def test_the_flag_beats_the_variable() -> None:
    """`--target-dir` is read before `CARGO_TARGET_DIR`."""
    calls = [
        env("CARGO_TARGET_DIR", "shared"),
        ("arg", '"--target-dir"'),
        ("arg", "scratch.path()"),
    ]
    assert read_target(calls, TEMP_SOURCE).state == PRIVATE


def test_the_flag_in_an_args_list_is_read() -> None:
    """`.args(["--target-dir", dir])` is the same two words."""
    calls = [("args", '["--target-dir", scratch.path()]')]
    assert read_target(calls, TEMP_SOURCE).state == PRIVATE


def test_a_call_the_reader_cannot_see_through_makes_the_answer_unknown() -> None:
    """An unseen call may have replaced what the reader saw."""
    calls = [env("CARGO_TARGET_DIR", "scratch.path()"), ("unseen", "")]
    assert read_target(calls, TEMP_SOURCE).state == UNKNOWN


def test_an_unreadable_environment_list_is_a_call_the_reader_cannot_see_through() -> (
    None
):
    """`.envs(extra)` may set the target after a visible write."""
    calls = [env("CARGO_TARGET_DIR", "scratch.path()"), ("envs", "extra.iter()")]
    assert read_target(calls, TEMP_SOURCE).state == UNKNOWN


def test_a_temporary_working_directory_needs_both_variables_gone() -> None:
    """The review of RFC 0006: one removal leaves the other variable free."""
    base = [("current_dir", "scratch.path()")]
    one = [*base, ("env_remove", '"CARGO_TARGET_DIR"')]
    both = [*one, ("env_remove", '"CARGO_BUILD_TARGET_DIR"')]
    assert read_target(one, TEMP_SOURCE).state == INHERITED
    assert read_target(both, TEMP_SOURCE).state == PRIVATE


def test_an_opaque_environment_call_after_env_clear_hides_the_directory() -> None:
    """After the clear, `.env(key, value)` may set the target, so it is not private."""
    calls = [
        ("current_dir", "scratch.path()"),
        ("env_clear", ""),
        ("env", "key, value"),
    ]
    assert read_target(calls, TEMP_SOURCE).state == INHERITED


@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        ([("arg", '"build"')], "yes"),
        ([("args", '["--color", "never"]'), ("args", '["test", "--locked"]')], "yes"),
        ([("arg", '"+nightly"'), ("arg", '"clippy"')], "yes"),
        ([("arg", '"metadata"')], "no"),
        ([("arg", '"fetch"')], "no"),
        ([("args", '["config", "get", query]')], "no"),
        ([("args", '["package", "--list"]')], "no"),
        ([("args", '["package", "--allow-dirty"]')], "yes"),
        ([("args", '["--manifest-path", path, "build"]')], "unknown"),
        ([("arg", "subcommand")], "unknown"),
        ([("unseen", "")], "unknown"),
        ([], "no"),
    ],
)
def test_compiles_state_reads_the_subcommand(
    calls: list[tuple[str, str]], expected: str
) -> None:
    """Only a compiling subcommand, or a hidden one, makes a spawn a build."""
    assert compiles_state(calls) == expected
