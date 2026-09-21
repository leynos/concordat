"""Regenerate the policy-input fixture envelopes for markdown-formatting-baseline.

Each scenario lays out a synthetic checkout from the fixture files under
``makefiles/``, ``markdownlint/``, and ``workflows/``, then hands it to the
production ``build_markdown_envelope`` so the recorded envelopes are exactly
what ``concordat artefact rule run`` would send to Conftest. The Makefile
facts therefore come from the pinned ``makeutil`` on PATH. ``data.json``
bundles every envelope under a ``fixtures`` key for ``conftest verify``.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/markdown-formatting-baseline/fixtures/generate.py
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
import typing as typ
from pathlib import Path

from concordat.rules.markdown_envelope import (
    MarkdownEnvelope,
    build_markdown_envelope,
)

FIXTURES_DIR = Path(__file__).resolve().parent
MAKEFILES_DIR = FIXTURES_DIR / "makefiles"
MARKDOWNLINT_DIR = FIXTURES_DIR / "markdownlint"
WORKFLOWS_DIR = FIXTURES_DIR / "workflows"
ENVELOPES_DIR = FIXTURES_DIR / "envelopes"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."


@dataclasses.dataclass(frozen=True)
class Scenario:
    """The files one fixture checkout is composed from.

    Attributes
    ----------
    makefile:
        Stem of the ``makefiles/*.mk`` fixture to install as ``Makefile``,
        or ``None`` for a checkout without one.
    markdownlint:
        Stem of the ``markdownlint/*.jsonc`` fixture to install as
        ``.markdownlint-cli2.jsonc``, or ``None`` for none.
    workflows:
        Mapping of workflow file name under ``.github/workflows`` to the
        ``workflows/*.yml`` fixture stem that supplies its content. An empty
        mapping leaves the workflows directory absent.
    markdown:
        Whether the checkout carries a Markdown file at all.
    alternate_config:
        A non-JSONC markdownlint configuration file name to create empty, or
        ``None``.
    """

    makefile: str | None = "compliant"
    markdownlint: str | None = "baseline"
    workflows: typ.Mapping[str, str] = dataclasses.field(
        default_factory=lambda: {"ci.yml": "action"}
    )
    markdown: bool = True
    alternate_config: str | None = None


SCENARIOS: typ.Final[dict[str, Scenario]] = {
    # -- clean checkouts ---------------------------------------------------
    "compliant": Scenario(),
    "literal_tools": Scenario(makefile="literal-tools"),
    "delegated": Scenario(makefile="delegated"),
    "probe_nested": Scenario(makefile="probe-nested"),
    "home_prefixed": Scenario(makefile="home-prefixed"),
    "config_extended": Scenario(markdownlint="extended"),
    "no_markdown": Scenario(markdown=False, makefile=None, markdownlint=None),
    # -- Makefile findings -------------------------------------------------
    "no_makefile": Scenario(makefile=None),
    "missing_targets": Scenario(makefile="missing-targets"),
    "mdformat_wrapper": Scenario(makefile="mdformat-wrapper"),
    "missing_flags": Scenario(makefile="missing-flags"),
    "soft_skip": Scenario(makefile="soft-skip"),
    "mode_swapped": Scenario(makefile="mode-swapped"),
    "echo_decoy": Scenario(makefile="echo-decoy"),
    "extra_invocation": Scenario(makefile="extra-invocation"),
    "conditional": Scenario(makefile="conditional"),
    "with_include": Scenario(makefile="with-include"),
    "ambiguous_variable": Scenario(makefile="ambiguous-variable"),
    "undefined_variable": Scenario(makefile="undefined-variable"),
    "recovered": Scenario(makefile="recovered"),
    # -- markdownlint configuration findings -------------------------------
    "config_missing": Scenario(markdownlint=None),
    "config_alternate": Scenario(
        markdownlint=None, alternate_config=".markdownlint.yaml"
    ),
    "config_drifted": Scenario(markdownlint="drifted"),
    "config_malformed": Scenario(markdownlint="malformed"),
    # -- workflow findings -------------------------------------------------
    "workflow_shell_lint": Scenario(workflows={"ci.yml": "shell-lint"}),
    "workflow_floating_tag": Scenario(workflows={"ci.yml": "floating-tag"}),
    "workflow_narrow_globs": Scenario(workflows={"ci.yml": "narrow-globs"}),
    "workflow_none": Scenario(workflows={"release.yml": "no-markdown"}),
    "workflow_absent": Scenario(workflows={}),
    "workflow_reusable_only": Scenario(workflows={"ci.yml": "reusable-call"}),
    "workflow_malformed": Scenario(
        workflows={"ci.yml": "malformed", "release.yml": "no-markdown"}
    ),
    "workflow_install_for_tests": Scenario(workflows={"ci.yml": "install-for-tests"}),
    "workflow_mixed": Scenario(
        workflows={"ci.yml": "action", "docs.yml": "shell-lint"}
    ),
    "workflow_disabled_action": Scenario(workflows={"ci.yml": "disabled-action"}),
    "workflow_disabled_job": Scenario(workflows={"ci.yml": "disabled-job"}),
    "workflow_echo_mention": Scenario(workflows={"ci.yml": "echo-mention"}),
}


def lay_out(scenario: Scenario, checkout: Path) -> None:
    """Populate *checkout* with the files *scenario* names."""
    if scenario.markdown:
        (checkout / "README.md").write_text("# Fixture\n", encoding="utf-8")
    if scenario.makefile is not None:
        shutil.copyfile(
            MAKEFILES_DIR / f"{scenario.makefile}.mk", checkout / "Makefile"
        )
    if scenario.markdownlint is not None:
        shutil.copyfile(
            MARKDOWNLINT_DIR / f"{scenario.markdownlint}.jsonc",
            checkout / ".markdownlint-cli2.jsonc",
        )
    if scenario.alternate_config is not None:
        (checkout / scenario.alternate_config).write_text("", encoding="utf-8")
    if scenario.workflows:
        workflows = checkout / ".github" / "workflows"
        workflows.mkdir(parents=True)
        for name, stem in scenario.workflows.items():
            shutil.copyfile(WORKFLOWS_DIR / f"{stem}.yml", workflows / name)


def build_fixture_envelope(scenario: Scenario) -> MarkdownEnvelope:
    """Return the production envelope for *scenario*, with a stable path."""
    with tempfile.TemporaryDirectory(prefix="markdown-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(scenario, checkout)
        envelope = build_markdown_envelope(checkout)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def main() -> None:
    """Regenerate every envelope and the bundled data document."""
    ENVELOPES_DIR.mkdir(exist_ok=True)
    envelopes = {
        key: build_fixture_envelope(scenario) for key, scenario in SCENARIOS.items()
    }
    for key, envelope in envelopes.items():
        target = ENVELOPES_DIR / f"{key}.json"
        target.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    bundle = FIXTURES_DIR / "data.json"
    bundle.write_text(
        json.dumps({"fixtures": envelopes}, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
