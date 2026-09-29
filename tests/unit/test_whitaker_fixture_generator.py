"""Unit tests for the whitaker-provisioning fixture generator.

`fixtures/generate.py` runs the production envelope builder over each checkout
under `fixtures/repos/` and writes the envelopes the Rego suite verifies. These
tests hold the checked-in evidence to what regeneration produces, and hold the
policy suite to using every scenario, so neither can drift while the other
still passes.

The rule package directory contains a hyphen, so it is not an importable
package name; the module is loaded from its path instead.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    import types

_PACKAGE_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / "whitaker-provisioning"
)
_GENERATE_PATH = _PACKAGE_DIR / "fixtures" / "generate.py"
_POLICY_TEST = _PACKAGE_DIR / "policy" / "whitaker_provisioning_test.rego"


def _load_generator() -> types.ModuleType:
    """Import `fixtures/generate.py` by path and return the module."""
    spec = importlib.util.spec_from_file_location(
        "whitaker_provisioning_generate", _GENERATE_PATH
    )
    if spec is None or spec.loader is None:
        message = f"could not load a module spec from {_GENERATE_PATH}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generate() -> types.ModuleType:
    """Return the loaded fixture-generator module."""
    return _load_generator()


def test_checked_in_envelopes_match_regeneration(generate: types.ModuleType) -> None:
    """The committed envelopes and bundle are exactly what generation produces."""
    expected = generate.build_envelopes()
    for key, envelope in expected.items():
        recorded = json.loads(
            (generate.ENVELOPES_DIR / f"{key}.json").read_text(encoding="utf-8")
        )
        assert recorded == envelope, key
    checked_in = {path.stem for path in generate.ENVELOPES_DIR.glob("*.json")}
    assert checked_in == set(expected)
    bundle = json.loads(
        (generate.FIXTURES_DIR / "data.json").read_text(encoding="utf-8")
    )
    assert bundle == {"fixtures": expected}


def test_every_scenario_is_exercised_by_the_policy_suite(
    generate: types.ModuleType,
) -> None:
    """A fixture no Rego test names would verify nothing."""
    used = set(re.findall(r"data\.fixtures\.(\w+)", _POLICY_TEST.read_text("utf-8")))
    assert set(generate.build_envelopes()) - used == set()
