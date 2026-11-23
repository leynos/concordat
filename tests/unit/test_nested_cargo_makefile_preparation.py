"""Read a Makefile `test` target that prepares the nested builds before it tests.

A repository can declare where its nested compile work is paid for in the
Makefile instead of a nextest setup script (combobulate's `prepare-ui-gate`,
whitaker's preparation target). The cases pin which shapes count: a target that
compiles, reached before the test command through a prerequisite or a
success-gated command, and the shapes that look similar but prove nothing.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules import runner

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID = "nested-cargo-cold-build"

TRYBUILD_TEST = """\
#[test]
fn step_macros_compile() {
    let t = trybuild::TestCases::new();
    t.pass("tests/fixtures/ok.rs");
}
"""


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write one file, creating its directory."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def reported(root: pathlib.Path) -> bool:
    """Return whether the rule reports the trybuild test as unprepared."""
    result = runner.run_rule(RULE_ID, root)
    return any(
        (found.rule_id, found.path) == ("NC-010", "tests/ui.rs")
        for found in result.findings
    )


def checkout(root: pathlib.Path, makefile: str) -> None:
    """Write a checkout with one trybuild test and the given Makefile."""
    put(root, "Cargo.toml", '[package]\nname = "app"\n')
    put(root, "tests/ui.rs", TRYBUILD_TEST)
    put(root, "Makefile", makefile)


def test_a_checkout_with_no_makefile_preparation_is_reported(
    tmp_path: pathlib.Path,
) -> None:
    """The baseline: a trybuild test and no declared preparation."""
    checkout(tmp_path, "test:\n\tcargo nextest run\n")
    assert reported(tmp_path)


@pytest.mark.parametrize(
    "makefile",
    [
        # A prerequisite that builds.
        "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build --all-targets\n",
        # A prerequisite that builds through another target.
        (
            "test: prepare\n\tcargo nextest run\n\n"
            "prepare: warm\n\nwarm:\n\tcargo test --no-run\n"
        ),
        # An earlier recipe line that builds.
        "test:\n\tcargo build --tests\n\tcargo nextest run\n",
        # An earlier command on the same line, joined by `&&`.
        "test:\n\tcargo build --tests && cargo nextest run\n",
        # A recursive make of a preparing target, on its own line or gated.
        (
            "test:\n\t$(MAKE) prepare\n\tcargo nextest run\n\n"
            "prepare:\n\tcargo bench --no-run\n"
        ),
        "test:\n\tmake prepare && cargo test\n\nprepare:\n\tcargo nextest archive\n",
        # A plain variable names the cargo, and `$(shell ...)` elsewhere is opaque.
        (
            "CARGO ?= cargo\nFLAGS := $(shell uname -s)\n\n"
            "test: prepare\n\t$(CARGO) nextest run\n\nprepare:\n\t$(CARGO) build\n"
        ),
        # Unresolved flags do not stop a build being a build.
        "test: prepare\n\tcargo nextest run\n\nprepare:\n\tcargo build $(FLAGS)\n",
    ],
)
def test_a_target_that_compiles_before_the_tests_prepares_the_builds(
    makefile: str, tmp_path: pathlib.Path
) -> None:
    """A success-gated compile ahead of the test command declares the preparation."""
    checkout(tmp_path, makefile)
    assert not reported(tmp_path)


@pytest.mark.parametrize(
    "makefile",
    [
        # The build runs after the test, so it prepares nothing for it.
        "test:\n\tcargo nextest run\n\tcargo build\n",
        # `;` and `||` do not make the test wait on the build.
        "test:\n\tcargo build ; cargo nextest run\n",
        "test:\n\tcargo build || cargo nextest run\n",
        # An ignored error leaves the test to run on a failed build.
        "test:\n\t-cargo build\n\tcargo nextest run\n",
        # A conditional build may not run.
        'test:\n\tif [ -n "$(X)" ]; then cargo build; fi\n\tcargo nextest run\n',
        # A prerequisite that only checks tools, formats or lints.
        "test: tools\n\tcargo nextest run\n\ntools:\n\tcargo fmt --check\n",
        # A target only a different target reaches.
        "test:\n\tcargo nextest run\n\nprepare:\n\tcargo build\n",
        # A recursive make into another directory reads another Makefile.
        (
            "test:\n\tmake -C sub prepare && cargo nextest run\n\n"
            "prepare:\n\tcargo build\n"
        ),
        # `.ONESHELL` changes what a failed line does to the next.
        ".ONESHELL:\ntest:\n\tcargo build\n\tcargo nextest run\n",
    ],
)
def test_a_compile_that_does_not_gate_the_tests_prepares_nothing(
    makefile: str, tmp_path: pathlib.Path
) -> None:
    """A build the test does not wait on, or may skip, is not a declaration."""
    checkout(tmp_path, makefile)
    assert reported(tmp_path)


def test_a_preparation_that_loops_back_to_the_test_target_is_not_followed_forever(
    tmp_path: pathlib.Path,
) -> None:
    """A cycle of targets ends as not prepared rather than looping."""
    checkout(
        tmp_path,
        "test: prepare\n\tcargo nextest run\n\nprepare: other\n\nother: prepare\n",
    )
    assert reported(tmp_path)


def test_a_runner_probe_is_prepared_in_both_branches(tmp_path: pathlib.Path) -> None:
    """A line that picks its runner by probing has the build before both."""
    probe = (
        "CARGO ?= cargo\n"
        "TEST_CMD := $(if $(shell $(CARGO) nextest --version 2>/dev/null),"
        "nextest run,test)\n"
    )
    checkout(tmp_path, f"{probe}\ntest:\n\t$(CARGO) build && $(CARGO) $(TEST_CMD)\n")
    assert not reported(tmp_path)
