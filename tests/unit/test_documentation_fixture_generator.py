"""Unit tests for the documentation-presence fixture generator.

`fixtures/generate.py` lays each scenario out as a checkout and records the
envelope the production builder produces, so what it emits is what the Rego
suite is tested on. These tests describe the generator's own decisions: what a
scenario overlays on the compliant base, and that the checked-in envelopes are
what regeneration would produce.

The rule package directory contains a hyphen, so it is not an importable
package name; the module is loaded from its path instead.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
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
    / "documentation-presence"
)
_GENERATE_PATH = _PACKAGE_DIR / "fixtures" / "generate.py"


def _load_generator() -> types.ModuleType:
    """Import `fixtures/generate.py` by path and return the module."""
    spec = importlib.util.spec_from_file_location(
        "documentation_presence_generate", _GENERATE_PATH
    )
    if spec is None or spec.loader is None:
        message = f"could not load a module spec from {_GENERATE_PATH}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve string annotations through `sys.modules`, so the
    # module must be registered before its body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generate() -> types.ModuleType:
    """Return the loaded fixture-generator module."""
    return _load_generator()


def test_the_default_scenario_installs_the_compliant_base(
    generate: types.ModuleType, tmp_path: pathlib.Path
) -> None:
    """With nothing overlaid, the checkout is the fully compliant one."""
    generate.lay_out(generate.Scenario(), tmp_path)

    assert (tmp_path / "Makefile").read_text(encoding="utf-8") == generate.makefile()
    for relative in generate.PYTHON_FILES:
        assert (tmp_path / relative).is_file(), relative


def test_a_none_overlay_removes_a_base_file(
    generate: types.ModuleType, tmp_path: pathlib.Path
) -> None:
    """`None` deletes, so a scenario can leave out the Makefile or the Python."""
    generate.lay_out(generate.Scenario({"Makefile": None}), tmp_path)

    assert not (tmp_path / "Makefile").exists()
    assert (tmp_path / "pkg" / "core.py").is_file()


def test_an_overlay_replaces_a_base_file(
    generate: types.ModuleType, tmp_path: pathlib.Path
) -> None:
    """A scenario's text wins over the base's."""
    generate.lay_out(generate.Scenario({"Makefile": "x:\n"}), tmp_path)

    assert (tmp_path / "Makefile").read_text(encoding="utf-8") == "x:\n"


def test_the_bundle_parameters_are_the_manifest_defaults(
    generate: types.ModuleType,
) -> None:
    """The bundle's parameters are exactly what a real run would pass."""
    parameters = generate.manifest_parameters()

    assert parameters["required_threshold"] == 100
    assert parameters["required_verbosity"] == 2


def test_the_recorded_repository_path_is_stable(generate: types.ModuleType) -> None:
    """The temporary checkout path never leaks into a fixture."""
    envelope = generate.build_fixture_envelope(generate.Scenario())

    assert envelope["repository"]["path"] == ".", envelope["repository"]
    assert envelope["kind"] == "policy-input/documentation-presence"


def test_checked_in_envelopes_match_regeneration(generate: types.ModuleType) -> None:
    """The committed envelopes and bundle are exactly what generation produces.

    This runs the pinned `makeutil` on every Makefile scenario, so drift
    between the committed evidence and the generator (or the pin) fails here
    rather than silently changing what the Rego suite verifies.
    """
    expected = {
        key: generate.build_fixture_envelope(scenario)
        for key, scenario in generate.SCENARIOS.items()
    }
    for key, envelope in expected.items():
        recorded = json.loads(
            (generate.ENVELOPES_DIR / f"{key}.json").read_text(encoding="utf-8")
        )
        assert recorded == envelope, key
    on_disk = {path.stem for path in generate.ENVELOPES_DIR.glob("*.json")}
    assert on_disk == set(expected), "an envelope is orphaned or missing"
    bundle = json.loads(
        (generate.FIXTURES_DIR / "data.json").read_text(encoding="utf-8")
    )
    assert bundle == {
        "fixtures": expected,
        "parameters": generate.manifest_parameters(),
    }


def test_a_rust_scenario_removes_the_python_base(
    generate: types.ModuleType, tmp_path: pathlib.Path
) -> None:
    """Rust scenarios change only the `missing_docs` facts, never Python's."""
    generate.lay_out(generate.rust_scenario({"Cargo.toml": "[package]\n"}), tmp_path)

    assert not list(tmp_path.rglob("*.py"))
    assert not (tmp_path / "pyproject.toml").exists()
    assert "clippy" in (tmp_path / "Makefile").read_text(encoding="utf-8")
    assert (tmp_path / "Cargo.toml").exists()
