"""Tell a command that installs a prover from one that runs it, or only names it."""

from __future__ import annotations

import pytest

from concordat.rules.prover_commands import commands, is_installer, is_runner


@pytest.mark.parametrize(
    ("line", "installs", "runs"),
    [
        ("$(PROVER_TOOLS) verus install --repo-root .", True, False),
        (
            "uvx --from git+https://x/rust-prover-tools@abc prover-tools kani install",
            True,
            False,
        ),
        ("prover-tools z3 install --install-dir .verus/z3", True, False),
        ("cargo install kani-verifier --locked", True, False),
        ("cargo binstall kani-verifier", True, False),
        ("cargo kani setup", True, True),
        (
            "$(PROVER_TOOLS) verus run --repo-root . --proof-file verus/lib.rs",
            False,
            True,
        ),
        ("prover-tools kani exec -- cargo kani", False, True),
        ("cargo kani --harness h", False, True),
        ("verus --crate-type=lib verus/lib.rs", False, True),
        ("printf 'prover-tools verus install <redacted>' >&2", False, False),
        ("echo prover-tools verus install", False, False),
        ("cargo test --workspace", False, False),
        ("make verus", False, False),
    ],
)
def test_classification(*, line: str, installs: bool, runs: bool) -> None:
    """A command is an installer or a runner by its parsed argument vector."""
    (argv,) = commands(line)
    assert is_installer(argv) is installs
    assert is_runner(argv) is runs


def test_recipe_markers_comments_and_continuations() -> None:
    """Make recipe markers are dropped, comments skipped and continuations joined."""
    text = (
        "@echo hi\n# prover-tools verus install\n-$(PROVER_TOOLS) \\\n  verus install\n"
    )
    found = commands(text)
    assert [is_installer(argv) for argv in found] == [False, True]


def test_separators_split_simple_commands() -> None:
    """A pipeline or a conjunction is several commands, each judged alone."""
    found = commands("make lint && prover-tools verus install || true")
    assert [is_installer(argv) for argv in found] == [False, True, False]


def test_an_untokenizable_line_is_skipped() -> None:
    """An unbalanced quote does not raise."""
    assert commands("echo 'unterminated") == []
