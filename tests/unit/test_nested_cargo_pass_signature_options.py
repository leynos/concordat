"""Pin the build-affecting options a pass signature keeps, and the words it skips."""

from __future__ import annotations

import pytest

from concordat.rules.nested_cargo_pass_signature import read_invocation


def _signature(text: str):  # noqa: ANN202 - the type is the module's own
    """Return the signature of a command that must be a test or build."""
    invocation = read_invocation(text)
    assert invocation is not None, text
    return invocation.signature


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("cargo test --target-dir /x", "cargo test"),
        ("cargo test --config build.jobs=1", "cargo test"),
        ("cargo test -Z unstable-options", "cargo test"),
        ("cargo +nightly test", "cargo test"),
        ("cargo --config a=1 test", "cargo test"),
    ],
)
def test_options_that_change_the_build_are_part_of_the_signature(
    left: str, right: str
) -> None:
    """A different target directory, config, unstable flag or toolchain differs."""
    assert _signature(left) != _signature(right)


@pytest.mark.parametrize(
    "command",
    ["cargo build --help", "cargo test -h", "cargo nextest archive --help"],
)
def test_a_help_request_is_neither_a_build_nor_a_pass(command: str) -> None:
    """Asking for help compiles and runs nothing."""
    assert read_invocation(command) is None


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("cargo test --features $(A)", "cargo test --features $(B)"),
        ("cargo test --features $(A)", "cargo test"),
    ],
)
def test_an_unresolved_option_value_keeps_the_reference_in_the_signature(
    left: str, right: str
) -> None:
    """Two options that differ only in an unresolved value never compare equal."""
    assert _signature(left) != _signature(right)
    assert not _signature(left).is_resolved


def test_an_unresolved_global_option_value_is_not_resolved() -> None:
    """`cargo --config $(CFG) test` may differ from any other, so it is unresolved."""
    assert not _signature("cargo --config $(CFG) test").is_resolved
    assert not _signature("cargo +$(TOOLCHAIN) test").is_resolved


def test_the_runner_and_the_named_test_targets_are_read() -> None:
    """A pass records its runner and a run records the `--test` names it limits to."""
    invocation = read_invocation("cargo test --test ui --test=other -p app")
    assert invocation is not None
    assert (invocation.runner, invocation.test_targets) == ("cargo", ("other", "ui"))
    nextest = read_invocation("cargo nextest run -E 'test(x)'")
    assert nextest is not None
    assert (nextest.runner, nextest.test_targets) == ("nextest", ())


@pytest.mark.parametrize(
    ("command", "is_filtered"),
    [
        ("cargo test -- --test-threads 4", False),
        ("cargo test -- --skip slow --nocapture", False),
        ("cargo test -- --test-threads 4 some_test", True),
    ],
)
def test_a_libtest_option_value_is_not_a_test_name(
    command: str, *, is_filtered: bool
) -> None:
    """Only a bare word that is not an option's value filters the run."""
    invocation = read_invocation(command)
    assert invocation is not None
    assert invocation.is_filtered is is_filtered
