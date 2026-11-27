"""Specify the derivation of the canon's Rust lint baseline data.

The committed `baseline.json` is what the policies read; `baseline.toml` is what
a repository copies. These tests hold the two together by building the document
again from the TOML, so a forgotten regeneration fails here rather than leaving
the rule and the paste-ready file saying different things.
"""

from __future__ import annotations

import itertools
import json
import pathlib
import typing as typ

import pytest

from concordat.rules.lint_baseline import BASELINE_TABLES, baseline_document
from concordat.rules.runner import _conftest_argv

DATA_DIR: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards"
    / "canon"
    / "lint-rules"
    / "rust-lint-tables"
    / "data"
)


def _derived() -> dict[str, typ.Any]:
    """Return the document built from the committed TOML files."""
    return baseline_document(
        (DATA_DIR / "baseline.toml").read_text(encoding="utf-8"),
        (DATA_DIR / "extras.toml").read_text(encoding="utf-8"),
    )


def test_the_committed_json_is_the_derivation_of_the_toml() -> None:
    """Run `data/generate.py` after editing either TOML file."""
    committed = json.loads((DATA_DIR / "baseline.json").read_text(encoding="utf-8"))
    assert committed == _derived(), "baseline.json is stale; run data/generate.py"


def test_every_table_has_entries_and_every_entry_a_level() -> None:
    """A table or lint without a level would be silently unenforced."""
    baseline = _derived()["baseline"]
    for name in BASELINE_TABLES:
        assert baseline[name], f"{name} table is empty"
        for lint, entry in baseline[name].items():
            assert entry["level"] in {"warn", "deny", "forbid"}, (name, lint, entry)


def test_the_two_lints_the_femtologging_extension_lacked_are_required() -> None:
    """Both appear in netsuke's tables and in every full-baseline repository."""
    clippy = _derived()["baseline"]["clippy"]
    assert clippy["missing_assert_message"]["level"] == "deny"
    assert clippy["missing_docs_in_private_items"]["level"] == "deny"


def test_unsafe_code_is_forbidden() -> None:
    """Only a named boundary crate may do better than forbid by being `deny`."""
    assert _derived()["baseline"]["rust"]["unsafe_code"]["level"] == "forbid"


def test_pedantic_keeps_its_priority() -> None:
    """Without priority -1 the group would override the individual lints."""
    assert _derived()["baseline"]["clippy"]["pedantic"] == {
        "level": "warn",
        "priority": -1,
    }


def test_check_cfg_is_not_part_of_the_requirement() -> None:
    """A repository's cfgs are its own; the level is what the rule asks for."""
    assert _derived()["baseline"]["rust"]["unexpected_cfgs"] == {"level": "warn"}


def test_the_pyo3_divergences_are_a_list_the_policy_can_read() -> None:
    """Empty today, by measurement; the schema is in `extras.toml`."""
    assert isinstance(_derived()["baseline"]["pyo3_divergences"], list)


def test_a_missing_table_is_refused() -> None:
    """A baseline without a rustdoc table would pass every package on it."""
    with pytest.raises(ValueError, match=r"workspace\.lints\.rustdoc"):
        baseline_document(
            '[workspace.lints.clippy]\nx = "deny"\n'
            '[workspace.lints.rust]\ny = "deny"\n',
            "pyo3_divergences = []\n[cognitive]\nlint = 'x'\n",
        )


def test_a_lint_that_is_not_a_level_is_refused() -> None:
    """An entry the policy could not compare must not reach the JSON."""
    with pytest.raises(ValueError, match="level"):
        baseline_document(
            "[workspace.lints.clippy]\nx = 3\n"
            '[workspace.lints.rust]\ny = "deny"\n'
            '[workspace.lints.rustdoc]\nz = "deny"\n',
            "pyo3_divergences = []\n[cognitive]\nlint = 'x'\n",
        )


def test_the_runner_loads_a_packages_reference_data_beside_the_parameters() -> None:
    """The policy reads `data.baseline`, so the runner must hand it over."""
    argv = _conftest_argv(
        "rust-lint-tables",
        pathlib.Path("policy"),
        [pathlib.Path("parameters.json"), pathlib.Path("data/baseline.json")],
        [pathlib.Path("envelope.json")],
    )
    pairs = list(itertools.pairwise(argv))
    assert ("--data", "data/baseline.json") in pairs, argv
    assert ("--data", "parameters.json") in pairs, argv
