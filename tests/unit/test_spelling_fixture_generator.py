"""Unit tests for the spelling-config-baseline fixture generator.

`fixtures/generate.py` lays each scenario out as a checkout and records the
envelope the production builder produces, so what it emits is what the Rego
suite is tested on. These tests describe the generator's own decisions: which
files a scenario installs, that every fixture file is used by some scenario,
and that the checked-in envelopes are what regeneration would produce.

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
    / "spelling-config-baseline"
)
_GENERATE_PATH = _PACKAGE_DIR / "fixtures" / "generate.py"


def _load_generator() -> types.ModuleType:
    """Import `fixtures/generate.py` by path and return the module."""
    spec = importlib.util.spec_from_file_location(
        "spelling_config_baseline_generate", _GENERATE_PATH
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


def test_the_default_scenario_installs_every_file(
    generate: types.ModuleType, tmp_path: pathlib.Path
) -> None:
    """The default scenario is the fully compliant checkout."""
    generate.lay_out(generate.Scenario(), tmp_path)

    assert (tmp_path / "Makefile").read_text() == (
        generate.MAKEFILES_DIR / "compliant.mk"
    ).read_text()
    assert (tmp_path / "typos.local.toml").read_text() == "schema = 1\n"
    assert (tmp_path / ".gitignore").read_text() == (
        generate.GITIGNORES_DIR / "complete.gitignore"
    ).read_text()
    assert (tmp_path / "typos.toml").is_file()
    assert (tmp_path / ".github" / "workflows" / "ci.yml").is_file()


def test_the_bare_scenario_leaves_only_prose(
    generate: types.ModuleType, tmp_path: pathlib.Path
) -> None:
    """With nothing named, the checkout holds only its README."""
    generate.lay_out(generate.BARE, tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["README.md"]


def _used_stems(generate: types.ModuleType, field: str) -> set[str]:
    """Return the fixture stems the scenarios name through one field."""
    stems: set[str] = set()
    for scenario in generate.SCENARIOS.values():
        value = getattr(scenario, field)
        if field == "workflows":
            stems.update(value.values())
        elif value:
            stems.add(value)
    return stems


def _present_stems(directory: pathlib.Path, suffix: str) -> set[str]:
    """Return the stems of the fixture files in one directory."""
    return {path.stem for path in directory.glob(f"*{suffix}")}


@pytest.mark.parametrize(
    ("field", "directory", "suffix"),
    [
        ("makefile", "MAKEFILES_DIR", ".mk"),
        ("overlay", "OVERLAYS_DIR", ".toml"),
        ("gitignore", "GITIGNORES_DIR", ".gitignore"),
        ("workflows", "WORKFLOWS_DIR", ".yml"),
        ("agents", "AGENTS_DIR", ".agents"),
    ],
)
def test_every_fixture_file_is_used_by_a_scenario(
    generate: types.ModuleType, field: str, directory: str, suffix: str
) -> None:
    """An orphaned fixture file is a behaviour nobody tests."""
    present = _present_stems(getattr(generate, directory), suffix)
    assert _used_stems(generate, field) == present, field


def test_the_recorded_repository_path_is_stable(generate: types.ModuleType) -> None:
    """The temporary checkout path never leaks into a fixture."""
    envelope = generate.build_fixture_envelope(generate.BARE)

    assert envelope["repository"]["path"] == ".", envelope["repository"]
    assert envelope["kind"] == "policy-input/spelling-config-baseline"


def test_checked_in_envelopes_match_regeneration(generate: types.ModuleType) -> None:
    """The committed envelopes and bundle are exactly what generation produces.

    This runs the pinned `makeutil` on every Makefile fixture, so drift between
    the committed evidence and the generator (or the pin) fails here rather
    than silently changing what the Rego suite verifies.
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
    bundle = json.loads(
        (generate.FIXTURES_DIR / "data.json").read_text(encoding="utf-8")
    )
    assert bundle == {
        "fixtures": expected,
        "parameters": generate.manifest_parameters(),
    }
