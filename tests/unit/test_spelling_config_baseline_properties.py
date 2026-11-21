"""Independent property tests for the spelling-config-baseline policy.

Numeric triples use non-negative integers with no strategy upper bound, but
twelve batches remain sampled evidence rather than an unbounded proof. Named
examples include values beyond machine integer precision.

Three clauses decide over a range rather than a list of shapes: PD-007 compares
the pinned release with the floor, PD-013 selects published text, and PD-011
applies `.gitignore`'s
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
from unittest import mock

from hypothesis import example, given, settings
from hypothesis import strategies as st

from concordat.rules import runner
from tests.unit.conftest_batch_support import invoke_conftest_batch

if typ.TYPE_CHECKING:
    from concordat.rules.spelling_envelope import SpellingEnvelope

_RULE_ID: typ.Final = "spelling-config-baseline"
_ENVELOPE_FIXTURE: typ.Final = (
    Path(__file__).parents[2]
    / "platform-standards/canon/lint-rules"
    / _RULE_ID
    / "fixtures/envelopes/compliant.json"
)
_RELEASES = st.tuples(*(st.integers(min_value=0),) * 3)
type Release = tuple[int, int, int]
_PINNED: typ.Final = "@v0.1.3"
_CACHE: typ.Final = ".typos-oxendict-base.json"

# Lines that do or do not match the JSON cache, in each of Git's spellings,
# and ones that must never count: a comment, a directory-only pattern, a brace
# list (Git reads braces literally), and the other cache file.
_GITIGNORE_LINES: typ.Final = (
    _CACHE,
    f"/{_CACHE}",
    f"**/{_CACHE}",
    f"!{_CACHE}",
    f"!/{_CACHE}",
    "*.json",
    "!*.json",
    ".typos-oxendict-base.*",
    ".typos-oxendict-base.{json,toml}",
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


def _tag(release: Release) -> str:
    """Render a generated tuple without parsing the policy's input grammar."""
    return "v{}.{}.{}".format(*release)


def _batch_findings(
    envelopes: list[SpellingEnvelope], overrides: dict[str, object]
) -> list[tuple[runner.Finding, ...]]:
    """Evaluate real policy batches with generated parameter defaults."""
    parameters = runner._rule_parameters(runner._rule_package_dir(_RULE_ID))
    parameters.update(overrides)
    with mock.patch.object(runner, "_rule_parameters", return_value=parameters):
        batches = invoke_conftest_batch(_RULE_ID, envelopes)
    return [runner._findings_from_results(results) for results in batches]


@settings(max_examples=12, deadline=None)
@example(floor=(0, 9, 0), releases=[(0, 10, 0), (0, 8, 9), (0, 9, 0)])
@example(
    floor=(2**53 + 1, 0, 0),
    releases=[(2**53, 0, 0), (2**64 + 1, 0, 0), (10**100, 0, 0)],
)
@given(floor=_RELEASES, releases=st.lists(_RELEASES, min_size=4, max_size=4))
def test_bare_and_explicit_floors_follow_numeric_release_order(
    floor: Release, releases: list[Release]
) -> None:
    """Both reference forms agree with independent numeric tuple ordering."""
    envelopes = [
        _pinned_to(prefix + _tag(release))
        for release in releases
        for prefix in ("", "refs/tags/")
    ]
    batches = _batch_findings(envelopes, {"builder_floor": _tag(floor)})
    for release, bare, explicit in zip(
        releases, batches[::2], batches[1::2], strict=True
    ):
        for findings in (bare, explicit):
            assert {finding.rule_id for finding in findings} == (
                {"PD-007"} if release < floor else set()
            ), (release, floor, findings)
            for finding in findings:
                assert "below the floor" in finding.message, finding


def _with_block(release: str, body: str) -> SpellingEnvelope:
    """Supply one selected block between the published builder markers."""
    envelope = _pinned_to(release)
    agents = envelope["agents_md"]
    assert agents is not None, "the compliant fixture must carry AGENTS text"
    agents["text"] = "\n".join((
        "<!-- typos-config-builder:agents-md:start -->",
        body,
        "<!-- typos-config-builder:agents-md:end -->",
    ))
    return envelope


@settings(max_examples=12, deadline=None)
@example(published=[(0, 1, 3), (0, 2, 0), (0, 3, 0)], pins=[(0, 2, 0)])
@example(
    published=[(2**53, 0, 0), (2**53 + 1, 0, 0), (10**100, 0, 0)],
    pins=[(2**53 + 1, 0, 0), (2**64 + 1, 0, 0)],
)
@given(
    published=st.lists(_RELEASES, min_size=3, max_size=3, unique=True),
    pins=st.lists(_RELEASES, min_size=4, max_size=4),
)
def test_bare_and_explicit_pins_select_the_oracle_published_block(
    published: list[Release], pins: list[Release]
) -> None:
    """Both forms select the newest eligible block, or the earliest fallback.

    Every draw includes the middle published version, with a newer block that
    makes empty-pin newest-block fallback observably wrong.
    """
    ordered = sorted(published)
    blocks = {
        _tag(version): f"## Spelling\n\nPublished {_tag(version)}."
        for version in ordered
    }
    selected_pins = [*pins, ordered[1]]
    expected = [
        max((version for version in ordered if version <= pin), default=ordered[0])
        for pin in selected_pins
    ]
    envelopes = [
        _with_block(prefix + _tag(pin), body)
        for pin, selected in zip(selected_pins, expected, strict=True)
        for prefix in ("", "refs/tags/")
        for body in (blocks[_tag(selected)], "## Spelling\n\nUnpublished text.")
    ]
    batches = _batch_findings(
        envelopes, {"agents_md_blocks": blocks, "builder_floor": "v0.0.0"}
    )
    for index, selected in enumerate(expected):
        for findings in batches[index * 4 : index * 4 + 4 : 2]:
            assert not findings, (selected, findings)
        for findings in batches[index * 4 + 1 : index * 4 + 4 : 2]:
            assert len(findings) == 1, findings
            finding = findings[0]
            assert (finding.rule_id, finding.verdict) == ("PD-013", "noncompliant"), (
                finding
            )
            assert f"typos-config-builder {_tag(selected)}'s" in finding.message, (
                finding
            )


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
