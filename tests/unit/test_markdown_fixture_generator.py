"""Unit tests for the markdown-formatting-baseline fixture generator.

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
    / "markdown-formatting-baseline"
)
_GENERATE_PATH = _PACKAGE_DIR / "fixtures" / "generate.py"


def _load_generator() -> types.ModuleType:
    """Import `fixtures/generate.py` by path and return the module."""
    spec = importlib.util.spec_from_file_location(
        "markdown_formatting_baseline_generate", _GENERATE_PATH
    )
    if spec is None or spec.loader is None:
        message = f"could not load a module spec from {_GENERATE_PATH}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    # `Scenario` is a dataclass under `from __future__ import annotations`;
    # dataclasses resolve those string annotations through `sys.modules`, so
    # the module must be registered before its body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generate() -> types.ModuleType:
    """Return the loaded fixture-generator module."""
    return _load_generator()


class TestLayOut:
    """A scenario installs exactly the files it names."""

    def test_default_scenario_installs_every_file(
        self, generate: types.ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """The default scenario is the fully compliant checkout."""
        generate.lay_out(generate.Scenario(), tmp_path)
        assert (tmp_path / "README.md").is_file()
        assert (tmp_path / "Makefile").read_text() == (
            generate.MAKEFILES_DIR / "compliant.mk"
        ).read_text()
        assert (tmp_path / ".markdownlint-cli2.jsonc").read_text() == (
            generate.MARKDOWNLINT_DIR / "baseline.jsonc"
        ).read_text()
        assert (tmp_path / ".github" / "workflows" / "ci.yml").read_text() == (
            generate.WORKFLOWS_DIR / "action.yml"
        ).read_text()

    def test_absent_files_are_left_out(
        self, generate: types.ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """`None` and an empty workflow mapping leave the checkout bare."""
        scenario = generate.Scenario(
            makefile=None, markdownlint=None, workflows={}, markdown=False
        )
        generate.lay_out(scenario, tmp_path)
        assert list(tmp_path.iterdir()) == [], list(tmp_path.iterdir())

    def test_alternate_config_is_created_empty(
        self, generate: types.ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """An alternate configuration name is created as an empty file."""
        scenario = generate.Scenario(
            markdownlint=None, alternate_config=".markdownlint.yaml"
        )
        generate.lay_out(scenario, tmp_path)
        assert (tmp_path / ".markdownlint.yaml").read_text() == ""
        assert not (tmp_path / ".markdownlint-cli2.jsonc").exists()


class TestScenarios:
    """The scenario table covers every fixture file and nothing missing."""

    def test_every_fixture_file_is_used_by_a_scenario(
        self, generate: types.ModuleType
    ) -> None:
        """An orphaned fixture file is a behaviour nobody tests."""
        scenarios = generate.SCENARIOS.values()
        used_makefiles = {s.makefile for s in scenarios if s.makefile}
        used_configs = {s.markdownlint for s in scenarios if s.markdownlint}
        used_workflows = {stem for s in scenarios for stem in s.workflows.values()}
        assert used_makefiles == {p.stem for p in generate.MAKEFILES_DIR.glob("*.mk")}
        assert used_configs == {
            p.stem for p in generate.MARKDOWNLINT_DIR.glob("*.jsonc")
        }
        assert used_workflows == {p.stem for p in generate.WORKFLOWS_DIR.glob("*.yml")}

    def test_every_scenario_names_existing_fixture_files(
        self, generate: types.ModuleType
    ) -> None:
        """A scenario cannot point at a fixture that does not exist."""
        for key, scenario in generate.SCENARIOS.items():
            if scenario.makefile:
                assert (generate.MAKEFILES_DIR / f"{scenario.makefile}.mk").is_file(), (
                    key
                )
            if scenario.markdownlint:
                assert (
                    generate.MARKDOWNLINT_DIR / f"{scenario.markdownlint}.jsonc"
                ).is_file(), key
            for stem in scenario.workflows.values():
                assert (generate.WORKFLOWS_DIR / f"{stem}.yml").is_file(), key


class TestBuildFixtureEnvelope:
    """The recorded envelope is the production builder's, with a stable path."""

    def test_repository_path_is_stable(self, generate: types.ModuleType) -> None:
        """The temporary checkout path never leaks into a fixture."""
        scenario = generate.Scenario(makefile=None, markdownlint=None, workflows={})
        envelope = generate.build_fixture_envelope(scenario)
        assert envelope["repository"]["path"] == ".", envelope["repository"]
        assert envelope["kind"] == "policy-input/markdown-formatting-baseline"


def test_checked_in_envelopes_match_regeneration(generate: types.ModuleType) -> None:
    """The committed envelopes and bundle are exactly what generation produces.

    This runs the pinned `makeutil` on every Makefile fixture, so a drift
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
    bundle = json.loads(
        (generate.FIXTURES_DIR / "data.json").read_text(encoding="utf-8")
    )
    assert bundle == {"fixtures": expected}
