"""Regenerate the policy-input fixture envelopes for uv-gate-baseline.

Each scenario lays out a synthetic checkout from the file contents written
below, then hands it to the production ``build_uv_gate_envelope`` so the
recorded envelopes are exactly what ``concordat artefact rule run`` would send
to Conftest. The Makefile facts therefore come from the pinned ``makeutil`` on
PATH. ``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify``, beside the manifest's parameters. Those are the manifest
defaults except ``gate_digests``, which names the stand-in helper the scenarios
vendor instead of the 850-line canonical file.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/uv-gate-baseline/fixtures/generate.py
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.uv_gate_envelope import UvGateEnvelope, build_uv_gate_envelope

if typ.TYPE_CHECKING:
    import collections.abc as cabc

FIXTURES_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = FIXTURES_DIR.parent / "rule.yaml"
ENVELOPES_DIR = FIXTURES_DIR / "envelopes"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."

GATE = "# stand-in for the canonical uv_gate.py\n"
DRIFTED_GATE = GATE + "# edited by hand\n"
GATE_DIGEST = hashlib.sha256(GATE.encode()).hexdigest()
# An earlier canonical helper that some repositories still vendor.
OLDER_GATE = "# stand-in for an earlier canonical uv_gate.py\n"
OLDER_GATE_DIGEST = hashlib.sha256(OLDER_GATE.encode()).hexdigest()

SHA = "0123456789abcdef0123456789abcdef01234567"
PYPROJECT = (
    '[project]\nname = "demo"\nversion = "0.1.0"\n'
    '[dependency-groups]\ndev = ["pytest>=8"]\n'
)

BUILDER = "git+https://github.com/leynos/typos-config-builder.git"
OTHER_TOOL = "git+https://github.com/example/tool.git"
CMD_MOX = "https://github.com/leynos/cmd-mox.git"
CMD_MOX_REQUIREMENT = "cmd-mox @ git+https://github.com/leynos/cmd-mox.git"


def gated_tool(spec: str, command: str = "tool") -> str:
    """Return a recipe that runs *command* from *spec* through the helper."""
    return f"$(UV_GATE) tool --from '{spec}' -- {command}"


def uv_source(**selectors: str) -> str:
    """Return a ``[tool.uv.sources]`` table with one Git source."""
    pairs = ", ".join(f'{key} = "{value}"' for key, value in selectors.items())
    return f'[tool.uv.sources]\ncmd-mox = {{ git = "{CMD_MOX}", {pairs} }}\n'


def with_dependency(requirement: str) -> str:
    """Return the base pyproject with *requirement* added to the dev group."""
    return PYPROJECT.replace('"pytest>=8"', f'"pytest>=8", "{requirement}"')


COMPLIANT_MAKEFILE = f"""\
UV_GATE ?= python3 scripts/uv_gate.py

.PHONY: prepare lint test spelling
prepare:
\t$(UV_GATE) prepare --group dev

lint: prepare
\t$(UV_GATE) run --group dev -- ruff check .

test: prepare
\t$(UV_GATE) run --group dev -- pytest -q

spelling:
\tuvx --from "{BUILDER}@v0.1.3" typos-config-builder gate
"""


def makefile(*recipes: str, extra: str = "") -> str:
    """Return a Makefile with the helper variable and one target per recipe."""
    body = "".join(f"t{index}:\n\t{recipe}\n" for index, recipe in enumerate(recipes))
    return f"UV_GATE ?= python3 scripts/uv_gate.py\n{extra}\n{body}"


def workflow(run: str, *, env: str = "") -> str:
    """Return a workflow with one job step that runs *run*."""
    return (
        "name: ci\non: push\njobs:\n  gate:\n    runs-on: ubuntu-latest\n"
        f"{env}    steps:\n      - run: {run}\n"
    )


@dataclasses.dataclass(frozen=True)
class Scenario:
    """The files one fixture checkout holds, by relative path.

    Attributes
    ----------
    files:
        Relative path to text. A scenario starts from ``BASE`` and layers
        these over it; a value of ``None`` removes the file.
    """

    files: cabc.Mapping[str, str | None] = dataclasses.field(default_factory=dict)


BASE: typ.Final = {
    "Makefile": COMPLIANT_MAKEFILE,
    "scripts/uv_gate.py": GATE,
    "pyproject.toml": PYPROJECT,
    "uv.lock": "version = 1\n",
    ".github/workflows/ci.yml": workflow("make lint test"),
}


def pyproject(extra: str) -> str:
    """Return the base pyproject with *extra* appended."""
    return PYPROJECT + extra


SCENARIOS: typ.Final[dict[str, Scenario]] = {
    "compliant": Scenario(),
    "not_applicable": Scenario({
        "Makefile": "build:\n\tcc -o x x.c\n",
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": workflow("make build"),
    }),
    # Each kind of evidence that a repository uses uv, on its own.
    "uv_in_recipe_only": Scenario({
        "Makefile": "lint:\n\tuv run ruff check .\n",
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": None,
    }),
    "uv_in_variable_only": Scenario({
        "Makefile": "RUNNER = uvx ruff==0.16.4\nbuild:\n\tcc -o x x.c\n",
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": None,
    }),
    "lock_only": Scenario({
        "Makefile": "build:\n\tcc -o x x.c\n",
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        ".github/workflows/ci.yml": None,
    }),
    "gate_only_drifted": Scenario({
        "Makefile": "build:\n\tcc -o x x.c\n",
        "scripts/uv_gate.py": DRIFTED_GATE,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": None,
    }),
    "gate_in_recipe_only": Scenario({
        "Makefile": "t0:\n\tpython3 scripts/uv_gate.py prepare\n",
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": None,
    }),
    "workflow_malformed_with_uv": Scenario({
        ".github/workflows/ci.yml": "jobs: [unterminated\n",
    }),
    "makefile_recovered": Scenario({
        "Makefile": COMPLIANT_MAKEFILE + "\nthis is not make\n"
    }),
    "bypass_unresolved_uvx": Scenario({
        "Makefile": makefile(
            "$(UVX) ruff==0.16.4 check .",
            extra="ifeq ($(A),1)\nUVX := uvx\nelse\nUVX := /opt/uvx\nendif\n",
        )
    }),
    "gate_variable_other": Scenario({
        "Makefile": COMPLIANT_MAKEFILE.replace(
            "python3 scripts/uv_gate.py", "python3 scripts/other.py", 1
        )
    }),
    "retry_word": Scenario({"Makefile": makefile("retry 3 $(UV_GATE) prepare")}),
    "retry_without_uv_ok": Scenario({
        "Makefile": COMPLIANT_MAKEFILE + "\nflaky:\n\tretry 3 make test\n"
    }),
    "uv_tool_run_from_unpinned": Scenario({
        "Makefile": makefile("uv tool run --from ruff ruff check .")
    }),
    "uv_tool_run_unpinned_positional": Scenario({
        "Makefile": makefile("uv tool run ruff check .")
    }),
    "uvx_from_unpinned": Scenario({
        "Makefile": makefile("uvx --from ruff ruff check .")
    }),
    "uvx_from_equals_pinned": Scenario({
        "Makefile": makefile("uvx --from=ruff==0.16.4 ruff check .")
    }),
    "uvx_unpinned_positional": Scenario({"Makefile": makefile("uvx ruff check .")}),
    # Fail closed where the Makefile may hide uv, and where uv is named by path.
    "include_without_uv": Scenario({
        "Makefile": "include uv.mk\n",
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": None,
    }),
    "recovered_without_uv": Scenario({
        "Makefile": "this is not make\n",
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": None,
    }),
    "path_qualified_uv": Scenario({"Makefile": makefile("/usr/bin/uv run pytest")}),
    "path_qualified_uv_only": Scenario({
        "Makefile": "lint:\n\t/opt/uv/bin/uv sync\n",
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": None,
    }),
    "gate_variable_conditional": Scenario({
        "Makefile": "ifeq ($(A),1)\nUV_GATE := uv\nendif\n"
        + COMPLIANT_MAKEFILE.replace("UV_GATE ?= python3 scripts/uv_gate.py\n", "", 1)
    }),
    "nested_action_cache": Scenario({
        ".github/actions/setup/python/action.yml": (
            "name: setup\nruns:\n  using: composite\n  steps:\n"
            "    - shell: bash\n      run: make prepare\n"
            "      env:\n        UV_TOOL_DIR: .uv-tools\n"
        )
    }),
    "git_dep_marker": Scenario({
        "pyproject.toml": with_dependency(
            f"{CMD_MOX_REQUIREMENT}@{SHA} ; python_version < '3.13'"
        )
    }),
    # UV-001
    "gate_missing": Scenario({"scripts/uv_gate.py": None}),
    "gate_older_canon": Scenario({"scripts/uv_gate.py": OLDER_GATE}),
    "gate_drifted": Scenario({"scripts/uv_gate.py": DRIFTED_GATE}),
    "workflow_only_no_gate": Scenario({
        "Makefile": None,
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": workflow("uvx ruff==0.16.4 check ."),
    }),
    # UV-002
    "cache_variable": Scenario({
        "Makefile": makefile("$(UV_GATE) prepare", extra="UV_CACHE_DIR := .uv-cache\n")
    }),
    "cache_in_env_variable": Scenario({
        "Makefile": makefile(
            "$(UV_GATE) prepare",
            extra="UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools\n",
        )
    }),
    "cache_in_recipe": Scenario({
        "Makefile": makefile("UV_TOOL_DIR=.uv-tools $(UV_GATE) prepare")
    }),
    "cache_workflow_env": Scenario({
        ".github/workflows/ci.yml": workflow(
            "make lint", env="    env:\n      UV_CACHE_DIR: .uv-cache\n"
        )
    }),
    "cache_workflow_script": Scenario({
        ".github/workflows/ci.yml": workflow(
            'echo "UV_CACHE_DIR=$PWD/.uv-cache" >> "$GITHUB_ENV"'
        )
    }),
    "cache_action_env": Scenario({
        ".github/actions/setup/action.yml": (
            "name: setup\nruns:\n  using: composite\n  steps:\n"
            "    - shell: bash\n      run: make prepare\n"
            "      env:\n        UV_TOOL_DIR: .uv-tools\n"
        )
    }),
    # UV-003
    "bypass_uv_run": Scenario({"Makefile": makefile("uv run --group dev pytest")}),
    "bypass_uv_sync": Scenario({"Makefile": makefile("@uv sync --locked")}),
    "bypass_uv_tool_run": Scenario({
        "Makefile": makefile("uv tool run ruff==0.16.4 check .")
    }),
    "bypass_uvx": Scenario({"Makefile": makefile("uvx ruff==0.16.4 check .")}),
    "bypass_dollar_uv": Scenario({
        "Makefile": makefile(
            "$(UV_ENV) $(UV) run pytest", extra="UV := uv\nUV_ENV := FOO=1\n"
        )
    }),
    "bypass_unresolved_uv": Scenario({
        "Makefile": makefile(
            "$(UV) run pytest",
            extra="ifeq ($(A),1)\nUV := uv\nelse\nUV := /opt/uv\nendif\n",
        )
    }),
    "gate_variable_wrong": Scenario({
        "Makefile": COMPLIANT_MAKEFILE.replace(
            "python3 scripts/uv_gate.py", "uv run scripts/uv_gate.py", 1
        )
    }),
    "gate_variable_twice": Scenario({
        "Makefile": COMPLIANT_MAKEFILE + "UV_GATE = python3 other.py\n"
    }),
    "makefile_include": Scenario({
        "Makefile": "include uv.mk\n\n" + COMPLIANT_MAKEFILE
    }),
    "workflow_malformed_only": Scenario({
        "Makefile": "build:\n\tcc -o x x.c\n",
        "scripts/uv_gate.py": None,
        "pyproject.toml": None,
        "uv.lock": None,
        ".github/workflows/ci.yml": "jobs: [unterminated\n",
    }),
    # UV-004
    "lock_missing": Scenario({"uv.lock": None}),
    # UV-005
    "refresh_flag": Scenario({
        "Makefile": makefile("$(UV_GATE) run --refresh -- pytest")
    }),
    "upgrade_flag": Scenario({"Makefile": makefile("$(UV_GATE) prepare --upgrade")}),
    "upgrade_short_flag": Scenario({"Makefile": makefile("$(UV_GATE) prepare -U")}),
    "lock_command": Scenario({"Makefile": makefile("uv lock")}),
    "cache_clean": Scenario({"Makefile": makefile("uv cache clean")}),
    "cache_prune": Scenario({"Makefile": makefile("uv cache prune")}),
    "retry_loop": Scenario({
        "Makefile": makefile("until $(UV_GATE) prepare; do sleep 1; done")
    }),
    "maintenance_lock_ok": Scenario({
        "Makefile": COMPLIANT_MAKEFILE + "\nlock:\n\tuv lock --upgrade\n"
    }),
    # UV-006
    "tool_unpinned": Scenario({
        "Makefile": makefile("$(UV_GATE) tool --from ruff -- ruff check .")
    }),
    "tool_positional_unpinned": Scenario({
        "Makefile": makefile("$(UV_GATE) tool ruff -- check .")
    }),
    "tool_range": Scenario({
        "Makefile": makefile(
            "$(UV_GATE) tool --from 'cibuildwheel>=2.16' -- cibuildwheel"
        )
    }),
    "tool_latest": Scenario({
        "Makefile": makefile("$(UV_GATE) tool --from typos@latest -- typos")
    }),
    "tool_git_branch": Scenario({
        "Makefile": makefile(gated_tool(OTHER_TOOL + "@main", "tool"))
    }),
    "tool_git_tag_other_repo": Scenario({
        "Makefile": makefile(gated_tool(OTHER_TOOL + "@v1.2.3", "tool"))
    }),
    "tool_git_sha": Scenario({
        "Makefile": makefile(gated_tool(f"{OTHER_TOOL}@{SHA}", "tool"))
    }),
    # The compliant base already runs the builder directly at a release tag, the
    # shape spelling-config-baseline (PD-007) requires.
    "tool_git_sha_make_escaped_fragment": Scenario({
        "Makefile": makefile(
            gated_tool(f"{OTHER_TOOL}@{SHA}\\#subdirectory=packages/x", "x")
        )
    }),
    "tool_git_sha_bare_fragment": Scenario({
        "Makefile": makefile(
            gated_tool(f"{OTHER_TOOL}@{SHA}#subdirectory=packages/x", "x")
        )
    }),
    "tool_git_short_sha_make_escaped_fragment": Scenario({
        "Makefile": makefile(
            gated_tool(f"{OTHER_TOOL}@{SHA[:12]}\\#subdirectory=packages/x", "x")
        )
    }),
    "tool_git_sha_with_suffix": Scenario({
        "Makefile": makefile(gated_tool(f"{OTHER_TOOL}@{SHA}zz", "x"))
    }),
    "tool_git_branch_make_escaped_fragment": Scenario({
        "Makefile": makefile(
            gated_tool(f"{OTHER_TOOL}@main\\#subdirectory=packages/x", "x")
        )
    }),
    "tool_typos_builder_tag": Scenario(),
    "tool_typos_builder_tag_via_gate": Scenario({
        "Makefile": makefile(
            gated_tool(BUILDER + "@v0.1.3", "typos-config-builder gate")
        )
    }),
    "tool_other_tag_direct": Scenario({
        "Makefile": makefile(f'uvx --from "{OTHER_TOOL}@v1.2.3" tool')
    }),
    "bypass_beside_release_tag": Scenario({
        "Makefile": makefile(
            "uv sync --locked && "
            f'uvx --from "{BUILDER}@v0.1.3" typos-config-builder gate'
        )
    }),
    "bypass_beside_release_tag_semicolon": Scenario({
        "Makefile": makefile(
            f'uvx --from "{BUILDER}@v0.1.3" typos-config-builder gate ; uv run pytest'
        )
    }),
    "bypass_beside_release_tag_or": Scenario({
        "Makefile": makefile(
            f'uvx --from "{BUILDER}@v0.1.3" typos-config-builder gate || uv run pytest'
        )
    }),
    "bypass_beside_release_tag_pipe": Scenario({
        "Makefile": makefile(
            f'uvx --from "{BUILDER}@v0.1.3" typos-config-builder gate | uv run pytest'
        )
    }),
    "release_tag_beside_release_tag": Scenario({
        "Makefile": makefile(
            f'uvx --from "{BUILDER}@v0.1.3" typos-config-builder gate'
            f' && uvx --from "{BUILDER}@v0.1.3" typos-config-builder gate'
        )
    }),
    "tool_mixed_tags_one_line": Scenario({
        "Makefile": makefile(
            f'uvx --from "{BUILDER}@v0.1.3" typos-config-builder gate'
            f' && uvx --from "{OTHER_TOOL}@v1.2.3" tool'
        )
    }),
    "tool_typos_builder_branch_direct": Scenario({
        "Makefile": makefile(f'uvx --from "{BUILDER}@main" typos-config-builder gate')
    }),
    "tool_typos_builder_sha": Scenario({
        "Makefile": makefile(
            gated_tool(f"{BUILDER}@{SHA}", "typos-config-builder gate")
        )
    }),
    "tool_typos_builder_branch": Scenario({
        "Makefile": makefile(gated_tool(BUILDER + "@main", "typos-config-builder gate"))
    }),
    "tool_pinned_equals": Scenario({
        "Makefile": makefile("$(UV_GATE) tool --from ruff==0.16.4 -- ruff check .")
    }),
    "tool_pinned_at": Scenario({
        "Makefile": makefile("$(UV_GATE) tool typos@1.2.3 -- typos")
    }),
    "tool_variable_resolved": Scenario({
        "Makefile": makefile(
            "$(UV_GATE) tool --from '$(RUFF)' -- ruff check .",
            extra="RUFF := ruff==0.16.4\n",
        )
    }),
    "tool_variable_nested": Scenario({
        "Makefile": makefile(
            "$(UV_GATE) tool --from '$(LINTS)' -- ambrleaks",
            extra=f"LINTS_REF := {SHA}\nLINTS := git+https://github.com/leynos/df12-python-lints.git@$(LINTS_REF)\n",
        )
    }),
    "tool_variable_unresolved": Scenario({
        "Makefile": makefile(
            "$(UV_GATE) tool --from '$(RUFF)' -- ruff check .",
            extra="ifeq ($(A),1)\nRUFF := ruff==1\nelse\nRUFF := ruff==2\nendif\n",
        )
    }),
    "tool_variable_unpinned": Scenario({
        "Makefile": makefile(
            "$(UV_GATE) tool --from '$(RUFF)' -- ruff check .", extra="RUFF := ruff\n"
        )
    }),
    "tool_workflow_unpinned": Scenario({
        ".github/workflows/ci.yml": workflow("uvx --from ruff ruff check .")
    }),
    "tool_uvx_pinned_only_bypass": Scenario({
        "Makefile": makefile("uvx --from ruff==0.16.4 ruff check .")
    }),
    # UV-007
    "git_dep_tag": Scenario({
        "pyproject.toml": with_dependency(CMD_MOX_REQUIREMENT + "@v0.2.0")
    }),
    "git_dep_bare": Scenario({"pyproject.toml": with_dependency("git+" + CMD_MOX)}),
    "git_dep_sha": Scenario({
        "pyproject.toml": with_dependency(f"{CMD_MOX_REQUIREMENT}@{SHA}")
    }),
    "git_source_branch": Scenario({
        "pyproject.toml": pyproject(uv_source(branch="main"))
    }),
    "git_source_tag": Scenario({"pyproject.toml": pyproject(uv_source(tag="v0.2.0"))}),
    "git_source_rev": Scenario({"pyproject.toml": pyproject(uv_source(rev=SHA))}),
    "pyproject_malformed": Scenario({"pyproject.toml": "[project\n"}),
}


def lay_out(scenario: Scenario, checkout: Path) -> None:
    """Populate *checkout* with the base files, overlaid by *scenario*'s.

    Parameters
    ----------
    scenario:
        The scenario whose files compose the checkout.
    checkout:
        An existing empty directory to lay the files out in.
    """
    files = {**BASE, **scenario.files}
    for relative, text in files.items():
        if text is None:
            continue
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def build_fixture_envelope(scenario: Scenario) -> UvGateEnvelope:
    """Return the production envelope for *scenario*, with a stable path.

    Returns
    -------
    UvGateEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so the
        temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="uv-gate-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(scenario, checkout)
        envelope = build_uv_gate_envelope(checkout)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def manifest_parameters() -> dict[str, object]:
    """Return the manifest defaults, with the stand-in helper's digest.

    Returns
    -------
    dict[str, object]
        The ``defaults`` of ``rule.yaml`` with ``gate_digests`` replaced.
    """
    manifest = YAML(typ="safe").load(MANIFEST_PATH.read_text(encoding="utf-8"))
    parameters = dict(manifest["parameters"]["defaults"])
    parameters["gate_digests"] = {
        "fixture": GATE_DIGEST,
        "older": OLDER_GATE_DIGEST,
    }
    return parameters


def main() -> None:
    """Regenerate every envelope and the bundled data document.

    Writes one JSON file per scenario under ``envelopes/`` and the bundle
    ``data.json`` that ``conftest verify --data`` consumes. Existing files are
    overwritten, so the pinned ``makeutil`` must be on PATH.
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
