"""Unit tests for composing pin resolution onto the envelope builder.

`packages.resolving_pins` is how the rule-run command adds the network step
to an otherwise query-only envelope build. These tests pass recording
resolvers, so they prove the composition without any network.
"""

from __future__ import annotations

import typing as typ

from concordat.rules.action_pins import PinResolution, commit_pin
from concordat.rules.packages import default_envelope_builder, resolving_pins

if typ.TYPE_CHECKING:
    import pathlib

ACTION: typ.Final = "DavidAnson/markdownlint-cli2-action"
COMMIT: typ.Final = "21c1be1b93ad9ed58fa840aacc3f279cde2a72ff"


class _Recorder:
    """A resolver that answers "commit" and records what it was asked."""

    def __init__(self) -> None:
        """Start with no questions asked."""
        self.asked: list[tuple[str, str]] = []

    def __call__(self, repository: str, sha: str) -> PinResolution:
        """Record the question and answer that *sha* is a commit."""
        self.asked.append((repository, sha))
        return commit_pin(sha)


def _pinned_checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a Markdown checkout whose workflow pins the lint action."""
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        f"jobs:\n  lint:\n    steps:\n      - uses: {ACTION}@{COMMIT}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_markdown_package_records_its_resolved_pins(tmp_path: pathlib.Path) -> None:
    """The package's configured action is asked about, and the answer recorded."""
    resolver = _Recorder()
    build = resolving_pins(default_envelope_builder, resolver)
    envelope = typ.cast(
        "dict[str, object]",
        build("markdown-formatting-baseline", _pinned_checkout(tmp_path)),
    )
    assert resolver.asked == [(ACTION, COMMIT)]
    assert envelope["action_pins"] == {COMMIT: commit_pin(COMMIT)}


def test_the_default_builder_alone_resolves_nothing(tmp_path: pathlib.Path) -> None:
    """Without the composition, the package builder leaves pins unresolved."""
    envelope = typ.cast(
        "dict[str, object]",
        default_envelope_builder(
            "markdown-formatting-baseline", _pinned_checkout(tmp_path)
        ),
    )
    assert envelope["action_pins"] == {}


def test_other_envelope_kinds_pass_through_untouched(tmp_path: pathlib.Path) -> None:
    """A non-Markdown envelope is returned as built and the resolver never runs."""
    other = {"kind": "policy-input/other", "schema_version": 1}
    resolver = _Recorder()
    build = resolving_pins(
        lambda _rule_id, _checkout: typ.cast("typ.Any", other), resolver
    )
    assert build("rust-makefile-baseline", tmp_path) is other
    assert resolver.asked == []
