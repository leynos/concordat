"""Property tests for the Make expansion and the interrogate option reader.

Each property states an expectation computed directly from the generated
decisions (which names are assigned once, how long a chain of references is,
which words are paths), not from a second reading of the text under test, so a
defect in the readers cannot be mirrored here.
"""

from __future__ import annotations

import itertools
import typing as typ

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules.documentation_envelope import Rule, _reachable
from concordat.rules.interrogate_invocation import lex_segments, scan_options
from concordat.rules.makefile_expansion import expand, shown, single_assignments

if typ.TYPE_CHECKING:
    from concordat.rules.makefile_facts import MakeVariable

_NAMES = st.from_regex(r"[A-Z][A-Z0-9_]{0,8}", fullmatch=True)
_WORDS = st.from_regex(r"[a-z][a-z0-9]{0,6}", fullmatch=True)


def _variable(name: str, value: str) -> MakeVariable:
    """Return a plain assignment as `makeutil` reports it."""
    return typ.cast(
        "MakeVariable",
        {"name": name, "raw_value": value, "conditions": [], "define_block": False},
    )


@given(
    once=st.dictionaries(_NAMES, _WORDS, max_size=6),
    twice=st.lists(_NAMES, max_size=4, unique=True),
)
def test_only_names_assigned_exactly_once_have_a_value(
    once: dict[str, str], twice: list[str]
) -> None:
    """A name assigned twice has no one value, whatever else is assigned."""
    twice = [name for name in twice if name not in once]
    variables = list(itertools.starmap(_variable, once.items()))
    for name in twice:
        variables.extend([_variable(name, "a"), _variable(name, "b")])

    assert single_assignments(variables) == once


@given(base=_WORDS, words=st.lists(_WORDS, max_size=6))
def test_a_chain_of_references_expands_to_the_words_it_names(
    base: str, words: list[str]
) -> None:
    """Each link prepends its word to what the next link expands to."""
    values = {f"V{i}": f"$(V{i + 1}) {word}" for i, word in enumerate(words)}
    values[f"V{len(words)}"] = base

    result = expand("$(V0)", values)

    assert result.text.split() == [base, *reversed(words)]
    assert result.is_resolved is True


@given(size=st.integers(min_value=1, max_value=6))
def test_a_ring_of_references_ends_unresolved(size: int) -> None:
    """A cycle never settles, so the expansion gives up rather than looping."""
    values = {f"R{i}": f"$(R{(i + 1) % size})" for i in range(size)}

    assert expand("$(R0)", values).is_resolved is False


@given(parts=st.lists(st.sampled_from(["echo", "$$HOME", "a$$b", "x"]), max_size=8))
def test_shell_dollars_survive_expansion(parts: list[str]) -> None:
    """`$$` is a shell dollar: it is neither expanded nor lost."""
    text = " ".join(parts)

    assert shown(expand(text, {}).text) == text.replace("$$", "$")


@given(
    paths=st.lists(_WORDS, max_size=5),
    verbosity=st.integers(min_value=0, max_value=3),
    threshold=st.one_of(st.none(), st.integers(min_value=0, max_value=100)),
)
@settings(max_examples=60)
def test_options_and_paths_are_read_apart(
    paths: list[str], verbosity: int, threshold: int | None
) -> None:
    """Paths keep their order; verbose counts and the threshold is the last."""
    arguments = ["-v"] * verbosity
    if threshold is not None:
        arguments += ["--fail-under", str(threshold)]
    arguments += paths

    options = scan_options(arguments)

    assert options.paths == paths
    assert options.verbose == verbosity
    assert options.fail_under == (None if threshold is None else str(threshold))


_TARGETS = st.sampled_from([f"t{i}" for i in range(8)])


@given(
    edges=st.dictionaries(_TARGETS, st.lists(_TARGETS, max_size=4), max_size=8),
    has_lint=st.booleans(),
)
def test_reachability_is_the_transitive_closure_from_lint(
    edges: dict[str, list[str]], *, has_lint: bool
) -> None:
    """The walk reaches exactly what `lint` leads to, once each, in any graph."""
    graph = {**edges, **({"lint": ["t0"]} if has_lint else {})}
    index = {name: [Rule(deps, [])] for name, deps in graph.items()}
    expected: set[str] = set()
    pending = ["lint"]
    while pending:
        name = pending.pop()
        if name in graph and name not in expected:
            expected.add(name)
            pending.extend(graph[name])

    walk = _reachable(index, {})

    assert set(walk.names) == expected
    assert len(walk.names) == len(set(walk.names))
    assert walk.is_resolved is True


_COMMAND = st.lists(_WORDS, min_size=1, max_size=4)


@given(
    commands=st.lists(_COMMAND, min_size=1, max_size=4),
    separators=st.lists(
        st.sampled_from([";", "&&", "||", "|"]), min_size=3, max_size=3
    ),
)
def test_lexing_splits_at_every_separator_and_nowhere_else(
    commands: list[list[str]], separators: list[str]
) -> None:
    """Each generated simple command comes back as its own list of words."""
    text = ""
    for position, words in enumerate(commands):
        text += " ".join(words)
        if position < len(commands) - 1:
            text += f" {separators[position]} "

    assert lex_segments(text) == commands
