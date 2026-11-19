"""Specify the dated toolchain literals RT-028 reads from a checkout.

Each test writes the shape a repository actually uses — a workflow step that
names a nightly, a Makefile variable, the two fix shapes wireframe#864 and
mornington#37 adopted — and reads the facts back through the real reader.
"""

from __future__ import annotations

import textwrap
import typing as typ

import pytest

from concordat.rules.toolchain_literal_facts import (
    ToolchainLiteralFacts,
    toolchain_literal_facts,
)

if typ.TYPE_CHECKING:
    import pathlib

_MANIFEST = '[package]\nname = "demo"\nversion = "0.1.0"\n'
_PIN = '[toolchain]\nchannel = "nightly-2026-11-05"\n'
_SETUP_RUST = (
    "leynos/shared-actions/.github/actions/setup-rust@"
    "6cec89bac47a21cf756d68d638a9a510998e57f8"
)
_ADD_CRANELIFT = (
    "rustup component add rustc-codegen-cranelift-preview "
    "--toolchain nightly-2026-10-30"
)
_ADD_CRANELIFT_TO_VARIABLE = (
    'rustup component add rustc-codegen-cranelift-preview --toolchain "$toolchain"'
)
_READ_CHANNEL = (
    'toolchain="$(sed -n \'s/^channel *= *"\\(.*\\)"$/\\1/p\' rust-toolchain.toml)"'
)


def _write(checkout: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* at *relative* under *checkout*."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _rust_repository(checkout: pathlib.Path) -> pathlib.Path:
    """Make *checkout* a Rust repository pinned to a dated nightly."""
    _write(checkout, "Cargo.toml", _MANIFEST)
    _write(checkout, "rust-toolchain.toml", _PIN)
    return checkout


def _literals(facts: ToolchainLiteralFacts) -> list[tuple[str, str, str]]:
    """Return each literal as `(kind, path, text)`."""
    return [(f["kind"], f["path"], f["text"]) for f in facts["literals"]]


def _workflow(steps: str) -> str:
    """Return a workflow whose single job runs *steps*."""
    return f"jobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n{steps}"


def test_wireframes_old_shape_is_reported(tmp_path: pathlib.Path) -> None:
    """A `rustup component add --toolchain nightly-DATE` names the nightly."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        _workflow(
            f"""\
              - name: Cranelift
                run: |
                  {_ADD_CRANELIFT}
                  sudo apt-get update
            """
        ),
    )

    facts = toolchain_literal_facts(tmp_path)

    assert _literals(facts) == [
        ("workflow", ".github/workflows/ci.yml", "nightly-2026-10-30")
    ], facts
    assert facts["errors"] == [], facts


def test_morningtons_old_shape_is_reported(tmp_path: pathlib.Path) -> None:
    """A `setup-rust` input and a `cross +nightly-DATE` both name the nightly."""
    _rust_repository(tmp_path)
    channel = "nightly-2026-08-27"
    _write(
        tmp_path,
        ".github/workflows/release.yml",
        _workflow(
            f"""\
              - uses: {_SETUP_RUST}
                with:
                  toolchain: {channel}
              - run: cross +{channel} build --release
            """
        ),
    )

    facts = toolchain_literal_facts(tmp_path)

    assert [(kind, text) for kind, _, text in _literals(facts)] == [
        ("workflow", channel),
        ("workflow", channel),
    ], facts


def test_the_fixed_shapes_pass(tmp_path: pathlib.Path) -> None:
    """Reading the channel into a step output and referencing it is silent."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        _workflow(
            f"""\
              - id: toolchain
                run: |
                  {_READ_CHANNEL}
                  {_ADD_CRANELIFT_TO_VARIABLE}
                  printf 'channel=%s\\n' "$toolchain" >> "$GITHUB_OUTPUT"
              - uses: {_SETUP_RUST}
                with:
                  toolchain: ${{{{ steps.toolchain.outputs.channel }}}}
              - run: cross +${{{{ steps.toolchain.outputs.channel }}}} build --release
            """
        ),
    )

    assert toolchain_literal_facts(tmp_path)["literals"] == [], _literals(
        toolchain_literal_facts(tmp_path)
    )


def test_a_reformatted_workflow_keeps_its_verdict(tmp_path: pathlib.Path) -> None:
    """The verdict comes from the decoded document, not the layout."""
    _rust_repository(tmp_path)
    compact = _workflow(
        "      - {uses: 'dtolnay/rust-toolchain@master', with: "
        "{toolchain: 'nightly-2026-10-30'}}\n"
    )
    expanded = _workflow(
        """\
          - uses: dtolnay/rust-toolchain@master
            with:
              toolchain: nightly-2026-10-30
        """
    )

    _write(tmp_path, ".github/workflows/compact.yml", compact)
    _write(tmp_path, ".github/workflows/expanded.yml", expanded)

    assert _literals(toolchain_literal_facts(tmp_path)) == [
        ("workflow", ".github/workflows/compact.yml", "nightly-2026-10-30"),
        ("workflow", ".github/workflows/expanded.yml", "nightly-2026-10-30"),
    ]


def test_a_workflow_env_declaration_is_deliberate(tmp_path: pathlib.Path) -> None:
    """A literal the workflow's own `env` declares is not reported."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        """\
        env:
          DEV_FAST_TOOLCHAIN: nightly-2026-08-23
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - uses: dtolnay/rust-toolchain@master
                with:
                  toolchain: nightly-2026-08-23
              - run: cargo +nightly-2026-09-01 test
        """,
    )

    assert [text for _, _, text in _literals(toolchain_literal_facts(tmp_path))] == [
        "nightly-2026-09-01"
    ]


def test_a_comment_or_display_name_is_not_a_literal(tmp_path: pathlib.Path) -> None:
    """A toolchain named in a comment or a step's name is not CI using it."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        _workflow(
            """\
              # Previously nightly-2026-10-30
              - name: Test on nightly-2026-10-30
                run: cargo test
            """
        ),
    )

    assert toolchain_literal_facts(tmp_path)["literals"] == []


def test_an_unpinned_repository_is_outside_the_rule(tmp_path: pathlib.Path) -> None:
    """Without rust-toolchain.toml there is no pin for a literal to bypass."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        _workflow("      - run: cargo +nightly-2026-10-30 test\n"),
    )

    facts = toolchain_literal_facts(tmp_path)

    assert facts["reads_pin"] is False, facts
    assert facts["literals"] == [], facts


def test_an_unreadable_workflow_is_carried_as_an_error(tmp_path: pathlib.Path) -> None:
    """A workflow that does not decode leaves the rule indeterminate."""
    _rust_repository(tmp_path)
    _write(tmp_path, ".github/workflows/ci.yml", "jobs: [unclosed\n")

    facts = toolchain_literal_facts(tmp_path)

    assert facts["literals"] == [], facts
    assert len(facts["errors"]) == 1, facts
    assert facts["errors"][0]["path"] == ".github/workflows/ci.yml", facts
    assert facts["errors"][0]["reason"].startswith("invalid YAML"), facts


def test_a_makefile_variable_declares_a_separate_pin(tmp_path: pathlib.Path) -> None:
    """Cuprum's `DEV_FAST_TOOLCHAIN ?= nightly-DATE` is a deliberate pin."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        "Makefile",
        "DEV_FAST_TOOLCHAIN ?= nightly-2026-08-23\n"
        "RUSTFMT_TOOLCHAIN ?= nightly-2026-05-28\n"
        "\n"
        "test:\n"
        "\tRUSTUP_TOOLCHAIN=$(DEV_FAST_TOOLCHAIN) cargo test\n"
        "\tcargo +$(RUSTFMT_TOOLCHAIN) fmt --check\n",
    )

    assert toolchain_literal_facts(tmp_path)["literals"] == []


def test_an_inline_makefile_literal_is_reported(tmp_path: pathlib.Path) -> None:
    """A literal written into a recipe bypasses the pin inline."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        "Makefile",
        "test:\n"
        "\tcargo +nightly-2026-10-30 test\n"
        "\trustup run nightly-2026-10-30 rustc -vV\n",
    )

    assert _literals(toolchain_literal_facts(tmp_path)) == [
        ("makefile", "Makefile", "nightly-2026-10-30"),
        ("makefile", "Makefile", "nightly-2026-10-30"),
    ]


def test_an_undefined_makefile_variable_is_reported(tmp_path: pathlib.Path) -> None:
    """A recipe naming an undefined variable cannot be shown to be deliberate."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        "Makefile",
        "test:\n\tRUSTUP_TOOLCHAIN=$(DEV_FAST_TOOLCHAIN) cargo test\n",
    )

    assert _literals(toolchain_literal_facts(tmp_path)) == [
        ("makefile", "Makefile", "$(DEV_FAST_TOOLCHAIN)")
    ]


def test_a_variable_that_expands_to_a_literal_is_reported(
    tmp_path: pathlib.Path,
) -> None:
    """Indirection through a Make variable is not a declaration to keep apart."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        "Makefile",
        "NIGHTLY = nightly-2026-10-30\n"
        "TEST_TOOLCHAIN = $(NIGHTLY)\n"
        "\n"
        "test:\n"
        "\tRUSTUP_TOOLCHAIN=$(TEST_TOOLCHAIN) cargo test\n",
    )

    assert [text for _, _, text in _literals(toolchain_literal_facts(tmp_path))] == [
        "nightly-2026-10-30"
    ]


def test_a_makefile_without_literals_is_silent(tmp_path: pathlib.Path) -> None:
    """An ordinary Makefile draws no finding."""
    _rust_repository(tmp_path)
    _write(tmp_path, "Makefile", "test:\n\tcargo test --all-targets\n")

    assert toolchain_literal_facts(tmp_path)["literals"] == []


@pytest.mark.parametrize("makefile", ["Makefile", "makefile", "GNUmakefile"])
def test_the_makefile_spelling_does_not_matter(
    tmp_path: pathlib.Path, makefile: str
) -> None:
    """Make reads any of the three spellings, so the reader finds the one present."""
    _rust_repository(tmp_path)
    _write(tmp_path, makefile, "test:\n\tcargo +nightly-2026-10-30 test\n")

    assert _literals(toolchain_literal_facts(tmp_path)) == [
        ("makefile", makefile, "nightly-2026-10-30")
    ]
