"""Regenerate the policy-input fixture envelopes for yaml-lint-baseline.

Each scenario lays out a synthetic checkout from the fixture files under
``makefiles/``, ``workflows/``, ``yamllint-base/``, ``yamllint-local/``, and
``actionlint/``, then hands it to the production ``build_yaml_lint_envelope`` so
the recorded envelopes are exactly what ``concordat artefact rule run`` would
send to Conftest. The Makefile facts therefore come from the pinned ``makeutil``
on PATH. ``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify``, beside the manifest's defaults under ``parameters``, so the
Rego suite compares against the digest and patterns the runner uses.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/yaml-lint-baseline/fixtures/generate.py
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.yaml_lint_envelope import (
    YamlLintEnvelope,
    build_yaml_lint_envelope,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

FIXTURES_DIR = Path(__file__).resolve().parent
MAKEFILES_DIR = FIXTURES_DIR / "makefiles"
WORKFLOWS_DIR = FIXTURES_DIR / "workflows"
BASES_DIR = FIXTURES_DIR / "yamllint-base"
LOCALS_DIR = FIXTURES_DIR / "yamllint-local"
ACTIONLINT_DIR = FIXTURES_DIR / "actionlint"
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
    base:
        Stem of the ``yamllint-base/*.yml`` fixture to install as
        ``.yamllint-estate.yml``, or ``None`` for none.
    local:
        Stem of the ``yamllint-local/*.yml`` fixture to install as
        ``.yamllint.yml``, or ``None`` for none.
    alternates:
        Alternate yamllint configuration names to create beside the others.
    actionlint:
        Stem of the ``actionlint/*.yaml`` fixture to install, or ``None``.
    actionlint_name:
        Where the actionlint fixture is installed.
    other_yaml:
        Extra YAML files to create, so a checkout with no workflows can still
        carry YAML.
    """

    makefile: str | None = "compliant"
    workflows: cabc.Mapping[str, str] = dataclasses.field(
        default_factory=lambda: {"ci.yml": "lint-lane"}
    )
    base: str | None = "canonical"
    local: str | None = "thin"
    alternates: tuple[str, ...] = ()
    actionlint: str | None = "ubicloud"
    actionlint_name: str = ".github/actionlint.yaml"
    other_yaml: tuple[str, ...] = ()


BARE: typ.Final = Scenario(
    makefile=None, workflows={}, base=None, local=None, actionlint=None
)

# A repository with YAML but no workflows: the yamllint checks apply, the
# actionlint and CI checks do not.
YAML_ONLY: typ.Final = dataclasses.replace(
    BARE,
    makefile="compliant",
    base="canonical",
    local="thin",
    other_yaml=("deploy/values.yaml",),
)

SCENARIOS: typ.Final[dict[str, Scenario]] = {
    # -- clean checkouts ---------------------------------------------------
    "compliant": Scenario(),
    "delegated": Scenario(makefile="delegated"),
    "xargs_options": Scenario(makefile="xargs-options"),
    "literal_tools": Scenario(makefile="literal-tools"),
    "local_ignore": Scenario(local="with-ignore"),
    "local_ignore_list": Scenario(local="ignore-list"),
    "extra_labels": Scenario(actionlint="extra-labels"),
    "actionlint_yml_spelling": Scenario(actionlint_name=".github/actionlint.yml"),
    "make_flags": Scenario(workflows={"ci.yml": "make-flags"}),
    "version_probe": Scenario(workflows={"ci.yml": "version-probe"}),
    "other_lint_targets_beside_lint": Scenario(
        workflows={"ci.yml": "lint-lane", "docs.yml": "other-lint-targets"}
    ),
    "yaml_only": YAML_ONLY,
    "not_applicable": BARE,
    # -- YL-001 / YL-002: the lint gate ------------------------------------
    "no_makefile": Scenario(makefile=None),
    "no_lint_target": Scenario(makefile="no-lint-target"),
    "no_yamllint": Scenario(makefile="no-yamllint"),
    "no_actionlint": Scenario(makefile="no-actionlint"),
    "not_in_lint": Scenario(makefile="not-in-lint"),
    "no_strict": Scenario(makefile="no-strict"),
    "no_ls_files": Scenario(makefile="no-ls-files"),
    "one_pathspec": Scenario(makefile="one-pathspec"),
    "config_data": Scenario(makefile="config-data"),
    "no_warnings": Scenario(makefile="no-warnings"),
    "shellcheck_off": Scenario(makefile="shellcheck-off"),
    "soft_skip": Scenario(makefile="soft-skip"),
    "echo_decoy": Scenario(makefile="echo-decoy"),
    "unresolved": Scenario(makefile="unresolved"),
    "hidden_tool": Scenario(makefile="hidden-tool"),
    "with_include": Scenario(makefile="with-include"),
    "conditional": Scenario(makefile="conditional"),
    "yaml_only_no_makefile": dataclasses.replace(YAML_ONLY, makefile=None),
    # -- YL-003: the yamllint configuration --------------------------------
    "base_missing": Scenario(base=None),
    "base_drifted": Scenario(base="drifted"),
    "local_missing": Scenario(local=None),
    "local_extends_default": Scenario(local="extends-default"),
    "local_no_extends": Scenario(local="no-extends"),
    "local_sets_rules": Scenario(local="sets-rules"),
    "local_extra_keys": Scenario(local="extra-keys"),
    "local_ignore_number": Scenario(local="ignore-number"),
    "local_non_mapping": Scenario(local="non-mapping"),
    "local_malformed": Scenario(local="malformed"),
    "alternate_dotless": Scenario(alternates=(".yamllint",)),
    "alternate_yaml": Scenario(alternates=(".yamllint.yaml",)),
    "alternate_only": Scenario(local=None, alternates=(".yamllint",)),
    # -- YL-004: the actionlint configuration ------------------------------
    "actionlint_missing": Scenario(actionlint=None),
    "actionlint_other_labels": Scenario(actionlint="other-labels"),
    "actionlint_no_section": Scenario(actionlint="no-section"),
    "actionlint_labels_not_list": Scenario(actionlint="labels-not-list"),
    "actionlint_section_not_mapping": Scenario(actionlint="section-not-mapping"),
    "actionlint_malformed": Scenario(actionlint="malformed"),
    # -- YL-005: CI ----------------------------------------------------------
    "ci_no_installer": Scenario(workflows={"ci.yml": "no-installer"}),
    "ci_unpinned_installer": Scenario(workflows={"ci.yml": "unpinned-installer"}),
    "ci_installer_after": Scenario(workflows={"ci.yml": "installer-after"}),
    "ci_installer_other_job": Scenario(workflows={"ci.yml": "installer-other-job"}),
    "ci_installer_disabled": Scenario(workflows={"ci.yml": "installer-disabled"}),
    "ci_lint_disabled": Scenario(workflows={"ci.yml": "lint-disabled"}),
    "ci_two_jobs": Scenario(workflows={"ci.yml": "two-jobs"}),
    "ci_no_lint": Scenario(workflows={"ci.yml": "no-lint"}),
    "ci_other_lint_targets": Scenario(workflows={"ci.yml": "other-lint-targets"}),
    "ci_reusable_only": Scenario(workflows={"ci.yml": "reusable-only"}),
    "ci_hand_rolled_uv": Scenario(workflows={"ci.yml": "hand-rolled-uv"}),
    "ci_hand_rolled_download": Scenario(workflows={"ci.yml": "hand-rolled-download"}),
    "ci_hand_rolled_apt": Scenario(workflows={"ci.yml": "hand-rolled-apt"}),
    "ci_third_party_action": Scenario(workflows={"ci.yml": "third-party-action"}),
    "ci_malformed": Scenario(
        workflows={"ci.yml": "lint-lane", "broken.yml": "malformed"}
    ),
    "ci_malformed_only": Scenario(workflows={"broken.yml": "malformed"}),
}


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
    copies = (
        (scenario.makefile, MAKEFILES_DIR, ".mk", "Makefile"),
        (scenario.base, BASES_DIR, ".yml", ".yamllint-estate.yml"),
        (scenario.local, LOCALS_DIR, ".yml", ".yamllint.yml"),
        (scenario.actionlint, ACTIONLINT_DIR, ".yaml", scenario.actionlint_name),
    )
    for stem, directory, suffix, target in copies:
        if stem is not None:
            destination = checkout / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / f"{stem}{suffix}", destination)
    for name in scenario.alternates:
        (checkout / name).write_text("extends: default\n", encoding="utf-8")
    for relative in scenario.other_yaml:
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("key: value\n", encoding="utf-8")
    _write_workflows(checkout, scenario.workflows)


def _write_workflows(checkout: Path, workflows: cabc.Mapping[str, str]) -> None:
    """Install each named workflow fixture under `.github/workflows`."""
    if not workflows:
        return
    directory = checkout / ".github" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    for name, stem in workflows.items():
        shutil.copyfile(WORKFLOWS_DIR / f"{stem}.yml", directory / name)


def build_fixture_envelope(scenario: Scenario) -> YamlLintEnvelope:
    """Return the production envelope for *scenario*, with a stable path.

    Parameters
    ----------
    scenario:
        The scenario to lay out and record.

    Returns
    -------
    YamlLintEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so
        the temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="yaml-lint-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(scenario, checkout)
        envelope = build_yaml_lint_envelope(checkout)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def manifest_parameters() -> dict[str, object]:
    """Return the manifest defaults the Rego suite must share with the runner.

    Returns
    -------
    dict[str, object]
        The ``defaults`` block of ``rule.yaml``.
    """
    manifest = YAML(typ="safe").load(MANIFEST_PATH.read_text(encoding="utf-8"))
    return dict(manifest["parameters"]["defaults"])


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
