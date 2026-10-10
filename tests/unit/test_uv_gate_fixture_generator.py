"""Unit tests for the uv-gate-baseline fixture generator.

`fixtures/generate.py` lays each scenario out as a checkout and records the
envelope the production builder produces, so what it emits is what the Rego
suite is tested on. These tests describe the generator's own decisions: what a
scenario overlays on the compliant base, and that the checked-in envelopes are
what regeneration would produce.

The rule package directory contains a hyphen, so it is not an importable
package name; the module is loaded from its path instead.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
import typing as typ

import pytest
from ruamel.yaml import YAML

if typ.TYPE_CHECKING:
    import types

_PACKAGE_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / "uv-gate-baseline"
)
_GENERATE_PATH = _PACKAGE_DIR / "fixtures" / "generate.py"


def _load_generator() -> types.ModuleType:
    """Import `fixtures/generate.py` by path and return the module."""
    spec = importlib.util.spec_from_file_location(
        "uv_gate_baseline_generate", _GENERATE_PATH
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

    assert (tmp_path / "Makefile").read_text() == generate.COMPLIANT_MAKEFILE
    assert (tmp_path / "scripts" / "uv_gate.py").read_text() == generate.GATE
    assert (tmp_path / "uv.lock").is_file()
    assert (tmp_path / ".github" / "workflows" / "ci.yml").is_file()


def test_a_none_overlay_removes_a_base_file(
    generate: types.ModuleType, tmp_path: pathlib.Path
) -> None:
    """`None` deletes, so a scenario can leave out the lock or the helper."""
    generate.lay_out(generate.Scenario({"uv.lock": None}), tmp_path)

    assert not (tmp_path / "uv.lock").exists()
    assert (tmp_path / "Makefile").is_file()


def test_an_overlay_replaces_a_base_file(
    generate: types.ModuleType, tmp_path: pathlib.Path
) -> None:
    """A scenario's text wins over the base's."""
    generate.lay_out(generate.Scenario({"Makefile": "x:\n"}), tmp_path)

    assert (tmp_path / "Makefile").read_text() == "x:\n"


def test_the_stand_in_helper_digest_is_the_one_the_bundle_accepts(
    generate: types.ModuleType,
) -> None:
    """The bundle's parameters accept exactly the stand-in the scenarios vendor."""
    parameters = generate.manifest_parameters()

    assert parameters["gate_digests"] == {
        "fixture": generate.GATE_DIGEST,
        "older": generate.OLDER_GATE_DIGEST,
    }
    assert parameters["forbidden_variables"] == ["UV_CACHE_DIR", "UV_TOOL_DIR"]


def test_the_recorded_repository_path_is_stable(generate: types.ModuleType) -> None:
    """The temporary checkout path never leaks into a fixture."""
    envelope = generate.build_fixture_envelope(generate.Scenario())

    assert envelope["repository"]["path"] == ".", envelope["repository"]
    assert envelope["kind"] == "policy-input/uv-gate-baseline"


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


def test_the_vendored_test_copy_is_the_canonical_helper() -> None:
    """The copy the behavioural tests vendor must be the digest the rule accepts."""
    manifest = YAML(typ="safe").load((_PACKAGE_DIR / "rule.yaml").read_text())
    accepted = set(manifest["parameters"]["defaults"]["gate_digests"].values())
    copy = (
        _PACKAGE_DIR.parents[3] / "tests" / "fixtures" / "uv_gate" / "uv_gate.py.canon"
    )

    assert hashlib.sha256(copy.read_bytes()).hexdigest() in accepted
