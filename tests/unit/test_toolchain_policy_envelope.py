"""Specify the toolchain facts evaluated by the RT-022 to RT-027 policy."""

from __future__ import annotations

import datetime as dt
import typing as typ

import pytest

from concordat.rules import packages
from concordat.rules.toolchain_policy_envelope import (
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    build_toolchain_policy_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

TODAY = dt.date(2026, 10, 11)

_MANIFEST = """\
[package]
name = "demo"
version = "0.1.0"
rust-version = "1.85"
"""

_PIN = """\
[toolchain]
channel = "nightly-2026-09-20"
"""


def _write(checkout: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *checkout*."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build(checkout: pathlib.Path) -> dict[str, typ.Any]:
    """Return the envelope for *checkout*, dated at TODAY."""
    return typ.cast(
        "dict[str, typ.Any]", build_toolchain_policy_envelope(checkout, today=TODAY)
    )


def test_a_checkout_without_rust_is_not_applicable(tmp_path: pathlib.Path) -> None:
    """No Cargo manifest puts the repository outside the rule."""
    envelope = _build(tmp_path)

    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION, envelope
    assert envelope["kind"] == ENVELOPE_KIND, envelope
    assert envelope["applicable"] is False, envelope
    assert envelope["toolchain"] is None, envelope
    assert envelope["evaluated_on"] == "2026-10-11", envelope


def test_the_builder_dates_the_envelope_with_the_date_it_is_given(
    tmp_path: pathlib.Path,
) -> None:
    """The builder reads no clock: `evaluated_on` is the caller's date."""
    envelope = build_toolchain_policy_envelope(tmp_path, today=dt.date(2030, 1, 2))

    assert envelope["evaluated_on"] == "2030-01-02", envelope


def test_the_pin_and_the_manifest_are_read(tmp_path: pathlib.Path) -> None:
    """A pinned nightly and a crate with an MSRV are carried as facts."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(tmp_path, "src/lib.rs", "")
    _write(tmp_path, "rust-toolchain.toml", _PIN)

    envelope = _build(tmp_path)

    assert envelope["applicable"] is True, envelope
    assert envelope["toolchain"]["channel"] == "nightly-2026-09-20", envelope
    assert envelope["toolchain"]["channel_kind"] == "nightly-dated", envelope
    assert envelope["pin_minor"] is None, envelope
    assert envelope["crates"] == [
        {
            "path": "Cargo.toml",
            "name": "demo",
            "is_publishable": True,
            "has_lib": True,
            "rust_version": "1.85",
            "inherits_workspace": False,
        }
    ], envelope


def test_a_numeric_pin_carries_its_minor(tmp_path: pathlib.Path) -> None:
    """The MSRV comparison needs the pin's major.minor."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(tmp_path, "rust-toolchain.toml", '[toolchain]\nchannel = "1.93.1"\n')

    envelope = _build(tmp_path)
    assert envelope["pin_minor"] == "1.93", envelope


_PUBLISH_CASES: typ.Final = {
    "": True,
    "publish = false\n": False,
    "publish = []\n": False,
    'publish = ["internal"]\n': True,
}


@pytest.mark.parametrize("publish", list(_PUBLISH_CASES), ids=repr)
def test_publishability_follows_cargo(tmp_path: pathlib.Path, publish: str) -> None:
    """Cargo publishes by default; only `false` or an empty list opts out."""
    _write(
        tmp_path,
        "Cargo.toml",
        f'[package]\nname = "demo"\nversion = "0.1.0"\n{publish}',
    )

    crate = _build(tmp_path)["crates"][0]

    assert crate["is_publishable"] is _PUBLISH_CASES[publish], crate


def test_an_application_has_no_library(tmp_path: pathlib.Path) -> None:
    """A binary-only package is not a library crate."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(tmp_path, "src/main.rs", "fn main() {}\n")

    envelope = _build(tmp_path)
    assert envelope["crates"][0]["has_lib"] is False, envelope


def test_an_explicit_lib_table_is_a_library(tmp_path: pathlib.Path) -> None:
    """A `[lib]` table with a custom path still builds a library."""
    _write(tmp_path, "Cargo.toml", f'{_MANIFEST}[lib]\npath = "core.rs"\n')

    envelope = _build(tmp_path)
    assert envelope["crates"][0]["has_lib"] is True, envelope


def test_workspace_inheritance_is_recorded(tmp_path: pathlib.Path) -> None:
    """`rust-version.workspace = true` is a declaration only with a workspace value."""
    _write(
        tmp_path,
        "Cargo.toml",
        '[workspace.package]\nrust-version = "1.80"\n\n'
        '[package]\nname = "demo"\nversion = "0.1.0"\nrust-version.workspace = true\n',
    )

    envelope = _build(tmp_path)

    assert envelope["workspace_rust_version"] == "1.80", envelope
    assert envelope["crates"][0]["rust_version"] is None, envelope
    assert envelope["crates"][0]["inherits_workspace"] is True, envelope


def test_an_unreadable_pin_stays_a_fact(tmp_path: pathlib.Path) -> None:
    """A pin that cannot be decoded is carried with its reason."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(tmp_path, "rust-toolchain.toml", "[toolchain\n")

    toolchain = _build(tmp_path)["toolchain"]

    assert toolchain["parse_error"] is not None, toolchain
    assert toolchain["channel"] is None, toolchain


def test_dependabot_rust_toolchain_entries_are_extracted(
    tmp_path: pathlib.Path,
) -> None:
    """Only `rust-toolchain` entries are kept, with directories normalised."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        ".github/dependabot.yml",
        "version: 2\nupdates:\n"
        "  - package-ecosystem: cargo\n    directory: /\n"
        "    schedule: {interval: daily}\n"
        "  - package-ecosystem: rust-toolchain\n    directory: rust/\n"
        "    schedule: {interval: weekly}\n",
    )

    dependabot = _build(tmp_path)["dependabot"]

    assert dependabot == {
        "present": True,
        "error": None,
        "entries": [{"directories": ["/rust"], "interval": "weekly"}],
    }, dependabot


def test_a_missing_dependabot_file_is_absent(tmp_path: pathlib.Path) -> None:
    """No configuration is recorded as absent, not as an empty list of entries."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)

    envelope = _build(tmp_path)
    assert envelope["dependabot"]["present"] is False, envelope


def test_an_undecodable_dependabot_file_carries_its_reason(
    tmp_path: pathlib.Path,
) -> None:
    """The policy reports an unreadable configuration as indeterminate."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(tmp_path, ".github/dependabot.yml", "updates: [\n")

    dependabot = _build(tmp_path)["dependabot"]

    assert dependabot["present"] is True, dependabot
    assert dependabot["error"] is not None, dependabot


_WORKFLOW = """\
name: ci
env:
  PINNED: nightly-2026-09-20
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: dtolnay/rust-toolchain@master
        with:
          toolchain: ${{ env.PINNED }}
      - run: rustup toolchain install 1.85.0 --profile minimal
      - run: cargo +1.93 check
      - uses: dtolnay/rust-toolchain@nightly-2026-03-26
  msrv:
    runs-on: ubuntu-latest
    steps:
      - run: cargo check --all-targets
"""


def test_the_workflows_toolchains_are_read_as_data(tmp_path: pathlib.Path) -> None:
    """Toolchains come from steps and env references, and an MSRV job is spotted."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(tmp_path, ".github/workflows/ci.yml", _WORKFLOW)

    ci = _build(tmp_path)["ci"]

    assert ci["errors"] == [], ci
    assert ci["dated_nightlies"] == ["nightly-2026-03-26", "nightly-2026-09-20"], ci
    assert ci["numeric_minors"] == ["1.85", "1.93"], ci
    assert ci["msrv_job"] is True, ci
    assert ci["reads_manifest_msrv"] is False, ci


def test_a_toolchain_in_a_comment_is_not_ci_using_it(tmp_path: pathlib.Path) -> None:
    """Workflows are walked as data, so a comment names nothing."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "# toolchain: nightly-2020-01-01\njobs:\n  b:\n    steps:\n      - run: make\n",
    )

    ci = _build(tmp_path)["ci"]

    assert ci["dated_nightlies"] == [], ci
    assert ci["numeric_minors"] == [], ci


def test_a_step_reading_the_manifest_msrv_exercises_it(
    tmp_path: pathlib.Path,
) -> None:
    """A step that installs a toolchain read from rust-version covers every MSRV."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  b:\n    steps:\n"
        "      - run: |\n"
        "          v=$(cargo metadata --no-deps | jq -r '.packages[0].rust_version')\n"
        '          rustup toolchain install "$v"\n',
    )

    ci = _build(tmp_path)["ci"]

    assert ci["reads_manifest_msrv"] is True, ci


def test_reading_the_msrv_without_selecting_a_toolchain_is_not_credited(
    tmp_path: pathlib.Path,
) -> None:
    """Printing `rust-version` exercises nothing; the step must select a toolchain."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  b:\n    steps:\n      - run: grep rust-version Cargo.toml\n",
    )

    envelope = _build(tmp_path)
    assert envelope["ci"]["reads_manifest_msrv"] is False, envelope


def test_an_undecodable_workflow_carries_its_reason(tmp_path: pathlib.Path) -> None:
    """A workflow that cannot be decoded is evidence of nothing."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(tmp_path, ".github/workflows/ci.yml", "jobs: [\n")

    errors = _build(tmp_path)["ci"]["errors"]

    assert len(errors) == 1, errors
    assert errors[0].startswith(".github/workflows/ci.yml: "), errors


def test_a_recorded_exception_is_found(tmp_path: pathlib.Path) -> None:
    """The exception section names the channel it was written against."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(tmp_path, "rust-toolchain.toml", _PIN)
    _write(
        tmp_path,
        "docs/developers-guide.md",
        "# Guide\n\n## Toolchain policy exception\n\n"
        "The pinned toolchain nightly-2026-09-20 is coupled to ra_ap_syntax.\n",
    )

    sections = _build(tmp_path)["exceptions"][0]["sections"]

    assert sections[0]["channels_named"] == ["nightly-2026-09-20"], sections


@pytest.mark.parametrize(
    ("command", "minors"),
    [
        pytest.param("rustup install 1.85", ["1.85"], id="install-alias"),
        pytest.param(
            "rustup toolchain install --profile minimal 1.85.0",
            ["1.85"],
            id="option-before-the-toolchain",
        ),
        pytest.param(
            "rustup toolchain install 1.85 --profile minimal --component clippy",
            ["1.85"],
            id="option-values-are-not-toolchains",
        ),
        pytest.param(
            "rustup toolchain install stable 1.85", ["1.85"], id="every-install-name"
        ),
        pytest.param("rustup run 1.85 cargo check", ["1.85"], id="run"),
        pytest.param("rustup default 1.85 && rustup show", ["1.85"], id="default"),
        pytest.param("cargo +1.85 check --lib", ["1.85"], id="cargo-override"),
        pytest.param("rustup override set 1.85 --path x", ["1.85"], id="override-set"),
    ],
)
def test_the_rustup_command_forms_are_read(
    tmp_path: pathlib.Path, command: str, minors: list[str]
) -> None:
    """Each documented way to select a toolchain on a command line is credited."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        f"jobs:\n  b:\n    steps:\n      - run: {command}\n",
    )

    ci = _build(tmp_path)["ci"]

    assert ci["numeric_minors"] == minors, ci


_MATRIX_WORKFLOW = """\
jobs:
  test:
    strategy:
      matrix:
        rust: [stable, "1.85"]
        include:
          - rust: nightly-2026-03-26
    steps:
      - uses: dtolnay/rust-toolchain@master
        with:
          toolchain: ${{ matrix.rust }}
      - run: cargo +${{ matrix.rust }} check
"""


def test_a_matrix_reference_is_resolved_to_every_leg(tmp_path: pathlib.Path) -> None:
    """The MSRV is usually a matrix leg, so `matrix.rust` expands to its values."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(tmp_path, ".github/workflows/ci.yml", _MATRIX_WORKFLOW)

    ci = _build(tmp_path)["ci"]

    assert ci["numeric_minors"] == ["1.85"], ci
    assert ci["dated_nightlies"] == ["nightly-2026-03-26"], ci


def test_an_unknown_matrix_key_names_nothing(tmp_path: pathlib.Path) -> None:
    """A reference to a key the matrix does not define is not a toolchain."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  t:\n    steps:\n      - run: cargo +${{ matrix.other }} check\n",
    )

    ci = _build(tmp_path)["ci"]

    assert ci["numeric_minors"] == [], ci
    assert ci["dated_nightlies"] == [], ci


@pytest.mark.parametrize(
    ("member_publish", "workspace_publish", "expected"),
    [
        pytest.param(
            "publish.workspace = true\n", "publish = false\n", False, id="false"
        ),
        pytest.param("publish.workspace = true\n", "", True, id="unset-is-publishable"),
        pytest.param(
            "publish.workspace = true\n", "publish = []\n", False, id="empty-list"
        ),
    ],
)
def test_publish_is_inherited_from_the_workspace(
    tmp_path: pathlib.Path,
    member_publish: str,
    workspace_publish: str,
    *,
    expected: bool,
) -> None:
    """`publish.workspace = true` takes the workspace's value, as rust-version does."""
    _write(
        tmp_path,
        "Cargo.toml",
        f"[workspace.package]\n{workspace_publish}\n"
        f'[package]\nname = "demo"\nversion = "0.1.0"\n{member_publish}',
    )

    crate = _build(tmp_path)["crates"][0]

    assert crate["is_publishable"] is expected, crate


_CUSTOM_PARAMETERS: typ.Final = {
    "exception_documents": ["docs/toolchain.md"],
    "exception_keyword": "Pinned on purpose",
}
_EXCEPTION_TEXT: typ.Final = "# Doc\n\n## {heading}\n\nNames nightly-2026-09-20.\n"


def test_the_exception_location_and_keyword_are_parameters(
    tmp_path: pathlib.Path,
) -> None:
    """A custom document and heading keyword find the exception; defaults do not."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        "docs/toolchain.md",
        _EXCEPTION_TEXT.format(heading="Pinned on purpose"),
    )
    _write(
        tmp_path,
        "docs/developers-guide.md",
        _EXCEPTION_TEXT.format(heading="Toolchain policy exception"),
    )

    custom = build_toolchain_policy_envelope(tmp_path, _CUSTOM_PARAMETERS, today=TODAY)[
        "exceptions"
    ]
    default = build_toolchain_policy_envelope(tmp_path, today=TODAY)["exceptions"]

    assert [d["path"] for d in custom] == ["docs/toolchain.md"], custom
    assert custom[0]["sections"][0]["heading"] == "Pinned on purpose", custom
    assert [d["path"] for d in default] == ["docs/developers-guide.md"], default
    assert default[0]["sections"][0]["heading"] == "Toolchain policy exception", default


def test_the_default_exception_keyword_ignores_other_headings(
    tmp_path: pathlib.Path,
) -> None:
    """A section whose heading lacks the keyword is not an exception record."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        "docs/developers-guide.md",
        _EXCEPTION_TEXT.format(heading="Pinned on purpose"),
    )

    exceptions = build_toolchain_policy_envelope(tmp_path, today=TODAY)["exceptions"]

    assert exceptions[0]["sections"] == [], exceptions


def test_the_command_boundary_supplies_the_utc_date(tmp_path: pathlib.Path) -> None:
    """The packaged builder dates the envelope with today's UTC date."""
    envelope = typ.cast(
        "dict[str, typ.Any]",
        packages.default_envelope_builder("rust-toolchain-policy", tmp_path),
    )

    assert envelope["evaluated_on"] == dt.datetime.now(dt.UTC).date().isoformat(), (
        envelope
    )
