"""Specify the toolchain literals RT-028 reads from decoded workflows.

Each test writes the shape a repository actually uses — a workflow step that
names a nightly, a matrix leg, a toolchain action's ref or input — and reads
the facts back through the real reader.
"""

from __future__ import annotations

import typing as typ

from tests.unit.toolchain_literal_support import (
    MANIFEST,
    literals,
    read,
    rust_repository,
    workflow,
    write,
)

if typ.TYPE_CHECKING:
    import pathlib

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


def test_wireframes_old_shape_is_reported(tmp_path: pathlib.Path) -> None:
    """A `rustup component add --toolchain nightly-DATE` names the nightly."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        workflow(
            f"""\
              - name: Cranelift
                run: |
                  {_ADD_CRANELIFT}
                  sudo apt-get update
            """
        ),
    )

    facts = read(tmp_path)

    assert literals(facts) == [
        ("workflow", ".github/workflows/ci.yml", "nightly-2026-10-30")
    ], facts
    assert facts["errors"] == [], facts


def test_morningtons_old_shape_is_reported(tmp_path: pathlib.Path) -> None:
    """A `setup-rust` input and a `cross +nightly-DATE` both name the nightly."""
    rust_repository(tmp_path)
    channel = "nightly-2026-08-27"
    write(
        tmp_path,
        ".github/workflows/release.yml",
        workflow(
            f"""\
              - uses: {_SETUP_RUST}
                with:
                  toolchain: {channel}
              - run: cross +{channel} build --release
            """
        ),
    )

    facts = read(tmp_path)

    assert [(kind, text) for kind, _, text in literals(facts)] == [
        ("workflow", channel),
        ("workflow", channel),
    ], facts


def test_the_fixed_shapes_pass(tmp_path: pathlib.Path) -> None:
    """Reading the channel into a step output and referencing it is silent."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        workflow(
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

    assert read(tmp_path)["literals"] == [], literals(read(tmp_path))


def test_a_reformatted_workflow_keeps_its_verdict(tmp_path: pathlib.Path) -> None:
    """The verdict comes from the decoded document, not the layout."""
    rust_repository(tmp_path)
    compact = workflow(
        "      - {uses: 'dtolnay/rust-toolchain@master', with: "
        "{toolchain: 'nightly-2026-10-30'}}\n"
    )
    expanded = workflow(
        """\
          - uses: dtolnay/rust-toolchain@master
            with:
              toolchain: nightly-2026-10-30
        """
    )

    write(tmp_path, ".github/workflows/compact.yml", compact)
    write(tmp_path, ".github/workflows/expanded.yml", expanded)

    assert literals(read(tmp_path)) == [
        ("workflow", ".github/workflows/compact.yml", "nightly-2026-10-30"),
        ("workflow", ".github/workflows/expanded.yml", "nightly-2026-10-30"),
    ]


def test_a_workflow_env_reference_declares_a_separate_pin(
    tmp_path: pathlib.Path,
) -> None:
    """A command that reaches the date through `env` binds it to a name."""
    rust_repository(tmp_path)
    write(
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
                  toolchain: ${{ env.DEV_FAST_TOOLCHAIN }}
              - run: cargo +${{ env.DEV_FAST_TOOLCHAIN }} test
              - run: cargo +nightly-2026-09-01 test
        """,
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-09-01"]


def test_an_inline_literal_beside_an_env_declaration_is_reported(
    tmp_path: pathlib.Path,
) -> None:
    """An `env` entry excuses the reference to it, not a second copy of the date."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        """\
        env:
          DEV_FAST_TOOLCHAIN: nightly-2026-08-23
        jobs:
          build:
            runs-on: ubuntu-latest
            steps:
              - run: cargo +nightly-2026-08-23 test
        """,
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-08-23"]


def test_a_comment_or_display_name_is_not_a_literal(tmp_path: pathlib.Path) -> None:
    """A toolchain named in a comment or a step's name is not CI using it."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        workflow(
            """\
              # Previously nightly-2026-10-30
              - name: Test on nightly-2026-10-30
                run: cargo test
            """
        ),
    )

    assert read(tmp_path)["literals"] == []


def test_an_unpinned_repository_is_outside_the_rule(tmp_path: pathlib.Path) -> None:
    """Without rust-toolchain.toml there is no pin for a literal to bypass."""
    write(tmp_path, "Cargo.toml", MANIFEST)
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        workflow("      - run: cargo +nightly-2026-10-30 test\n"),
    )

    facts = read(tmp_path)

    assert facts["reads_pin"] is False, facts
    assert not facts["literals"], facts


def test_an_unreadable_workflow_is_carried_as_an_error(tmp_path: pathlib.Path) -> None:
    """A workflow that does not decode leaves the rule indeterminate."""
    rust_repository(tmp_path)
    write(tmp_path, ".github/workflows/ci.yml", "jobs: [unclosed\n")

    facts = read(tmp_path)

    assert not facts["literals"], facts
    assert len(facts["errors"]) == 1, facts
    assert facts["errors"][0]["path"] == ".github/workflows/ci.yml", facts
    assert facts["errors"][0]["reason"].startswith("invalid YAML"), facts


def test_a_host_suffixed_nightly_is_reported(tmp_path: pathlib.Path) -> None:
    """A target-triple suffix does not hide the date rustup selects."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        workflow(
            "      - run: cargo +nightly-2026-10-30-x86_64-unknown-linux-gnu test\n"
        ),
    )

    assert [text for _, _, text in literals(read(tmp_path))] == [
        "nightly-2026-10-30-x86_64-unknown-linux-gnu"
    ]


def test_a_matrix_toolchain_is_resolved(tmp_path: pathlib.Path) -> None:
    """A toolchain the matrix supplies is read from every leg."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        """\
        jobs:
          build:
            runs-on: ubuntu-latest
            strategy:
              matrix:
                toolchain:
                  - nightly-2026-10-30
                  - nightly-2026-11-05
                include:
                  - toolchain: nightly-2026-12-01
            steps:
              - uses: dtolnay/rust-toolchain@master
                with:
                  toolchain: ${{ matrix.toolchain }}
        """,
    )

    assert [text for _, _, text in literals(read(tmp_path))] == [
        "nightly-2026-10-30",
        "nightly-2026-11-05",
        "nightly-2026-12-01",
    ]


def test_a_matrix_toolchain_in_a_command_is_resolved(tmp_path: pathlib.Path) -> None:
    """A `${{ matrix.x }}` argument is resolved before the date is read."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        """\
        jobs:
          build:
            runs-on: ubuntu-latest
            strategy:
              matrix:
                toolchain: [nightly-2026-10-30]
            steps:
              - run: cargo +${{ matrix.toolchain }} test
        """,
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_matrix_include_beside_an_axis_contributes(tmp_path: pathlib.Path) -> None:
    """An `include` leg written before an axis still adds its value."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        ".github/workflows/ci.yml",
        """\
        jobs:
          build:
            runs-on: ubuntu-latest
            strategy:
              matrix:
                include:
                  - toolchain: nightly-2026-10-30
                toolchain: [stable]
            steps:
              - run: cargo +${{ matrix.toolchain }} check
        """,
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]
