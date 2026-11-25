"""Read one cargo test or build command into the signature a preparation shares.

A preparation warms a pass only when both build the same thing, so the cases pin
what makes two commands equal (the same packages, features, target, profile and
build-affecting environment) and what does not (a nextest filter, a job count,
the order of features).
"""

from __future__ import annotations

import pytest

from concordat.rules.nested_cargo_pass_signature import (
    Action,
    has_unresolved_command_word,
    read_invocation,
)


def signature(text: str):  # noqa: ANN201 - the type is the module's own
    """Return the signature of a command that must be a test or build."""
    invocation = read_invocation(text)
    assert invocation is not None, text
    return invocation.signature


@pytest.mark.parametrize(
    ("command", "action"),
    [
        ("cargo nextest run", Action.RUN),
        ("cargo nextest run --no-run", Action.COMPILE),
        ("cargo-nextest nextest run --workspace", Action.RUN),
        ("cargo ntr", Action.RUN),
        ("cargo nextest archive --archive-file out.tar.zst", Action.COMPILE),
        ("cargo test", Action.RUN),
        ("cargo +nightly test --locked", Action.RUN),
        ("cargo test --no-run", Action.COMPILE),
        ("cargo build --all-targets", Action.COMPILE),
        ("cargo bench --no-run", Action.COMPILE),
        # A command split off an `if` line leads with the keyword that opens it.
        ("then cargo nextest run", Action.RUN),
        ("else RUSTFLAGS=-Dwarnings cargo test", Action.RUN),
        ("RUSTFLAGS='-D warnings' cargo test", Action.RUN),
    ],
)
def test_a_command_is_read_as_a_run_or_a_compile(command: str, action: Action) -> None:
    """Test commands run, build commands compile, and `--no-run` compiles."""
    invocation = read_invocation(command)
    assert invocation is not None
    assert invocation.action is action


@pytest.mark.parametrize(
    "command",
    ["cargo fmt --check", "cargo clippy", "cargo metadata", "make test", "echo hi", ""],
)
def test_any_other_command_is_neither(command: str) -> None:
    """Formatting, linting and unrelated commands are not passes or preparations."""
    assert read_invocation(command) is None


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("cargo test --all-features", "cargo test --all-features --locked"),
        ("cargo nextest run -j 4", "cargo nextest run -j 8"),
        ("cargo nextest run --profile ci", "cargo nextest run"),
        ("cargo test --features a,b", "cargo test --features b --features a"),
        ("cargo test -p a -p b", "cargo test --package b --package a"),
        ("cargo nextest run", "cargo nextest run -E 'test(x)' --no-fail-fast"),
        ("cargo test", "cargo nextest run"),
        ("cargo test --target x86_64", "cargo test --target=x86_64"),
        ("cargo test", "cargo test -- --nocapture"),
    ],
)
def test_commands_that_build_the_same_thing_have_equal_signatures(
    left: str, right: str
) -> None:
    """Options that change what runs, not what builds, leave the signature alone."""
    assert signature(left) == signature(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("cargo test --all-features", "cargo test --no-default-features"),
        ("cargo test --all-features", "cargo test"),
        ("cargo test -p a", "cargo test -p b"),
        ("cargo test -p a", "cargo test --workspace"),
        ("cargo test --workspace", "cargo test --workspace --exclude a"),
        ("cargo test", "cargo test --release"),
        ("cargo test --target a", "cargo test --target b"),
        ("cargo test", "RUSTFLAGS='-D warnings' cargo test"),
        ("RUSTFLAGS='-D warnings' cargo test", "RUSTFLAGS='-C x' cargo test"),
        ("cargo test", "CARGO_TARGET_DIR=/x cargo test"),
        ("cargo nextest run", "cargo nextest run --cargo-profile dev-fast"),
        ("cargo test --manifest-path a/Cargo.toml", "cargo test"),
    ],
)
def test_commands_that_build_something_different_have_unequal_signatures(
    left: str, right: str
) -> None:
    """A differing package set, feature set, profile, target or flag is a mismatch."""
    assert signature(left) != signature(right)


def test_an_environment_variable_that_does_not_build_is_ignored() -> None:
    """Only the variables cargo builds with are part of the signature."""
    assert signature("FOO=1 cargo test") == signature("cargo test")


def test_an_env_wrapper_carries_its_assignments() -> None:
    """`env RUSTFLAGS=.. cargo test` sets the flags like a plain prefix does."""
    assert signature("env RUSTFLAGS=-Dwarnings cargo test") == signature(
        "RUSTFLAGS=-Dwarnings cargo test"
    )


@pytest.mark.parametrize(
    ("command", "is_filtered"),
    [
        ("cargo nextest run", False),
        ("cargo nextest run -E 'test(x)'", True),
        ("cargo nextest run --filterset 'test(x)'", True),
        ("cargo nextest run --test ui", True),
        ("cargo nextest run my_test", True),
        ("cargo test nested_fixture_preparation", True),
        ("cargo test --lib", True),
        ("cargo test -- some_test", True),
        ("cargo test --all-features -j 4", False),
        ("cargo nextest run --profile ci --no-fail-fast", False),
    ],
)
def test_a_run_restricted_to_named_tests_is_filtered(
    command: str, *, is_filtered: bool
) -> None:
    """A test name, a test target or a filter set narrows the run."""
    invocation = read_invocation(command)
    assert invocation is not None
    assert invocation.is_filtered is is_filtered


def test_a_compile_is_never_filtered() -> None:
    """Only a real run can be a filtered one."""
    invocation = read_invocation("cargo nextest run --no-run -E 'test(x)'")
    assert invocation is not None
    assert not invocation.is_filtered


def test_a_make_reference_in_the_flags_is_opaque_but_comparable() -> None:
    """The same unresolved reference on both sides leaves the commands equal."""
    left = signature("cargo test $(FLAGS)")
    right = signature("cargo test --locked $(FLAGS)")
    assert not left.is_resolved
    assert left == right
    assert left != signature("cargo test $(OTHER)")


def test_an_option_the_reader_does_not_know_is_opaque() -> None:
    """An unknown option may carry a value or change the build, so it is kept."""
    assert not signature("cargo test --mystery").is_resolved
    assert signature("cargo test --mystery") != signature("cargo test")


def test_a_reference_holding_quotes_and_pipes_stays_one_word() -> None:
    """`$(shell cut -d' ' -f1)` closes where its parentheses balance."""
    text = (
        "RUSTUP_TOOLCHAIN=\"$(shell rustup show | cut -d' ' -f1)\" "
        "cargo test --all-features"
    )
    found = signature(text)
    assert found.features == ("--all-features",)
    assert found == signature(text)


def test_a_command_word_that_is_a_reference_is_unresolved() -> None:
    """`$(RUNNER) --all-features` may be a test runner the reader cannot see."""
    assert has_unresolved_command_word("$(RUNNER) --all-features")
    assert not has_unresolved_command_word("cargo test")
    assert not has_unresolved_command_word("")
