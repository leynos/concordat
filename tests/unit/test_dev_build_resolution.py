"""Cargo and rustup discovery selects only the files governing the build."""

from __future__ import annotations

import json
import typing as typ

import pytest

from concordat.rules import runner
from concordat.rules.cargo_config import locate_cargo_config
from concordat.rules.dev_build_context import resolve_dev_context
from concordat.rules.rust_surfaces import resolve_rust_surfaces
from concordat.rules.toolchain import inspect_toolchain
from tests.unit.test_dev_build_footprint import DISABLED, FLAGS, RULE_ID, write_checkout

if typ.TYPE_CHECKING:
    import pathlib


def _declare_nested_surface(root: pathlib.Path) -> None:
    """Select a nested Cargo root independently of any ancestor manifest."""
    (root / ".concordat").write_text(
        "language:\n  rust:\n    surfaces:\n      - path: rust/Cargo.toml\n",
        encoding="utf-8",
    )


def test_shared_cargo_discovery_prefers_the_extensionless_config(
    tmp_path: pathlib.Path,
) -> None:
    """A migration leaving both filenames must keep Cargo's legacy precedence."""
    write_checkout(tmp_path)
    legacy = tmp_path / ".cargo/config"
    legacy.write_text("[profile.dev]\ndebug = 2\n", encoding="utf-8")
    location = locate_cargo_config(tmp_path)
    assert location.path == legacy, location
    assert location.read_error is None, location


def test_policy_audits_the_config_cargo_actually_selects(
    tmp_path: pathlib.Path,
) -> None:
    """A safe suffixed file cannot hide larger defaults in the active config."""
    write_checkout(tmp_path)
    (tmp_path / ".cargo/config").write_text(
        FLAGS + "[profile.dev]\ndebug = 2\nincremental = true\n", encoding="utf-8"
    )
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {finding.rule_id for finding in result.findings} == {"DF-001", "DF-002"}, (
        result
    )
    assert result.verdict == "noncompliant", result


@pytest.mark.parametrize(
    ("legacy", "suffixed", "expected"),
    [
        pytest.param("stable\n", "nightly-2026-05-28", "stable", id="plain-stable"),
        pytest.param(
            "nightly-2026-05-28\n", "stable", "nightly-2026-05-28", id="plain-nightly"
        ),
        pytest.param(
            '[toolchain]\nchannel = "stable"\n',
            "nightly-2026-05-28",
            "stable",
            id="toml-in-legacy",
        ),
    ],
)
def test_legacy_pin_wins_over_a_conflicting_suffixed_pin(
    tmp_path: pathlib.Path, legacy: str, suffixed: str, expected: str
) -> None:
    """The channel governing metadata comes from rustup's selected filename."""
    config = "" if expected == "stable" else FLAGS
    write_checkout(tmp_path, config=config, channel=suffixed)
    (tmp_path / "rust-toolchain").write_text(legacy, encoding="utf-8")
    surface = resolve_rust_surfaces(tmp_path).surfaces[0]
    context = resolve_dev_context(tmp_path, surface)
    assert context.channel == expected, context
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "compliant", result


def test_a_nearer_valid_pin_shadows_a_malformed_ancestor(
    tmp_path: pathlib.Path,
) -> None:
    """A file rustup never consults must contribute neither channel nor errors."""
    write_checkout(tmp_path)
    (tmp_path / "rust-toolchain.toml").write_text("[toolchain\n", encoding="utf-8")
    write_checkout(tmp_path / "rust")
    _declare_nested_surface(tmp_path)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "compliant", result


def test_a_malformed_nearest_pin_does_not_fall_back(tmp_path: pathlib.Path) -> None:
    """Unreadable selected evidence stays indeterminate despite a safe ancestor."""
    write_checkout(tmp_path)
    write_checkout(tmp_path / "rust")
    (tmp_path / "rust/rust-toolchain.toml").write_text("[toolchain\n", encoding="utf-8")
    _declare_nested_surface(tmp_path)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "indeterminate", result
    assert any(finding.rule_id == "DF-000" for finding in result.findings), result


def test_a_selected_legacy_pin_cannot_read_outside_the_checkout(
    tmp_path: pathlib.Path,
) -> None:
    """Legacy filename precedence must preserve the repository-only boundary."""
    checkout = tmp_path / "repo"
    write_checkout(checkout, config="", channel="stable")
    outside = tmp_path / "outside-pin"
    outside.write_text("stable\n", encoding="utf-8")
    (checkout / "rust-toolchain").symlink_to(outside)
    result = runner.run_rule(RULE_ID, checkout)
    assert result.verdict == "indeterminate", result
    assert any(finding.rule_id == "DF-000" for finding in result.findings), result


@pytest.mark.parametrize("excluded", [False, True], ids=["member", "excluded"])
def test_exact_workspace_exclusions_allow_standalone_profile_audits(
    tmp_path: pathlib.Path, *, excluded: bool
) -> None:
    """An exact excluded root owns its profile; a workspace member does not."""
    write_checkout(
        tmp_path / "rust", profile=DISABLED.replace("debug = 0", "debug = 2")
    )
    membership = 'exclude = ["rust"]' if excluded else 'members = ["rust"]'
    (tmp_path / "Cargo.toml").write_text(
        f"[workspace]\n{membership}\n", encoding="utf-8"
    )
    _declare_nested_surface(tmp_path)
    result = runner.run_rule(RULE_ID, tmp_path)
    expected = {"DF-002"} if excluded else {"DF-000"}
    assert {finding.rule_id for finding in result.findings} == expected, result
    assert result.verdict == ("noncompliant" if excluded else "indeterminate"), result


@pytest.mark.parametrize(
    ("flags", "source", "expected"),
    [
        pytest.param(["-g"], "build", {"DF-002"}, id="short-debug"),
        pytest.param(["-g", "-Cdebuginfo=0"], "build", set(), id="later-disable"),
        pytest.param(["-Cdebuginfo=0", "-g"], "build", {"DF-002"}, id="later-enable"),
        pytest.param(["-g"], "profile", {"DF-002"}, id="additive-short-debug"),
    ],
)
def test_short_debug_flags_obey_rustc_assignment_order(
    tmp_path: pathlib.Path, flags: list[str], source: str, expected: set[str]
) -> None:
    """The standalone debug alias must participate in the last-option-wins rule."""
    config = FLAGS.replace("[]", json.dumps(flags))
    if source == "profile":
        config = FLAGS + "[profile.dev]\nrustflags = " + json.dumps(flags)
    write_checkout(tmp_path, config=config)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {finding.rule_id for finding in result.findings} == expected, result


def test_canonical_toolchain_inspection_still_targets_the_toml_pin(
    tmp_path: pathlib.Path,
) -> None:
    """Effective build discovery does not redefine the existing canonical policy."""
    write_checkout(tmp_path, channel="stable")
    (tmp_path / "rust-toolchain").write_text("nightly-2026-05-28\n", encoding="utf-8")
    facts = inspect_toolchain(tmp_path)
    assert facts is not None, "the canonical TOML pin remains readable"
    assert facts["channel"] == "stable", facts
    assert facts["path"] == "rust-toolchain.toml", facts


def test_a_malformed_selected_legacy_pin_does_not_fall_back(
    tmp_path: pathlib.Path,
) -> None:
    """Filename precedence applies to parse failures as well as valid channels."""
    write_checkout(tmp_path, config="", channel="stable")
    (tmp_path / "rust-toolchain").write_text("[toolchain\n", encoding="utf-8")
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "indeterminate", result
    assert any(finding.rule_id == "DF-000" for finding in result.findings), result
