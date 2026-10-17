"""Bound the documented helper path and run-request grammar independently.

Each generated draw exercises both an admitted control and rejected mutations.
The path oracle constructs the canonical repository-relative spelling, then
adds an absolute root, an alien name, or an explicit parent component. It does
not normalize paths or import the reader's path predicate. The request oracle
constructs options followed by the command separator, then makes a named
structural mutation; it does not count separators using production logic.

Every check uses the full documentation envelope and a fresh temporary
checkout. The bounded shell-literal components exclude slash, control bytes,
and Make interpolation, keeping generated cases inside the declared grammar.
The private checkout helper belongs only to these two properties; it is not a
shared parser or a rule-policy implementation.
"""

from __future__ import annotations

import itertools
import shlex
import string
import typing as typ
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import event, given, settings
from hypothesis import strategies as st

from concordat.rules.documentation_envelope import build_documentation_envelope

if typ.TYPE_CHECKING:
    from concordat.rules.interrogate_invocation import InterrogateRun

_COMPONENT = st.text(
    alphabet=string.ascii_letters + string.digits + "._- ", min_size=1, max_size=12
)
_LAUNCHER = st.sampled_from(["python", "python3"])
_RUN_OPTIONS: typ.Final = (
    ("--python", "3.13"),
    ("--group", "dev"),
    ("--extra", "docs"),
    ("--with", "interrogate==1.7.0"),
)


def _recorded_runs(words: list[str]) -> list[InterrogateRun]:
    """Read one literal command through the public envelope in an isolated tree.

    Returns
    -------
    list[InterrogateRun]
        The full recorded invocation facts for the generated command.
    """
    with TemporaryDirectory(prefix="concordat-interrogate-property-") as scratch:
        checkout = Path(scratch)
        (checkout / "Makefile").write_text(
            f"lint:\n\t{shlex.join(words)}\n", encoding="utf-8"
        )
        return build_documentation_envelope(checkout)["runs"]


# Bounded like the existing shell properties: each example invokes makeutil,
# so its process latency is not a useful per-example deadline assertion.
@settings(max_examples=30, deadline=None)
@given(
    components=st.lists(_COMPONENT, max_size=4),
    dot_depth=st.integers(min_value=0, max_value=3),
    launcher=_LAUNCHER,
)
def test_helper_paths_follow_the_documented_boundary(
    components: list[str], dot_depth: int, launcher: str
) -> None:
    """Canonical controls pass; absolute, alien and every parent position fail."""
    canonical = "./" * dot_depth + "scripts/uv_gate.py"
    suffix = ["run", "--", "interrogate", "-f", "100", "pkg"]
    runs = _recorded_runs([launcher, canonical, *suffix])

    assert len(runs) == 1
    assert runs[0]["resolved"] is True
    assert runs[0]["paths"] == ["pkg"]
    assert runs[0]["fail_under"] == "100"
    event("path: admitted canonical control")

    for position in range(len(components) + 1):
        parent = "/".join([*components[:position], "..", *components[position:]])
        assert _recorded_runs([launcher, parent, *suffix]) == []
    event("path: parent rejected at every generated position")

    alien_name = "alien-" + ("-".join(components) or "empty")
    for alien in ["/" + canonical, "scripts/" + alien_name, alien_name + "/uv_gate.py"]:
        assert _recorded_runs([launcher, alien, *suffix]) == []
    event("path: absolute and alien-name mutations rejected")


@settings(max_examples=30, deadline=None)
@given(
    groups=st.lists(st.sampled_from(_RUN_OPTIONS), max_size=3),
    extra_word=st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10),
    launcher=_LAUNCHER,
)
def test_helper_run_requests_require_the_documented_separator_boundary(
    groups: list[tuple[str, str]], extra_word: str, launcher: str
) -> None:
    """An admitted request passes and each structural mutation fails closed."""
    options = list(itertools.chain.from_iterable(groups))
    prefix = [launcher, "scripts/uv_gate.py", "run"]
    command = ["interrogate", "-f", "100", "pkg"]
    runs = _recorded_runs([*prefix, *options, "--", *command])

    assert len(runs) == 1
    assert runs[0]["resolved"] is True
    assert runs[0]["paths"] == ["pkg"]
    event("request: admitted terminal-separator control")

    wrong_command = "different-" + extra_word
    mutations = {
        "missing separator": options,
        "repeated separator": [*options, "--", "--"],
        "separator before another word": [*options, "--", wrong_command],
        "positional word before separator": [*options, wrong_command, "--"],
    }
    for name, arguments in mutations.items():
        assert _recorded_runs([*prefix, *arguments, *command]) == []
        event("request: " + name + " rejected")
