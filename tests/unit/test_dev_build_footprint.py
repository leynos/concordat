"""Audit the default development build through parsed files and real policy."""

from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest

from concordat.rules import runner
from concordat.rules.dev_build_envelope import build_dev_build_envelope

RULE_ID = "rust-dev-build-footprint"
MANIFEST = '[package]\nname = "demo"\nversion = "0.1.0"\n'
DISABLED = "[profile.dev]\nincremental = false\ndebug = 0\n"
FLAGS = "[build]\nrustflags = []\n[unstable]\nno-embed-metadata = true\n"


@dataclasses.dataclass(frozen=True, slots=True)
class DevBuildCase:
    """One named input scenario and its expected storage-policy findings."""

    profile: str
    config: str
    channel: str
    expected: set[str]


def write_checkout(
    root: pathlib.Path,
    *,
    profile: str = DISABLED,
    config: str = FLAGS,
    channel: str = "nightly-2026-05-28",
) -> None:
    """Write a synthetic checkout without consulting a host toolchain."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "Cargo.toml").write_text(MANIFEST + profile, encoding="utf-8")
    (root / ".cargo").mkdir(exist_ok=True)
    (root / ".cargo/config.toml").write_text(config, encoding="utf-8")
    (root / "rust-toolchain.toml").write_text(
        f'[toolchain]\nchannel = "{channel}"\n', encoding="utf-8"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            DevBuildCase(DISABLED, FLAGS, "nightly-2026-05-28", set()), id="nightly"
        ),
        pytest.param(DevBuildCase(DISABLED, "", "stable", set()), id="stable"),
        pytest.param(DevBuildCase(DISABLED, "", "beta", set()), id="beta"),
        pytest.param(DevBuildCase(DISABLED, "", "1.99.0", set()), id="numbered-stable"),
        pytest.param(
            DevBuildCase("", FLAGS, "nightly-2026-05-28", {"DF-001", "DF-002"}),
            id="defaults",
        ),
        pytest.param(
            DevBuildCase(DISABLED, "", "nightly-2026-05-28", {"DF-003"}),
            id="metadata-missing",
        ),
        pytest.param(
            DevBuildCase(DISABLED, FLAGS, "nightly-2025-06-25", {"DF-003"}),
            id="old-support-unknown",
        ),
        pytest.param(
            DevBuildCase(DISABLED, FLAGS, "nightly-2025-06-26", set()),
            id="supported-boundary",
        ),
        pytest.param(
            DevBuildCase(DISABLED, FLAGS, "nightly", {"DF-003"}),
            id="floating-support-unknown",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS.replace("[]", '["-Zembed-metadata=no"]'),
                "stable",
                {"DF-003"},
            ),
            id="unstable-flag-on-stable",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED.replace("debug = 0", 'debug = "none"'),
                FLAGS,
                "nightly-2026-05-28",
                set(),
            ),
            id="debug-none",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED.replace("debug = 0", "debug = false"),
                FLAGS,
                "nightly-2026-05-28",
                set(),
            ),
            id="debug-false",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED + "[profile.dev.package.demo]\ndebug = 2\n",
                FLAGS,
                "nightly-2026-05-28",
                {"DF-002"},
            ),
            id="package-debug",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED + '[profile.dev.package."*"]\nincremental = true\n',
                FLAGS,
                "nightly-2026-05-28",
                {"DF-001"},
            ),
            id="wildcard-incremental",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED + "[profile.dev.build-override]\ndebug = true\n",
                FLAGS,
                "nightly-2026-05-28",
                {"DF-002"},
            ),
            id="build-script-debug",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED + "[profile.release]\ndebug = 2\nincremental = true\n",
                FLAGS,
                "nightly-2026-05-28",
                set(),
            ),
            id="release-outside-scope",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS.replace("[]", '["-Z", "embed-metadata=no"]'),
                "nightly-2026-05-28",
                {"DF-003"},
            ),
            id="split-metadata",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS.replace("[]", '["-Cdebuginfo=2"]'),
                "nightly-2026-05-28",
                {"DF-002"},
            ),
            id="flag-debug",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS.replace("[]", '["-Cincremental=cache"]'),
                "nightly-2026-05-28",
                {"DF-001"},
            ),
            id="flag-incremental",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS.replace("[]", '["-Zembed-metadata=no", "-Zembed-metadata=yes"]'),
                "nightly-2026-05-28",
                {"DF-003"},
            ),
            id="last-metadata-wins",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS.replace("[]", '["-Cdebuginfo=2", "-Cdebuginfo=0"]'),
                "nightly-2026-05-28",
                set(),
            ),
            id="last-debug-wins",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS
                + '[target.x86_64-unknown-linux-gnu]\nrustflags = ["-Dwarnings"]\n',
                "nightly-2026-05-28",
                set(),
            ),
            id="target-replacement",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS + "[profile.dev]\ndebug = 2\n",
                "nightly-2026-05-28",
                {"DF-002"},
            ),
            id="config-wins",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS + '[profile.dev]\nrustflags = ["-Cdebuginfo=2"]\n',
                "nightly-2026-05-28",
                {"DF-002"},
            ),
            id="profile-rustflags",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS + '[profile.dev]\nrustflags = ["-Cdebuginfo=0"]\n',
                "nightly-2026-05-28",
                set(),
            ),
            id="additive-profile-flags",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED,
                FLAGS.replace("[build]", "[build]\nincremental = true"),
                "nightly-2026-05-28",
                {"DF-001"},
            ),
            id="global-incremental-wins",
        ),
        pytest.param(
            DevBuildCase(
                "[profile.dev]\ndebug = 0\n",
                FLAGS.replace("[build]", "[build]\nincremental = false"),
                "nightly-2026-05-28",
                set(),
            ),
            id="global-incremental-off",
        ),
        pytest.param(
            DevBuildCase("", FLAGS + DISABLED, "nightly-2026-05-28", set()),
            id="config-defaults",
        ),
        pytest.param(
            DevBuildCase(
                DISABLED.replace("debug = 0", "debug = 0.0"),
                FLAGS,
                "nightly-2026-05-28",
                {"DF-002"},
            ),
            id="malformed-debug",
        ),
    ],
)
def test_real_policy_audits_dev_builds(
    tmp_path: pathlib.Path,
    case: DevBuildCase,
) -> None:
    """Files, default resolution, flag precedence and policy agree end to end."""
    write_checkout(
        tmp_path, profile=case.profile, config=case.config, channel=case.channel
    )
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {finding.rule_id for finding in result.findings} == case.expected, (
        result.findings
    )
    assert (result.verdict == "compliant") == (not case.expected), result.verdict


def test_config_merges_with_manifest_overrides(tmp_path: pathlib.Path) -> None:
    """A config override must retain package settings from the manifest."""
    write_checkout(
        tmp_path,
        profile=DISABLED + "[profile.dev.package.demo]\ndebug = 2\n",
        config=FLAGS + "[profile.dev]\nincremental = false\n",
    )
    envelope = build_dev_build_envelope(tmp_path)
    surface = envelope["surfaces"][0]
    assert surface["profiles"][1]["debug"] == "enabled", surface


def test_nested_surface_uses_ancestor_and_local_config(tmp_path: pathlib.Path) -> None:
    """A declared nested workspace inherits flags but can replace its profile."""
    nested = tmp_path / "rust"
    write_checkout(nested, config="[profile.dev]\ndebug = 2\n")
    (tmp_path / ".concordat").write_text(
        "language:\n  rust:\n    surfaces:\n      - path: rust/Cargo.toml\n",
        encoding="utf-8",
    )
    (tmp_path / ".cargo").mkdir()
    (tmp_path / ".cargo/config.toml").write_text(FLAGS, encoding="utf-8")
    result = runner.run_rule(RULE_ID, tmp_path)
    assert {finding.rule_id for finding in result.findings} == {"DF-002"}, result
    assert all(finding.path == "rust/Cargo.toml" for finding in result.findings), result


def test_an_unreadable_config_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """A present malformed document cannot become an absent configuration."""
    write_checkout(tmp_path, config="[broken")
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "indeterminate", result
    assert {finding.rule_id for finding in result.findings} == {"DF-000"}, result


def test_no_rust_is_inapplicable(tmp_path: pathlib.Path) -> None:
    """A non-Rust checkout does not acquire Rust build requirements."""
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "compliant", result


def test_config_symlink_cannot_read_the_host(tmp_path: pathlib.Path) -> None:
    """The audit cannot mistake an ambient configuration for repository policy."""
    checkout = tmp_path / "checkout"
    write_checkout(checkout)
    config = checkout / ".cargo/config.toml"
    config.unlink()
    outside = tmp_path / "host.toml"
    outside.write_text(FLAGS, encoding="utf-8")
    config.symlink_to(outside)
    result = runner.run_rule(RULE_ID, checkout)
    assert result.verdict == "indeterminate", result


def test_a_workspace_member_cannot_override_its_workspace(
    tmp_path: pathlib.Path,
) -> None:
    """Cargo ignores member profiles, so declaring one cannot hide root defaults."""
    write_checkout(tmp_path / "member")
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["member"]\n', encoding="utf-8"
    )
    (tmp_path / ".concordat").write_text(
        "language:\n  rust:\n    surfaces:\n      - path: member/Cargo.toml\n",
        encoding="utf-8",
    )
    result = runner.run_rule(RULE_ID, tmp_path)
    assert result.verdict == "indeterminate", result


@pytest.mark.parametrize("name", ["nightly", "stable"])
def test_recorded_policy_inputs_match_the_real_sensor(name: str) -> None:
    """The Rego fixtures are exactly what their source checkout produces."""
    fixtures = (
        pathlib.Path(__file__).resolve().parents[2]
        / "platform-standards/canon/lint-rules/rust-dev-build-footprint/fixtures"
    )
    expected = json.loads((fixtures / "data.json").read_text(encoding="utf-8"))
    assert (
        build_dev_build_envelope(fixtures / "repos" / name)
        == expected["fixtures"][name]
    ), name
