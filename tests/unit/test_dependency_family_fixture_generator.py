"""Unit tests for the dependency-family-contracts fixture generator.

`fixtures/generate.py` lays each scenario out as a checkout and records the
envelope the production builder produces, so the Rego suite is tested on what
`concordat artefact rule run` would send. These tests pin that the checked-in
`data.json` is what regeneration produces, and that no scenario leaks the
temporary directory it was laid out in.

The rule package directory contains a hyphen, so it is not an importable
package name; the module is loaded from its path instead.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import typing as typ

if typ.TYPE_CHECKING:
    import types

_PACKAGE_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / "dependency-family-contracts"
)
_GENERATE_PATH = _PACKAGE_DIR / "fixtures" / "generate.py"


def _load_generator() -> types.ModuleType:
    """Import `fixtures/generate.py` by path and return the module."""
    spec = importlib.util.spec_from_file_location(
        "dependency_family_contracts_generate", _GENERATE_PATH
    )
    if spec is None or spec.loader is None:
        message = f"could not load a module spec from {_GENERATE_PATH}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_checked_in_fixtures_are_what_regeneration_produces() -> None:
    """A stale `data.json` would make the Rego suite test a document that is gone."""
    generator = _load_generator()

    assert (_PACKAGE_DIR / "fixtures" / "data.json").read_text(
        encoding="utf-8"
    ) == generator.render()


def test_no_scenario_records_the_temporary_checkout() -> None:
    """Every envelope names the checkout as `.`, and no path leaks into a fact."""
    generator = _load_generator()
    fixtures = json.loads(generator.render())["fixtures"]

    for name, envelope in fixtures.items():
        assert envelope["repository"]["path"] == ".", name
        assert "dependency-family-fixture" not in json.dumps(envelope), name
