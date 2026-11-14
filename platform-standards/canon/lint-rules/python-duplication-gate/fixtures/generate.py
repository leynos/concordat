"""Regenerate the policy-input fixtures for python-duplication-gate.

Each scenario lays out a synthetic checkout from the file contents written
below, then hands it to the production ``build_duplication_gate_envelope`` so the
recorded envelopes are exactly what ``concordat artefact rule run`` would send
to Conftest. ``data.json`` bundles every envelope under a ``fixtures`` key for
``conftest verify``, beside the manifest's default parameters.

Run from the repository root::

    uv run python \
        platform-standards/canon/lint-rules/python-duplication-gate/fixtures/generate.py
"""

from __future__ import annotations

import json
import tempfile
import typing as typ
from pathlib import Path

from ruamel.yaml import YAML

from concordat.rules.duplication_gate_envelope import (
    DuplicationGateEnvelope,
    build_duplication_gate_envelope,
)

FIXTURES_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = FIXTURES_DIR.parent / "rule.yaml"

# The recorded repository path must not leak the temporary directory the
# scenario was laid out in; every envelope names the checkout as `.`.
RECORDED_REPOSITORY_PATH: typ.Final = "."

CODE = "def run() -> None:\n    pass\n"


def nose_table(
    *,
    roots: str = '["pkg"]',
    top: str | None = "0",
    exclude: str | None = '["**/tests/**"]',
) -> str:
    """Return a ``pyproject.toml`` with a ``[tool.nose]`` table.

    Each argument is TOML source for that key; ``None`` omits the key.

    Returns
    -------
    str
        The pyproject text.
    """
    lines = [
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        "[tool.nose]",
        'version = "0.20.0"',
        f"roots = {roots}",
    ]
    if top is not None:
        lines.append(f"top = {top}")
    if exclude is not None:
        lines.append(f"exclude = {exclude}")
    return "\n".join(lines) + "\n"


# Every scenario starts from this checkout; a scenario's files overlay it and a
# value of ``None`` removes the file.
BASE: typ.Final[dict[str, str | None]] = {
    "pyproject.toml": nose_table(),
    "pkg/__init__.py": CODE,
    "pkg/mod.py": CODE,
    "pkg/tests/test_mod.py": CODE,
}

SCENARIOS: typ.Final[dict[str, dict[str, str | None]]] = {
    "compliant": {},
    "compliant_anchored_excludes": {
        "pyproject.toml": nose_table(exclude='["tests/**", "unittests/"]'),
        "pkg/unittests/test_other.py": CODE,
    },
    "compliant_file_root": {
        "pyproject.toml": nose_table(roots='["pkg", "tool.py"]'),
        "tool.py": CODE,
    },
    "compliant_test_tree_without_python": {
        "pyproject.toml": nose_table(exclude=None),
        "pkg/tests/test_mod.py": None,
        "pkg/tests/data.json": "{}\n",
    },
    "compliant_hidden_and_vendored_test_trees": {
        "pyproject.toml": nose_table(exclude=None),
        "pkg/tests/test_mod.py": None,
        "pkg/.venv/tests/test_dep.py": CODE,
        "pkg/node_modules/tests/test_dep.py": CODE,
    },
    "compliant_file_root_beside_test_tree": {
        "pyproject.toml": nose_table(roots='["pkg/mod.py"]', exclude=None),
    },
    "top_absent": {"pyproject.toml": nose_table(top=None)},
    "top_positive": {"pyproject.toml": nose_table(top="30")},
    "top_string": {"pyproject.toml": nose_table(top='"0"')},
    "top_positive_and_test_tree": {
        "pyproject.toml": nose_table(top="30", exclude=None)
    },
    "test_tree_not_excluded": {"pyproject.toml": nose_table(exclude=None)},
    "test_tree_partly_excluded": {
        "pyproject.toml": nose_table(exclude='["tests/unit/"]'),
        "pkg/tests/unit/test_unit.py": CODE,
        "pkg/tests/integration/test_integration.py": CODE,
    },
    "test_tree_exclude_names_repository_path": {
        # Patterns are relative to each scan root, as the detector reads them,
        # so a repository-relative pattern excludes nothing.
        "pyproject.toml": nose_table(exclude='["pkg/tests/**"]'),
    },
    "test_tree_reincluded_by_negation": {
        "pyproject.toml": nose_table(exclude='["tests/**", "!tests/test_mod.py"]'),
    },
    "test_tree_unittests_not_excluded": {
        "pyproject.toml": nose_table(exclude=None),
        "pkg/tests/test_mod.py": None,
        "pkg/unittests/test_mod.py": CODE,
    },
    "root_is_test_tree": {
        "pyproject.toml": nose_table(roots='["tests"]', exclude=None),
        "tests/test_mod.py": CODE,
    },
    "root_inside_test_tree": {
        "pyproject.toml": nose_table(roots='["tests/helpers"]', exclude=None),
        "tests/helpers/shared.py": CODE,
    },
    "root_is_test_tree_and_excluded": {
        "pyproject.toml": nose_table(roots='["tests"]', exclude='["*.py"]'),
        "tests/test_mod.py": CODE,
    },
    "root_absent": {"pyproject.toml": nose_table(roots='["pkg", "missing"]')},
    "root_selects_nothing": {
        "pyproject.toml": nose_table(roots='["pkg", "docs"]'),
        "docs/guide.md": "# Guide\n",
    },
    "root_excluded_entirely": {
        "pyproject.toml": nose_table(exclude='["**/*.py"]'),
        "pkg/tests/test_mod.py": None,
    },
    "root_is_not_python_file": {
        "pyproject.toml": nose_table(roots='["pkg", "data.json"]'),
        "data.json": "{}\n",
    },
    "no_pyproject": {"pyproject.toml": None},
    "no_nose_table": {
        "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.0"\n'
    },
    "pyproject_invalid": {"pyproject.toml": "[tool.nose\nroots = \n"},
    "nose_not_a_table": {"pyproject.toml": "[tool]\nnose = 3\n"},
    "roots_not_a_list": {"pyproject.toml": nose_table(roots='"pkg"')},
    "roots_empty": {"pyproject.toml": nose_table(roots="[]")},
    "roots_absolute": {"pyproject.toml": nose_table(roots='["/etc"]')},
    "roots_leave_repository": {"pyproject.toml": nose_table(roots='["../elsewhere"]')},
    "exclude_not_a_list": {"pyproject.toml": nose_table(exclude='"tests/**"')},
    "exclude_invalid_pattern": {"pyproject.toml": nose_table(exclude='["tests\\\\"]')},
}


def lay_out(files: dict[str, str | None], checkout: Path) -> None:
    """Populate *checkout* with the base files, overlaid by *files*.

    Parameters
    ----------
    files:
        The scenario's files; ``None`` removes a base file.
    checkout:
        An existing empty directory to lay the files out in.
    """
    for relative, text in {**BASE, **files}.items():
        if text is None:
            continue
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def manifest_parameters() -> dict[str, object]:
    """Return the manifest defaults the rule runs with.

    Returns
    -------
    dict[str, object]
        The ``defaults`` of ``rule.yaml``.
    """
    manifest = YAML(typ="safe").load(MANIFEST_PATH.read_text(encoding="utf-8"))
    return dict(manifest["parameters"]["defaults"])


def build_fixture_envelope(
    files: dict[str, str | None], parameters: dict[str, object]
) -> DuplicationGateEnvelope:
    """Return the production envelope for a scenario, with a stable path.

    Returns
    -------
    DuplicationGateEnvelope
        The recorded envelope, with the checkout path rewritten to ``.`` so the
        temporary directory does not leak into version control.
    """
    with tempfile.TemporaryDirectory(prefix="duplication-gate-fixture-") as scratch:
        checkout = Path(scratch)
        lay_out(files, checkout)
        envelope = build_duplication_gate_envelope(checkout, parameters)
    envelope["repository"]["path"] = RECORDED_REPOSITORY_PATH
    return envelope


def main() -> None:
    """Regenerate ``data.json``, the bundle ``conftest verify --data`` consumes."""
    parameters = manifest_parameters()
    envelopes = {
        key: build_fixture_envelope(files, parameters)
        for key, files in SCENARIOS.items()
    }
    bundle = FIXTURES_DIR / "data.json"
    bundle.write_text(
        json.dumps({"fixtures": envelopes, "parameters": parameters}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
