"""Regenerate the policy-input fixture envelopes for spelling-config-baseline.

Each scenario lays out a synthetic checkout from the fixture files under
``makefiles/``, ``workflows/``, ``overlays/``, ``gitignores/``, and ``agents/``,
then hands
it to the production ``build_spelling_envelope`` so the recorded envelopes are
exactly what ``concordat artefact rule run`` would send to Conftest. The
Makefile facts therefore come from the pinned ``makeutil`` on PATH.
``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify``, beside the manifest's ``agents_md_blocks`` under
``parameters``, so the Rego suite compares against the text the runner uses.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/spelling-config-baseline/fixtures/generate.py
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.spelling_envelope import (
    SpellingEnvelope,
    build_spelling_envelope,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

FIXTURES_DIR = Path(__file__).resolve().parent
MAKEFILES_DIR = FIXTURES_DIR / "makefiles"
WORKFLOWS_DIR = FIXTURES_DIR / "workflows"
OVERLAYS_DIR = FIXTURES_DIR / "overlays"
GITIGNORES_DIR = FIXTURES_DIR / "gitignores"
AGENTS_DIR = FIXTURES_DIR / "agents"
MANIFEST_PATH = FIXTURES_DIR.parent / "rule.yaml"
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
        Stem of the ``makefiles/*.mk`` fixture to install as ``Makefile``, or
        ``None`` for a checkout without one.
    workflows:
        Mapping of workflow file name under ``.github/workflows`` to the
        ``workflows/*.yml`` fixture stem that supplies its content.
    overlay:
        Stem of the ``overlays/*.toml`` fixture to install as
        ``typos.local.toml``, or ``None`` for none.
    gitignore:
        Stem of the ``gitignores/*.gitignore`` fixture to install as
        ``.gitignore``, or ``None`` for none.
    vendored:
        Repository-relative paths of legacy machinery to create.
    agents:
        Stem of the ``agents/*.agents`` fixture to install as ``AGENTS.md``,
        or ``None`` for none. The suffix keeps the Markdown gates off these
        deliberately malformed files.
    typos_config:
        Whether the checkout carries a generated ``typos.toml``.
    """

    makefile: str | None = "compliant"
    workflows: cabc.Mapping[str, str] = dataclasses.field(
        default_factory=lambda: {"ci.yml": "make-spelling"}
    )
    overlay: str | None = "schema-1"
    gitignore: str | None = "complete"
    vendored: tuple[str, ...] = ()
    typos_config: bool = True
    agents: str | None = "compliant"


BARE: typ.Final = Scenario(
    makefile=None,
    workflows={},
    overlay=None,
    gitignore=None,
    typos_config=False,
    agents=None,
)

SCENARIOS: typ.Final[dict[str, Scenario]] = {
    # -- clean checkouts ---------------------------------------------------
    "compliant": Scenario(),
    "uv_tool_run": Scenario(makefile="uv-tool-run"),
    "delegated": Scenario(makefile="delegated"),
    "above_floor": Scenario(makefile="above-floor"),
    "echo_mention": Scenario(
        makefile="echo-mention", workflows={"ci.yml": "echo-mention"}
    ),
    "gitignore_rooted": Scenario(gitignore="rooted"),
    "not_applicable": BARE,
    # -- PD-007: the spelling gate -----------------------------------------
    "no_makefile": Scenario(makefile=None),
    "no_spelling_target": Scenario(makefile="no-spelling-target"),
    "no_gate": Scenario(makefile="no-gate"),
    "unpinned": Scenario(makefile="unpinned"),
    "sha_pin": Scenario(makefile="sha-pin"),
    "branch_pin": Scenario(makefile="branch-pin"),
    "below_floor": Scenario(makefile="below-floor"),
    "other_repository": Scenario(makefile="other-repository"),
    "soft_skip": Scenario(makefile="soft-skip"),
    "default_command": Scenario(makefile="default-command"),
    "drift_check": Scenario(makefile="drift-check"),
    "direct_typos": Scenario(makefile="direct-typos"),
    "unresolved": Scenario(makefile="unresolved"),
    "pin_unresolved_paren": Scenario(makefile="pin-unresolved-paren"),
    "pin_unresolved_brace": Scenario(makefile="pin-unresolved-brace"),
    "with_include": Scenario(makefile="with-include"),
    # -- PD-008: legacy pins and helper targets ----------------------------
    "legacy_variables": Scenario(makefile="legacy-variables"),
    "legacy_targets": Scenario(makefile="legacy-targets"),
    # -- PD-009: vendored machinery ----------------------------------------
    "vendored": Scenario(
        vendored=(
            "scripts/generate_typos_config.py",
            "scripts/typos_rollout_check.py",
            "scripts/tests/test_typos_rollout_check.py",
        )
    ),
    "vendored_only": dataclasses.replace(BARE, vendored=("scripts/typos_rollout.py",)),
    # -- PD-010: CI ----------------------------------------------------------
    "ci_direct_typos": Scenario(workflows={"spelling.yml": "direct-typos"}),
    "ci_typos_action": Scenario(workflows={"spelling.yml": "typos-action"}),
    "ci_drift_builder": Scenario(workflows={"spelling.yml": "drift-builder"}),
    "ci_drift_git_diff": Scenario(workflows={"spelling.yml": "drift-git-diff"}),
    "ci_legacy_script": Scenario(workflows={"spelling.yml": "legacy-script"}),
    "ci_legacy_target": Scenario(workflows={"spelling.yml": "legacy-target"}),
    "ci_malformed": Scenario(
        workflows={"ci.yml": "make-spelling", "broken.yml": "malformed"}
    ),
    "ci_malformed_only": dataclasses.replace(
        BARE, workflows={"broken.yml": "malformed"}
    ),
    # -- PD-011: the cache is ignored --------------------------------------
    "gitignore_missing": Scenario(gitignore=None),
    "gitignore_partial": Scenario(gitignore="partial"),
    "gitignore_none_listed": Scenario(gitignore="none-listed"),
    "gitignore_negated": Scenario(gitignore="negated"),
    "gitignore_glob": Scenario(gitignore="glob"),
    "gitignore_braces": Scenario(gitignore="braces"),
    # -- PD-012: the overlay -------------------------------------------------
    "overlay_missing": Scenario(overlay=None),
    "overlay_schema_2": Scenario(overlay="schema-2"),
    "overlay_no_schema": Scenario(overlay="no-schema"),
    "overlay_malformed": Scenario(overlay="malformed"),
    # -- PD-013: the AGENTS.md spelling block ------------------------------
    "agents_reflowed": Scenario(agents="reflowed"),
    "agents_overlay_mention": Scenario(agents="overlay-mention"),
    "agents_missing": Scenario(agents=None),
    "agents_drifted": Scenario(agents="drifted"),
    "agents_no_markers": Scenario(agents="no-markers"),
    "agents_two_blocks": Scenario(agents="two-blocks"),
    "agents_end_before_start": Scenario(agents="end-before-start"),
    "agents_duplicate_guidance": Scenario(agents="duplicate-guidance"),
    "agents_above_newest_text": Scenario(makefile="above-floor"),
}


# The single-file fixtures: the scenario field naming the stem, the directory
# and suffix it is read from, and the name it is installed under.
_COPIES: typ.Final = (
    ("makefile", MAKEFILES_DIR, ".mk", "Makefile"),
    ("overlay", OVERLAYS_DIR, ".toml", "typos.local.toml"),
    ("gitignore", GITIGNORES_DIR, ".gitignore", ".gitignore"),
    ("agents", AGENTS_DIR, ".agents", "AGENTS.md"),
)


def lay_out(scenario: Scenario, checkout: Path) -> None:
    """Populate *checkout* with the files *scenario* names.

    Parameters
    ----------
    scenario:
        The scenario whose fixture files compose the checkout.
    checkout:
        An existing empty directory to lay the files out in.
    """
    (checkout / "README.md").write_text("# Fixture\n", encoding="utf-8")
    for field, directory, suffix, target in _COPIES:
        stem = getattr(scenario, field)
        if stem is not None:
            shutil.copyfile(directory / f"{stem}{suffix}", checkout / target)
    if scenario.typos_config:
        (checkout / "typos.toml").write_text("[default]\n", encoding="utf-8")
    _write_vendored(checkout, scenario.vendored)
    _write_workflows(checkout, scenario.workflows)


def _write_vendored(checkout: Path, paths: cabc.Sequence[str]) -> None:
    """Create each named piece of legacy machinery as a one-line module."""
    for relative in paths:
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('"""Vendored."""\n', encoding="utf-8")


def _write_workflows(checkout: Path, workflows: cabc.Mapping[str, str]) -> None:
    """Install each named workflow fixture under `.github/workflows`."""
    if not workflows:
        return
    directory = checkout / ".github" / "workflows"
    directory.mkdir(parents=True)
    for name, stem in workflows.items():
        shutil.copyfile(WORKFLOWS_DIR / f"{stem}.yml", directory / name)


def build_fixture_envelope(scenario: Scenario) -> SpellingEnvelope:
    """Return the production envelope for *scenario*, with a stable path.

    Parameters
    ----------
    scenario:
        The scenario to lay out and record.

    Returns
    -------
    SpellingEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so
        the temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="spelling-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(scenario, checkout)
        envelope = build_spelling_envelope(checkout)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def manifest_parameters() -> dict[str, object]:
    """Return the manifest parameters the Rego suite must share with the runner.

    Returns
    -------
    dict[str, object]
        The ``agents_md_blocks`` default from ``rule.yaml``.
    """
    manifest = YAML(typ="safe").load(MANIFEST_PATH.read_text(encoding="utf-8"))
    defaults = manifest["parameters"]["defaults"]
    return {"agents_md_blocks": defaults["agents_md_blocks"]}


def main() -> None:
    """Regenerate every envelope and the bundled data document.

    Writes one JSON file per scenario under ``envelopes/`` and the bundle
    ``data.json`` that ``conftest verify --data`` consumes. Existing files
    are overwritten, so the pinned ``makeutil`` must be on PATH.
    """
    ENVELOPES_DIR.mkdir(exist_ok=True)
    envelopes = {
        key: build_fixture_envelope(scenario) for key, scenario in SCENARIOS.items()
    }
    for key, envelope in envelopes.items():
        target = ENVELOPES_DIR / f"{key}.json"
        target.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    bundle = FIXTURES_DIR / "data.json"
    bundle.write_text(
        json.dumps(
            {"fixtures": envelopes, "parameters": manifest_parameters()}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
