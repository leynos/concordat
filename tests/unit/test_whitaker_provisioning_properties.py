"""Independent property tests for the whitaker-provisioning recognizers.

The policy classifies arbitrary command text, so a list of shapes cannot show
that comments and quoting decide correctly. Each property composes Makefile
lines from parts whose meaning is fixed by construction, runs the real policy
through Conftest, and compares the verdict with an oracle that never reads the
Rego: a line is a route exactly when its command part is one, whatever comment
follows it or quoted `#` precedes it.
"""

from __future__ import annotations

import copy
import json
import typing as typ
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules import runner

if typ.TYPE_CHECKING:
    from concordat.rules.whitaker_provisioning_envelope import ProvisioningEnvelope

_RULE_ID: typ.Final = "whitaker-provisioning"
_ENVELOPE_FIXTURE: typ.Final = (
    Path(__file__).parents[2]
    / "platform-standards/canon/lint-rules"
    / _RULE_ID
    / "fixtures/envelopes/compliant.json"
)

# Command parts whose classification is known: True is a route to Whitaker
# other than the action, False is a command the policy must leave alone.
_COMMANDS: typ.Final = (
    ("cargo install whitaker-installer", True),
    ("cargo binstall --no-confirm cargo-dylint", True),
    ("whitaker-installer --version", True),
    ("whitaker --all -- --all-targets", False),
    ("echo installing", False),
    ("make lint", False),
)

# What may precede a command on its line. A `#` inside quotes is text, and a
# lone apostrophe is prose, so neither may hide the command that follows.
_PREFIXES: typ.Final = ("", "echo 'a # b'; ", 'echo "x # y" && ', "echo it's; ")

# Comments name the tools freely: a comment is prose whatever it says.
_COMMENTS: typ.Final = (
    "",
    " # cargo install whitaker-installer",
    " # whitaker-installer is run by hand",
    " # see https://example.invalid/whitaker#fragment",
)


def _verdict(lines: list[str]) -> str:
    """Return the real policy's verdict on a Makefile made of *lines*."""
    loaded = json.loads(_ENVELOPE_FIXTURE.read_text(encoding="utf-8"))
    envelope = typ.cast("ProvisioningEnvelope", copy.deepcopy(loaded))
    text = "lint:\n" + "".join(f"\t{line}\n" for line in lines)
    # The fixture's workflow pins a stand-in revision the shipped parameters do
    # not list; only the Makefile is under test here.
    envelope["workflows"] = []
    envelope["scripts"] = [{"path": "Makefile", "text": text, "error": None}]
    results = runner._invoke_conftest(_RULE_ID, envelope)
    findings = runner._findings_from_results(results)
    is_noncompliant = any(f.verdict == "noncompliant" for f in findings)
    return "noncompliant" if is_noncompliant else "compliant"


@settings(max_examples=25, deadline=None)
@given(
    lines=st.lists(
        st.tuples(
            st.sampled_from(_PREFIXES),
            st.sampled_from(_COMMANDS),
            st.sampled_from(_COMMENTS),
        ),
        min_size=1,
        max_size=4,
    )
)
def test_a_line_is_a_route_exactly_when_its_command_is(
    lines: list[tuple[str, tuple[str, bool], str]],
) -> None:
    """Comments and quoted hashes never change what a command is."""
    text = [prefix + command + comment for prefix, (command, _), comment in lines]
    is_route = any(flag for _, (_, flag), _ in lines)

    assert _verdict(text) == ("noncompliant" if is_route else "compliant"), text


@settings(max_examples=15, deadline=None)
@given(
    comment=st.sampled_from([c for c in _COMMENTS if c]),
    prefix=st.sampled_from(_PREFIXES),
)
def test_a_whole_line_comment_is_never_a_route(comment: str, prefix: str) -> None:
    """A line that is only a comment, however it names the tools, is prose."""
    assert _verdict([f"#{comment.removeprefix(' #')}", prefix + "echo done"]) == (
        "compliant"
    )
