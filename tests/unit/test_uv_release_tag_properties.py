"""Generated equivalence checks for listed direct UV release-tag invocations.

Canonical non-negative numeric triples have no strategy upper bound. Twelve
batches sample this support; they are not an unbounded proof. Named examples
include components beyond machine integer precision. Finite Rego cases retain
malformed refs, helper rejection, repository boundaries and chained commands.
"""

from __future__ import annotations

import copy
import hashlib
import json
import typing as typ
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

from concordat.rules import runner
from tests.unit.conftest_batch_support import invoke_conftest_batch

if typ.TYPE_CHECKING:
    from concordat.rules.uv_gate_envelope import UvGateEnvelope

_FIXTURE: typ.Final = Path(__file__).parents[2] / (
    "platform-standards/canon/lint-rules/uv-gate-baseline/fixtures/envelopes/"
    "tool_typos_builder_tag.json"
)
_CANONICAL_HELPER: typ.Final = (
    Path(__file__).parents[2] / "tests/fixtures/uv_gate/uv_gate.py.canon"
)
_RELEASES = st.tuples(*(st.integers(min_value=0),) * 3)
type Release = tuple[int, int, int]


def _direct_pin(release: Release, invocation: str, *, explicit: bool) -> UvGateEnvelope:
    """Return the compliant listed-tool fixture with one direct release pin."""
    envelope = typ.cast(
        "UvGateEnvelope", copy.deepcopy(json.loads(_FIXTURE.read_text("utf-8")))
    )
    gate = envelope["gate"]
    assert gate is not None, "the listed-tool fixture must carry helper facts"
    # Raw Rego fixtures use a stand-in accepted by fixture-only parameters.
    # The real batch runner loads live manifest defaults, so supply the digest
    # of the vendored canonical helper while retaining the UV-001 check.
    gate["sha256"] = hashlib.sha256(_CANONICAL_HELPER.read_bytes()).hexdigest()
    makefile = envelope["makefile"]
    assert makefile is not None, "the listed-tool fixture must carry Make facts"
    tag = "v{}.{}.{}".format(*release)
    pin = f"refs/tags/{tag}" if explicit else tag
    replaced = 0
    for rule in makefile["rules"]:
        for recipe in rule["recipes"]:
            if "typos-config-builder.git@v0.1.3" in recipe["text"]:
                recipe["text"] = recipe["text"].replace("@v0.1.3", f"@{pin}")
                recipe["text"] = recipe["text"].replace("uvx", invocation)
                replaced += 1
    assert replaced == 1, "the listed-tool fixture must have exactly one pin"
    return envelope


@settings(max_examples=12, deadline=None)
@example(releases=[(0, 0, 0), (0, 10, 0), (2**53 + 1, 2**64 + 1, 10**100)])
@given(releases=st.lists(_RELEASES, min_size=4, max_size=4))
def test_listed_direct_invocations_accept_bare_and_explicit_tags(
    releases: list[Release],
) -> None:
    """Both direct UV forms accept every generated listed-tool release pin."""
    envelopes = [
        _direct_pin(release, invocation, explicit=explicit)
        for release in releases
        for invocation in ("uvx", "uv tool run")
        for explicit in (False, True)
    ]
    batches = invoke_conftest_batch("uv-gate-baseline", envelopes)
    for envelope, results in zip(envelopes, batches, strict=True):
        findings = runner._findings_from_results(results)
        assert not findings, (envelope, findings)
