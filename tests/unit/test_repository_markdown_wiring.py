"""The repository's own Markdown wiring must satisfy the rule it publishes.

Concordat owns the `markdown-formatting-baseline` canon, so its `Makefile`,
its `.markdownlint-cli2.jsonc`, and its CI workflow are the rule's first
consumer. The policy suite proves the sensor against synthetic fixtures; this
module proves the checked-in wiring itself, through the production builder
and real Conftest, so a change to any of those three files that breaks the
mandate fails here rather than in the estate.
"""

from __future__ import annotations

import pathlib
import re
import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.markdown_envelope import build_markdown_envelope

_RULE_ID: typ.Final = "markdown-formatting-baseline"
REPO_ROOT: typ.Final = pathlib.Path(__file__).resolve().parents[2]
CI_WORKFLOW: typ.Final = REPO_ROOT / ".github" / "workflows" / "ci.yml"
USERS_GUIDE: typ.Final = REPO_ROOT / "docs" / "users-guide.md"
_ACTION_PIN = re.compile(
    r"DavidAnson/markdownlint-cli2-action@([0-9a-zA-Z._-]+)",
)


def _pins(path: pathlib.Path) -> list[str]:
    """Return every markdownlint action reference *path* carries."""
    return _ACTION_PIN.findall(path.read_text(encoding="utf-8"))


@pytest.mark.timeout(120)
def test_repository_satisfies_its_own_markdown_baseline() -> None:
    """Running the shipped rule over this checkout reports no findings.

    This is the wiring change the rule exists to mandate: `fmt` and
    `check-fmt` calling the tools directly, the canonical markdownlint
    configuration, and CI linting only through the pinned action.
    """
    envelope = build_markdown_envelope(REPO_ROOT)
    results = runner._invoke_conftest(_RULE_ID, envelope)
    findings = runner._findings_from_results(results)
    assert list(findings) == [], findings


def test_ci_pins_the_action_to_a_full_commit_sha() -> None:
    """The workflow's pin is forty hex characters, as PD-006 requires."""
    pins = _pins(CI_WORKFLOW)
    assert len(pins) == 1, pins
    assert re.fullmatch(r"[0-9a-f]{40}", pins[0]), pins[0]


def test_the_documented_pin_matches_the_workflow() -> None:
    """The users' guide snippet and CI name the same revision.

    A reader copies the snippet into their own workflow, so a snippet that
    drifts from the pin this repository actually runs propagates the stale
    revision across the estate.
    """
    documented = _pins(USERS_GUIDE)
    assert documented == _pins(CI_WORKFLOW), documented
