"""Read where a cargo command builds, and whether its arguments compile.

These are the reader's own cases, written against the recorded call list rather
than a whole checkout: the replay order (last write wins), the three places
cargo takes a target from, the binding resolution that turns a name into a
temporary directory, and the subcommand table behind NC-010's trigger.
"""

from __future__ import annotations

import pytest

from concordat.rules.nested_cargo_bindings import local_bindings
from concordat.rules.nested_cargo_compiles import compiles_state
from concordat.rules.nested_cargo_target import (
    DEDICATED,
    INHERITED,
    PRIVATE,
    SHARED,
    UNKNOWN,
    classify_value,
    read_target,
    target_signature,
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
        # The system temporary directory is shared by every run, so a fixed path
        # under it persists like any other; only a unique name makes it private.
        ('std::env::temp_dir().join("scenario")', "", DEDICATED),
        (
            'std::env::temp_dir().join(format!("s-{}", std::process::id()))',
            "",
            PRIVATE,
        ),
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
    """After the clear, `.env(key, value)` may set the target: it is unknown."""
    calls = [
        ("current_dir", "scratch.path()"),
        ("env_clear", ""),
        ("env", "key, value"),
    ]
    assert read_target(calls, TEMP_SOURCE).state == UNKNOWN


def test_an_opaque_environment_call_with_nothing_set_is_unknown() -> None:
    """`.envs(extra)` may set a private target, so inheriting one is not proven."""
    assert read_target([("envs", "extra")], "").state == UNKNOWN


def test_an_unseen_argument_list_may_carry_a_target_flag() -> None:
    """A conditional `.args(["--target-dir", dir])` may select a private target."""
    assert read_target([("unseen-arguments", "")], "").state == UNKNOWN


@pytest.mark.parametrize(
    ("payload", "state"),
    [
        # A literal list that does not name the flag cannot select a target.
        ('["orthohelp", "--out-dir", out.path()]', PRIVATE),
        # One that names it, a variable and a handoff may.
        ('["--target-dir", scratch.path()]', UNKNOWN),
        ("extra", UNKNOWN),
        ("", UNKNOWN),
    ],
    ids=["literal-list", "names-the-flag", "variable", "handoff"],
)
def test_an_unseen_argument_call_hides_a_target_only_when_it_may_carry_the_flag(
    payload: str, state: str
) -> None:
    """A conditional `.args([..])` is read for the flag; an opaque one may hold it."""
    calls = [env("CARGO_TARGET_DIR", "scratch.path()"), ("unseen-arguments", payload)]
    assert read_target(calls, TEMP_SOURCE).state == state


@pytest.mark.parametrize(
    ("payload", "state"),
    [
        # An unreadable first element may be the flag; a value after an option may not.
        ("[flag, scratch.path()]", UNKNOWN),
        ("flag", UNKNOWN),
        ('["--out-dir", out.path()]', PRIVATE),
        ('["--out-dir", out.path(), other]', UNKNOWN),
        # A switch takes no value, and an option given its value inline takes
        # no more, so what follows either may be `--target-dir=...`.
        ('["--release", flag]', UNKNOWN),
        ('["--out-dir=x", flag]', UNKNOWN),
        # Cargo has many switches; an option the reader does not know takes no value.
        ('["--timings", flag]', UNKNOWN),
        ('["--some-unknown-option", flag]', UNKNOWN),
        ('["--manifest-path", path]', PRIVATE),
    ],
    ids=[
        "unreadable-first",
        "bare-variable",
        "option-value",
        "two-unreadable",
        "after-a-switch",
        "after-an-inline-value",
        "after-timings",
        "after-an-unknown-option",
        "after-a-value-option",
    ],
)
def test_an_unreadable_element_is_a_value_only_after_an_option(
    payload: str, state: str
) -> None:
    """`[flag, path]` may select a target; `["--out-dir", path]` cannot."""
    calls = [env("CARGO_TARGET_DIR", "scratch.path()"), ("unseen-arguments", payload)]
    assert read_target(calls, TEMP_SOURCE).state == state


def test_an_unconditional_unreadable_argument_may_be_the_flag() -> None:
    """`.arg("build").arg(flag)` may pass `--target-dir=..`: the target is unknown."""
    calls = [
        env("CARGO_TARGET_DIR", "scratch.path()"),
        ("arg", '"build"'),
        ("arg", "flag"),
    ]
    assert read_target(calls, TEMP_SOURCE).state == UNKNOWN
    after_option = [
        env("CARGO_TARGET_DIR", "scratch.path()"),
        ("arg", '"--manifest-path"'),
        ("arg", "path"),
    ]
    assert read_target(after_option, TEMP_SOURCE).state == PRIVATE


def test_a_conditional_argument_leaves_the_previous_option_unknown() -> None:
    """An option spelled before a conditional call may already have its value.

    `args(["build", "--manifest-path"])`, a conditional `arg("Cargo.toml")` and
    then `arg(flag)`: if the branch ran, `flag` is free to be
    `--target-dir=<private path>`. Reading it as the manifest path's value would
    report the temporary variable as the target, so it must be unknown instead.
    """
    calls = [
        env("CARGO_TARGET_DIR", "scratch.path()"),
        ("args", '["build", "--manifest-path"]'),
        ("unseen-arguments", '"Cargo.toml"'),
        ("arg", "flag"),
    ]
    assert read_target(calls, TEMP_SOURCE).state == UNKNOWN


def test_the_conditional_argument_reset_does_not_reach_the_unconditional_case() -> None:
    """An option and its value written unconditionally are still read as such."""
    unconditional = [
        env("CARGO_TARGET_DIR", "scratch.path()"),
        ("args", '["build", "--manifest-path"]'),
        ("arg", '"Cargo.toml"'),
    ]
    assert read_target(unconditional, TEMP_SOURCE).state == PRIVATE


def test_a_conditional_call_after_the_separator_leaves_the_target_inherited() -> None:
    """After `--` the rest of the line is the program's, so no word is cargo's flag."""
    calls = [
        ("args", '["run", "--"]'),
        ("unseen-arguments", ""),
        ("arg", "flag"),
    ]
    assert read_target(calls, "").state == INHERITED


def test_the_target_signature_keeps_an_argument_the_reader_cannot_read() -> None:
    """A run that adds an unreadable argument may build somewhere else.

    An all-literal call cannot change where the command builds, so unchanged
    runs and harmless literal additions still collapse to one signature.
    """
    literal = ("args", '["build", "--frozen"]')
    assert target_signature([literal]) == ()
    assert target_signature([("arg", "flag")]) == (("arg", "flag"),)
    assert target_signature([("unseen-arguments", "")]) == (("unseen-arguments", ""),)
    assert target_signature([literal, ("arg", '"--release"')]) == ()


def test_an_escaped_variable_name_is_not_its_text() -> None:
    """A hexadecimal escape can spell `CARGO_TARGET_DIR`: the text is not the name."""
    calls = [("env", '"\\x43ARGO_TARGET_DIR", scratch.path()')]
    assert read_target(calls, TEMP_SOURCE).state == UNKNOWN


def test_a_computed_argument_is_not_an_exact_literal() -> None:
    """`"--target-".to_owned() + &format!(..)` builds the flag at run time."""
    payload = '["--timings".to_owned(), "--target-".to_owned() + &format!("dir={}", p)]'
    calls = [env("CARGO_TARGET_DIR", "scratch.path()"), ("unseen-arguments", payload)]
    assert read_target(calls, TEMP_SOURCE).state == UNKNOWN
    option = '["--out-dir".to_owned(), other]'
    assert (
        read_target(
            [env("CARGO_TARGET_DIR", "scratch.path()"), ("unseen-arguments", option)],
            TEMP_SOURCE,
        ).state
        == UNKNOWN
    )


def test_the_environment_cannot_settle_an_unseen_flag() -> None:
    """`--target-dir` beats the variables, so `env_clear()` or a write cannot help."""
    hidden = ("unseen-arguments", "[flag, x]")
    cleared = [hidden, ("env_clear", "")]
    assert read_target(cleared, TEMP_SOURCE).state == UNKNOWN
    set_after = [hidden, env("CARGO_TARGET_DIR", "scratch.path()")]
    assert read_target(set_after, TEMP_SOURCE).state == UNKNOWN
    named_after = [hidden, ("args", '["--target-dir", scratch.path()]')]
    assert read_target(named_after, TEMP_SOURCE).state == PRIVATE


@pytest.mark.parametrize(
    "signature",
    [
        "fn build(target: &Path)",
        "fn build((target,): (&Path,))",
        "fn build(Pair { target, .. }: Pair)",
        "fn build(mut target: PathBuf, flag: bool)",
        # A renamed field has its own colon, which does not end the pattern.
        "fn build(Pair { path: target }: Pair)",
        "fn build(Outer { inner: Pair { path: target } }: Outer)",
        "fn build(Pair::Both(target, _): Pair::Both)",
    ],
    ids=["simple", "tuple", "struct", "mut", "renamed-field", "nested", "path-pattern"],
)
def test_every_name_a_parameter_pattern_binds_is_unknown(signature: str) -> None:
    """A destructured parameter holds what the caller passed, not a caller's binding."""
    text = f'{signature} {{ cmd.env("CARGO_TARGET_DIR", target); }}\n'
    assert local_bindings(text)["target"] == "<Parameter>"


def test_an_unseen_argument_list_after_the_separator_is_the_programs() -> None:
    """After `--` the rest of the line is not cargo's, so no hidden word is a flag."""
    calls = [("args", '["run", "--"]'), ("unseen-arguments", "")]
    assert read_target(calls, "").state == INHERITED


@pytest.mark.parametrize(
    "slot",
    [
        'vec![("A", "b")',
        'format!("{}_TARGET_DIR", "CARGO")',
        "name",
    ],
    ids=["first-string-of-a-larger-expression", "format-macro", "variable"],
)
def test_only_an_exact_literal_names_a_target_variable(slot: str) -> None:
    """The first string in a slot is not its name, so the call is not read as one."""
    calls = [("env", f"{slot}, scratch.path()")]
    assert read_target(calls, TEMP_SOURCE).state == UNKNOWN


def test_words_after_the_separator_are_not_cargos_target_flag() -> None:
    """`cargo run -- ls --target-dir x` hands the flag to the program cargo runs."""
    calls = [
        ("args", '["run", "--bin", "tool", "--", "ls", "--target-dir"]'),
        ("arg", "scratch.path()"),
    ]
    assert read_target(calls, TEMP_SOURCE).state == INHERITED


def test_a_manifest_path_moves_the_default_target_out_of_the_directory() -> None:
    """With `--manifest-path`, a temporary working directory proves nothing."""
    base = [
        ("current_dir", "scratch.path()"),
        ("env_clear", ""),
        ("args", '["build", "--manifest-path", "../app/Cargo.toml"]'),
    ]
    assert read_target(base, TEMP_SOURCE).state == INHERITED
    without = [call for call in base if call[0] != "args"]
    assert read_target(without, TEMP_SOURCE).state == PRIVATE


def test_the_build_target_variable_yields_to_an_inherited_target_variable() -> None:
    """Cargo reads `CARGO_TARGET_DIR` first, and a parent may have set it."""
    lower = [env("CARGO_BUILD_TARGET_DIR", "scratch.path()")]
    assert read_target(lower, TEMP_SOURCE).state == UNKNOWN
    removed = [("env_remove", '"CARGO_TARGET_DIR"'), *lower]
    assert read_target(removed, TEMP_SOURCE).state == PRIVATE
    cleared = [("env_clear", ""), *lower]
    assert read_target(cleared, TEMP_SOURCE).state == PRIVATE


def test_an_opaque_call_after_the_removal_may_restore_the_higher_variable() -> None:
    """After `env_remove("CARGO_TARGET_DIR")`, an unseen `.env(k, v)` may set it."""
    calls = [
        ("env_remove", '"CARGO_TARGET_DIR"'),
        ("env", "key, value"),
        env("CARGO_BUILD_TARGET_DIR", "scratch.path()"),
    ]
    assert read_target(calls, TEMP_SOURCE).state == UNKNOWN


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
        ([("arg", '"t"')], "yes"),
        ([("args", '["b", "--release"]')], "yes"),
        ([("arg", '"fix"')], "yes"),
        # An unseen environment call cannot change the subcommand.
        ([("unseen", ""), ("arg", '"build"')], "yes"),
        ([("unseen-arguments", ""), ("arg", '"build"')], "unknown"),
        ([("unseen-arguments", "")], "unknown"),
        ([("unseen", "")], "no"),
        # An escaped literal is not its text: a hexadecimal escape can spell build.
        ([("arg", '"\\x62uild"')], "unknown"),
        ([], "no"),
    ],
)
def test_compiles_state_reads_the_subcommand(
    calls: list[tuple[str, str]], expected: str
) -> None:
    """Only a compiling subcommand, or a hidden one, makes a spawn a build."""
    assert compiles_state(calls) == expected
