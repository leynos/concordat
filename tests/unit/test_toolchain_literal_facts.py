"""Specify the dated toolchain literals RT-028 reads from a checkout.

Each test writes the shape a repository actually uses — a workflow step that
names a nightly, a Makefile variable, the two fix shapes wireframe#864 and
mornington#37 adopted — and reads the facts back through the real reader.
"""

from __future__ import annotations

import textwrap
import typing as typ

import pytest

from concordat.rules.makefile_facts import MakefileFacts
from concordat.rules.toolchain import inspect_toolchain
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


def _read(checkout: pathlib.Path) -> ToolchainLiteralFacts:
    """Read *checkout*'s literals against the pin facts it exposes."""
    return toolchain_literal_facts(checkout, inspect_toolchain(checkout))


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

    facts = _read(tmp_path)

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

    facts = _read(tmp_path)

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

    assert _read(tmp_path)["literals"] == [], _literals(_read(tmp_path))


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

    assert _literals(_read(tmp_path)) == [
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

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-09-01"]


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

    assert _read(tmp_path)["literals"] == []


def test_an_unpinned_repository_is_outside_the_rule(tmp_path: pathlib.Path) -> None:
    """Without rust-toolchain.toml there is no pin for a literal to bypass."""
    _write(tmp_path, "Cargo.toml", _MANIFEST)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        _workflow("      - run: cargo +nightly-2026-10-30 test\n"),
    )

    facts = _read(tmp_path)

    assert facts["reads_pin"] is False, facts
    assert facts["literals"] == [], facts


def test_an_unreadable_workflow_is_carried_as_an_error(tmp_path: pathlib.Path) -> None:
    """A workflow that does not decode leaves the rule indeterminate."""
    _rust_repository(tmp_path)
    _write(tmp_path, ".github/workflows/ci.yml", "jobs: [unclosed\n")

    facts = _read(tmp_path)

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

    assert _read(tmp_path)["literals"] == []


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

    assert _literals(_read(tmp_path)) == [
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

    assert _literals(_read(tmp_path)) == [
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

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_makefile_without_literals_is_silent(tmp_path: pathlib.Path) -> None:
    """An ordinary Makefile draws no finding."""
    _rust_repository(tmp_path)
    _write(tmp_path, "Makefile", "test:\n\tcargo test --all-targets\n")

    assert _read(tmp_path)["literals"] == []


@pytest.mark.parametrize("makefile", ["Makefile", "makefile", "GNUmakefile"])
def test_the_makefile_spelling_does_not_matter(
    tmp_path: pathlib.Path, makefile: str
) -> None:
    """Make reads any of the three spellings, so the reader finds the one present."""
    _rust_repository(tmp_path)
    _write(tmp_path, makefile, "test:\n\tcargo +nightly-2026-10-30 test\n")

    assert _literals(_read(tmp_path)) == [("makefile", makefile, "nightly-2026-10-30")]


def test_a_host_suffixed_nightly_is_reported(tmp_path: pathlib.Path) -> None:
    """A target-triple suffix does not hide the date rustup selects."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        _workflow(
            "      - run: cargo +nightly-2026-10-30-x86_64-unknown-linux-gnu test\n"
        ),
    )

    assert [text for _, _, text in _literals(_read(tmp_path))] == [
        "nightly-2026-10-30-x86_64-unknown-linux-gnu"
    ]


def test_a_quoted_command_is_not_a_selection(tmp_path: pathlib.Path) -> None:
    """Text an `echo` prints is an argument, not a command."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        _workflow(
            '      - run: echo "cargo +nightly-2026-10-30 test"\n'
            "      - run: true; cargo +nightly-2026-11-05 check\n"
        ),
    )

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-11-05"]


def test_a_matrix_toolchain_is_resolved(tmp_path: pathlib.Path) -> None:
    """A toolchain the matrix supplies is read from every leg."""
    _rust_repository(tmp_path)
    _write(
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

    assert [text for _, _, text in _literals(_read(tmp_path))] == [
        "nightly-2026-10-30",
        "nightly-2026-11-05",
        "nightly-2026-12-01",
    ]


def test_a_matrix_toolchain_in_a_command_is_resolved(tmp_path: pathlib.Path) -> None:
    """A `${{ matrix.x }}` argument is resolved before the date is read."""
    _rust_repository(tmp_path)
    _write(
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

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_command_variable_is_expanded(tmp_path: pathlib.Path) -> None:
    """A recipe that runs `$(CHECK)` selects whatever that variable commands."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        "Makefile",
        "CHECK = cargo +nightly-2026-10-30 check\n\ntest:\n\t$(CHECK)\n",
    )

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_silent_recipe_prefix_is_not_the_program(tmp_path: pathlib.Path) -> None:
    """Make's `@` prefix does not stop `cargo` being the program."""
    _rust_repository(tmp_path)
    _write(tmp_path, "Makefile", "test:\n\t@cargo +nightly-2026-10-30 test\n")

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_quoted_make_reference_is_a_reference(tmp_path: pathlib.Path) -> None:
    """Quoting the reference must not hide that it is one."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        "Makefile",
        "TOOL = $(PIN)\nPIN = nightly-2026-10-30\n\n"
        'test:\n\tRUSTUP_TOOLCHAIN="$(TOOL)" cargo test\n',
    )

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_recovered_parse_is_carried_as_an_error(tmp_path: pathlib.Path) -> None:
    """A recovered parse may have lost the recipe that names the date."""
    _rust_repository(tmp_path)
    _write(tmp_path, "Makefile", "test:\n\tcargo test\n")
    report: typ.Any = {
        "schema_version": 1,
        "parse": {"status": "recovered"},
        "rules": [],
        "variables": [],
        "includes": [],
    }

    facts = toolchain_literal_facts(
        tmp_path,
        inspect_toolchain(tmp_path),
        inspect=lambda _path: MakefileFacts(report=report, status="recovered"),
    )

    assert facts["literals"] == [], facts
    assert [error["path"] for error in facts["errors"]] == ["Makefile"], facts
    assert "recovered" in facts["errors"][0]["reason"], facts


def test_a_makefile_outside_the_checkout_is_an_error(tmp_path: pathlib.Path) -> None:
    """A symlink must not feed another repository's recipes into the audit."""
    checkout = tmp_path / "checkout"
    external = tmp_path / "external"
    _rust_repository(checkout)
    external.mkdir()
    (external / "Makefile").write_text("test:\n\tcargo test\n", encoding="utf-8")
    (checkout / "Makefile").symlink_to(external / "Makefile")

    facts = _read(checkout)

    assert facts["literals"] == [], facts
    assert [error["path"] for error in facts["errors"]] == ["Makefile"], facts
    assert "outside the checkout" in facts["errors"][0]["reason"], facts


def test_a_here_document_body_is_not_a_command(tmp_path: pathlib.Path) -> None:
    """Text a quoted here-document feeds to a command is data, not a selection."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        _workflow(
            """\
              - run: |
                  cat <<'EOF' > notes.txt
                  cargo +nightly-2026-10-30 check
                  EOF
                  cargo +nightly-2026-09-01 test
            """
        ),
    )

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-09-01"]


def test_a_matrix_include_beside_an_axis_contributes(tmp_path: pathlib.Path) -> None:
    """An `include` leg written before an axis still adds its value."""
    _rust_repository(tmp_path)
    _write(
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

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_recipe_prefix_does_not_hide_an_assignment(tmp_path: pathlib.Path) -> None:
    """Make strips `@` before the shell, so the word is still an assignment."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        "Makefile",
        "test:\n\t@RUSTUP_TOOLCHAIN=nightly-2026-10-30 cargo test\n",
    )

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_command_variable_is_read_as_a_shell_command(tmp_path: pathlib.Path) -> None:
    """A quoted override in a command variable is shell syntax, not text."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        "Makefile",
        'CHECK = cargo "+nightly-2026-10-30" check\n\ntest:\n\t$(CHECK)\n',
    )

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_command_variable_may_hold_several_commands(
    tmp_path: pathlib.Path,
) -> None:
    """Every command a command variable holds is read, not only the first."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        "Makefile",
        "CHECK = cargo +nightly-2026-10-30 check && "
        "cargo +nightly-2026-09-01 test\n"
        "\n"
        "test:\n"
        "\t$(CHECK)\n",
    )

    assert [text for _, _, text in _literals(_read(tmp_path))] == [
        "nightly-2026-10-30",
        "nightly-2026-09-01",
    ]


def test_an_exported_toolchain_is_a_selection(tmp_path: pathlib.Path) -> None:
    """`export RUSTUP_TOOLCHAIN=...` selects, and is not a workflow `env`."""
    _rust_repository(tmp_path)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        _workflow(
            """\
              - run: |
                  export RUSTUP_TOOLCHAIN=nightly-2026-10-30
                  cargo check
            """
        ),
    )

    assert [text for _, _, text in _literals(_read(tmp_path))] == ["nightly-2026-10-30"]
