"""Cargo must coordinate metadata emission rather than receive a raw rustc flag."""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from concordat.rules import runner
from tests.unit.test_dev_build_footprint import RULE_ID, write_checkout

if typ.TYPE_CHECKING:
    import pathlib

OLD = "[unstable]\nno-embed-metadata = true\n"
NEW = "[unstable]\nembed-metadata = false\n"
RAW = '[build]\nrustflags = ["-Zembed-metadata=no"]\n'


@dataclasses.dataclass(frozen=True, slots=True)
class NativeMetadataCase:
    """A Cargo capability window, configuration, and operator-facing verdict."""

    channel: str
    config: str
    verdict: str


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            NativeMetadataCase("nightly-2025-06-26", OLD, "compliant"), id="old-floor"
        ),
        pytest.param(
            NativeMetadataCase("nightly-2025-07-22", OLD, "compliant"), id="old-native"
        ),
        pytest.param(
            NativeMetadataCase("nightly-2026-07-18", OLD, "compliant"), id="old-end"
        ),
        pytest.param(
            NativeMetadataCase("nightly-2026-07-29", NEW, "compliant"), id="new-floor"
        ),
        pytest.param(
            NativeMetadataCase("nightly-2026-10-20", NEW, "compliant"), id="new-native"
        ),
        pytest.param(
            NativeMetadataCase("nightly-2026-07-19", OLD, "indeterminate"),
            id="transition-start",
        ),
        pytest.param(
            NativeMetadataCase("nightly-2026-07-28", NEW, "indeterminate"),
            id="transition-end",
        ),
        pytest.param(
            NativeMetadataCase("nightly-2026-10-20", OLD, "noncompliant"),
            id="old-key-on-new",
        ),
        pytest.param(
            NativeMetadataCase("nightly-2025-07-22", NEW, "noncompliant"),
            id="new-key-on-old",
        ),
        pytest.param(
            NativeMetadataCase("nightly-2025-07-22", RAW, "noncompliant"),
            id="unsafe-raw-only",
        ),
        pytest.param(
            NativeMetadataCase("nightly-2025-07-22", OLD + RAW, "noncompliant"),
            id="native-plus-raw",
        ),
        pytest.param(
            NativeMetadataCase(
                "nightly-2025-07-22", OLD + RAW.replace("=no", "=yes"), "noncompliant"
            ),
            id="raw-reenables",
        ),
        pytest.param(
            NativeMetadataCase(
                "nightly-2025-07-22",
                OLD + '[target.x86_64-unknown-linux-gnu]\nrustflags = ["-Dwarnings"]\n',
                "compliant",
            ),
            id="native-covers-target",
        ),
        pytest.param(
            NativeMetadataCase(
                "nightly-2025-07-22", OLD.replace("true", '"true"'), "indeterminate"
            ),
            id="invalid-boolean",
        ),
        pytest.param(
            NativeMetadataCase("stable", NEW, "compliant"), id="stable-ignores-unstable"
        ),
        pytest.param(
            NativeMetadataCase("beta", OLD, "compliant"), id="beta-ignores-unstable"
        ),
        pytest.param(
            NativeMetadataCase("stable", NEW + RAW, "noncompliant"),
            id="stable-rejects-raw",
        ),
        pytest.param(
            NativeMetadataCase(
                "nightly-2025-07-22", OLD.replace("true", "false"), "noncompliant"
            ),
            id="old-false-enables",
        ),
        pytest.param(
            NativeMetadataCase(
                "nightly-2026-10-20", NEW.replace("false", "true"), "noncompliant"
            ),
            id="new-true-enables",
        ),
    ],
)
def test_native_cargo_metadata_coordination(
    tmp_path: pathlib.Path, case: NativeMetadataCase
) -> None:
    """Native Cargo controls pass; raw compiler disabling alone remains unsafe."""
    write_checkout(tmp_path, channel=case.channel, config=case.config)
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == case.verdict, result
    expected = set() if case.verdict == "compliant" else {"DF-003"}
    assert {finding.rule_id for finding in result.findings} == expected, result


def test_native_metadata_configuration_obeys_nested_precedence(
    tmp_path: pathlib.Path,
) -> None:
    """A nearer native Cargo setting can undo an inherited safe control."""
    write_checkout(tmp_path, config=OLD)
    write_checkout(tmp_path / "rust", config=OLD.replace("true", "false"))
    (tmp_path / ".concordat").write_text(
        "language:\n  rust:\n    surfaces:\n      - path: rust/Cargo.toml\n",
        encoding="utf-8",
    )
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "noncompliant", result
    assert {finding.rule_id for finding in result.findings} == {"DF-003"}, result
