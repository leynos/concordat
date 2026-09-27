"""Independent property tests for the spelling-config-baseline policy.

Two clauses decide over a range rather than a list of shapes: PD-007 compares
the pinned release with the floor, and PD-011 applies `.gitignore`'s
last-match rule. Each property varies the compliant fixture in one fact, runs
the real policy through Conftest, and compares the verdict with a Python
oracle written from the published semantics (numeric release order; Git's
"the last matching pattern decides") rather than from the Rego.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import typing as typ
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from concordat.rules import runner

if typ.TYPE_CHECKING:
    from concordat.rules.spelling_envelope import SpellingEnvelope

_RULE_ID: typ.Final = "spelling-config-baseline"
_ENVELOPE_FIXTURE: typ.Final = (
    Path(__file__).parents[2]
    / "platform-standards/canon/lint-rules"
    / _RULE_ID
    / "fixtures/envelopes/compliant.json"
)
_FLOOR: typ.Final = (0, 1, 3)
_PINNED: typ.Final = "@v0.1.3"
_CACHE: typ.Final = ".typos-oxendict-base.json"

# Lines that do or do not match the JSON cache, in each of Git's spellings,
# and ones that must never count: a comment, a directory-only pattern, and
# the other cache file.
_GITIGNORE_LINES: typ.Final = (
    _CACHE,
    f"/{_CACHE}",
    f"**/{_CACHE}",
    f"!{_CACHE}",
    f"!/{_CACHE}",
    "*.json",
    "!*.json",
    ".typos-oxendict-base.*",
    f"# {_CACHE}",
    f"{_CACHE}/",
    ".venv/",
)


def _compliant() -> SpellingEnvelope:
    """Return a fresh copy of the compliant fixture envelope."""
    loaded = json.loads(_ENVELOPE_FIXTURE.read_text(encoding="utf-8"))
    return typ.cast("SpellingEnvelope", copy.deepcopy(loaded))


def _findings(envelope: SpellingEnvelope, rule_id: str) -> list[str]:
    """Return the messages of *rule_id*'s findings from a real Conftest run."""
    results = runner._invoke_conftest(_RULE_ID, envelope)
    return [
        finding.message
        for finding in runner._findings_from_results(results)
        if finding.rule_id == rule_id
    ]


def _pinned_to(release: str) -> SpellingEnvelope:
    """Return the compliant envelope with its gate pinned to *release*."""
    envelope = _compliant()
    makefile = envelope["makefile"]
    assert makefile is not None, "the compliant fixture must carry Make facts"
    for rule in makefile["rules"]:
        for recipe in rule["recipes"]:
            recipe["text"] = recipe["text"].replace(_PINNED, f"@{release}")
    return envelope


def _ignores_cache(lines: list[str]) -> bool:
    """Apply Git's last-match rule to the cache file, independently of Rego."""
    ignored = False
    for line in lines:
        if line.startswith("#") or line.endswith("/"):
            continue
        negated = line.startswith("!")
        pattern = line.removeprefix("!").removeprefix("**/").removeprefix("/")
        if fnmatch.fnmatchcase(_CACHE, pattern):
            ignored = not negated
    return ignored


@settings(max_examples=12, deadline=None)
@given(release=st.tuples(*(st.integers(min_value=0, max_value=12),) * 3))
def test_the_floor_is_numeric_release_order(release: tuple[int, int, int]) -> None:
    """A release is below the floor exactly when it sorts before it numerically.

    Components run past nine, so a string comparison (`v0.10.0` before
    `v0.9.0`) would disagree with the oracle.
    """
    tag = "v{}.{}.{}".format(*release)
    below = [msg for msg in _findings(_pinned_to(tag), "PD-007") if "floor" in msg]

    assert bool(below) == (release < _FLOOR), (tag, below)


@settings(max_examples=12, deadline=None)
@given(lines=st.lists(st.sampled_from(_GITIGNORE_LINES), max_size=5))
def test_the_last_matching_gitignore_line_decides(lines: list[str]) -> None:
    """PD-011 reports the JSON cache exactly when Git would track it."""
    envelope = _compliant()
    envelope["gitignore"] = {
        "path": ".gitignore",
        "lines": [*lines, ".typos-oxendict-base.toml"],
        "error": None,
    }
    reported = [msg for msg in _findings(envelope, "PD-011") if _CACHE in msg]

    assert bool(reported) != _ignores_cache(lines), (lines, reported)
