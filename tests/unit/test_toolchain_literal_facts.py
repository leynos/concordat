"""Specify the dated toolchain literals RT-028 reads from a checkout.

Each test writes the shape a repository actually uses — a workflow step that
names a nightly, a Makefile variable, the two fix shapes wireframe#864 and
mornington#37 adopted — and reads the facts back through the real reader.
"""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.makefile_facts import MakefileFacts
from concordat.rules.toolchain import inspect_toolchain
from concordat.rules.toolchain_literal_facts import (
    toolchain_literal_facts,
)
from tests.unit.toolchain_literal_support import (
    literals,
    makefile_facts,
    makefile_outcome,
    read,
    rust_repository,
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


def test_a_makefile_variable_declares_a_separate_pin(tmp_path: pathlib.Path) -> None:
    """Cuprum's `DEV_FAST_TOOLCHAIN ?= nightly-DATE` is a deliberate pin."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        "DEV_FAST_TOOLCHAIN ?= nightly-2026-08-23\n"
        "RUSTFMT_TOOLCHAIN ?= nightly-2026-05-28\n"
        "\n"
        "test:\n"
        "\tRUSTUP_TOOLCHAIN=$(DEV_FAST_TOOLCHAIN) cargo test\n"
        "\tcargo +$(RUSTFMT_TOOLCHAIN) fmt --check\n",
    )

    assert not read(tmp_path)["literals"]


def test_an_inline_makefile_literal_is_reported(tmp_path: pathlib.Path) -> None:
    """A literal written into a recipe bypasses the pin inline."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        "test:\n"
        "\tcargo +nightly-2026-10-30 test\n"
        "\trustup run nightly-2026-10-30 rustc -vV\n",
    )

    assert literals(read(tmp_path)) == [
        ("makefile", "Makefile", "nightly-2026-10-30"),
        ("makefile", "Makefile", "nightly-2026-10-30"),
    ]


def test_a_toolchain_from_an_unsupplied_variable_is_indeterminate(
    tmp_path: pathlib.Path,
) -> None:
    """A recipe naming a variable the Makefile never assigns has no known date."""
    facts = makefile_facts(
        tmp_path,
        "test:\n\tRUSTUP_TOOLCHAIN=$(TOOLCHAIN) cargo test\n",
    )

    assert not facts["literals"], facts
    assert [error["path"] for error in facts["errors"]] == ["Makefile"], facts
    assert "cannot be determined" in facts["errors"][0]["reason"], facts


def test_an_include_does_not_declare_an_unsupplied_variable(
    tmp_path: pathlib.Path,
) -> None:
    """An include may supply the name, but it cannot be shown to be a pin."""
    outcome = makefile_outcome(
        tmp_path,
        "-include optional.mk\n\ntest:\n\tRUSTUP_TOOLCHAIN=$(TOOLCHAIN) cargo test\n",
    )

    assert outcome == ([], ["Makefile"]), outcome


def test_a_computed_toolchain_is_not_a_literal(tmp_path: pathlib.Path) -> None:
    """A value Make computes at build time is not a date written down."""
    outcome = makefile_outcome(
        tmp_path,
        "test:\n\tRUSTUP_TOOLCHAIN=$(shell cat tools/toolchain.txt) cargo test\n",
    )

    assert outcome == ([], []), outcome


def test_a_reference_inside_a_computed_value_is_not_missing(
    tmp_path: pathlib.Path,
) -> None:
    """`$(shell cat $(PIN_FILE))` computes its value; the argument is not a pin."""
    outcome = makefile_outcome(
        tmp_path,
        "test:\n\tRUSTUP_TOOLCHAIN=$(shell cat $(PIN_FILE)) cargo test\n",
    )

    assert outcome == ([], []), outcome


def test_a_call_with_plain_parentheses_stays_one_call(
    tmp_path: pathlib.Path,
) -> None:
    """A `)` in the call's own text does not end a `$(shell ...)` early."""
    outcome = makefile_outcome(
        tmp_path,
        "test:\n\tRUSTUP_TOOLCHAIN=$(shell echo ')')-$(PIN_FILE) cargo test\n",
    )

    assert outcome == ([], ["Makefile"]), outcome


def test_a_top_level_reference_beside_a_call_is_missing(
    tmp_path: pathlib.Path,
) -> None:
    """A reference after a completed call is still a missing variable."""
    outcome = makefile_outcome(
        tmp_path,
        "test:\n\tRUSTUP_TOOLCHAIN=$(shell echo hi)-$(PIN_FILE) cargo test\n",
    )

    assert outcome == ([], ["Makefile"]), outcome


def test_a_trailing_shell_dollar_is_not_an_open_call(
    tmp_path: pathlib.Path,
) -> None:
    """A `$$` after a computed call is a shell dollar, not a Make reference."""
    outcome = makefile_outcome(
        tmp_path,
        "test:\n\tRUSTUP_TOOLCHAIN=$(shell cat f)$$ cargo test\n",
    )

    assert outcome == ([], []), outcome


def test_a_partly_expanded_value_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """`nightly-$(DATE)` has no date the reader can establish without DATE."""
    outcome = makefile_outcome(
        tmp_path,
        "TOOLCHAIN = nightly-$(DATE)\n"
        "\n"
        "test:\n"
        "\tRUSTUP_TOOLCHAIN=$(TOOLCHAIN) cargo test\n",
    )

    assert outcome == ([], ["Makefile"]), outcome


def test_a_dotted_variable_name_is_indeterminate(tmp_path: pathlib.Path) -> None:
    """`$(PIN.DATE)` is a plain name with a dot, not a computed call."""
    outcome = makefile_outcome(
        tmp_path,
        "test:\n\tRUSTUP_TOOLCHAIN=$(PIN.DATE) cargo test\n",
    )

    assert outcome == ([], ["Makefile"]), outcome


def test_a_declared_dotted_variable_is_a_separate_pin(
    tmp_path: pathlib.Path,
) -> None:
    """A dotted name assigned the date itself is a deliberate separate pin."""
    outcome = makefile_outcome(
        tmp_path,
        "PIN.DATE = nightly-2026-10-30\n"
        "\n"
        "test:\n"
        "\tRUSTUP_TOOLCHAIN=$(PIN.DATE) cargo test\n",
    )

    assert outcome == ([], []), outcome


def test_a_reference_program_that_is_not_cargo_selects_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """`$(CARGO.PRINT) = echo` means the override is an argument to echo."""
    outcome = makefile_outcome(
        tmp_path,
        "CARGO.PRINT = echo\n"
        "\n"
        "test:\n"
        "\t$(CARGO.PRINT) +nightly-2026-10-30 cargo test\n",
    )

    assert outcome == ([], []), outcome


def test_a_reference_program_that_is_cargo_is_read(
    tmp_path: pathlib.Path,
) -> None:
    """A reference that resolves to cargo keeps its `+toolchain` override."""
    outcome = makefile_outcome(
        tmp_path,
        "CARGO = cargo\n\ntest:\n\t$(CARGO) +nightly-2026-10-30 test\n",
    )

    assert outcome == (["nightly-2026-10-30"], []), outcome


def test_a_variable_that_expands_to_a_literal_is_reported(
    tmp_path: pathlib.Path,
) -> None:
    """Indirection through a Make variable is not a declaration to keep apart."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        "NIGHTLY = nightly-2026-10-30\n"
        "TEST_TOOLCHAIN = $(NIGHTLY)\n"
        "\n"
        "test:\n"
        "\tRUSTUP_TOOLCHAIN=$(TEST_TOOLCHAIN) cargo test\n",
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_makefile_without_literals_is_silent(tmp_path: pathlib.Path) -> None:
    """An ordinary Makefile draws no finding."""
    rust_repository(tmp_path)
    write(tmp_path, "Makefile", "test:\n\tcargo test --all-targets\n")

    assert not read(tmp_path)["literals"]


@pytest.mark.parametrize("makefile", ["Makefile", "makefile", "GNUmakefile"])
def test_the_makefile_spelling_does_not_matter(
    tmp_path: pathlib.Path, makefile: str
) -> None:
    """Make reads any of the three spellings, so the reader finds the one present."""
    rust_repository(tmp_path)
    write(tmp_path, makefile, "test:\n\tcargo +nightly-2026-10-30 test\n")

    assert literals(read(tmp_path)) == [("makefile", makefile, "nightly-2026-10-30")]


def test_a_command_variable_is_expanded(tmp_path: pathlib.Path) -> None:
    """A recipe that runs `$(CHECK)` selects whatever that variable commands."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        "CHECK = cargo +nightly-2026-10-30 check\n\ntest:\n\t$(CHECK)\n",
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_silent_recipe_prefix_is_not_the_program(tmp_path: pathlib.Path) -> None:
    """Make's `@` prefix does not stop `cargo` being the program."""
    rust_repository(tmp_path)
    write(tmp_path, "Makefile", "test:\n\t@cargo +nightly-2026-10-30 test\n")

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_quoted_make_reference_is_a_reference(tmp_path: pathlib.Path) -> None:
    """Quoting the reference must not hide that it is one."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        "TOOL = $(PIN)\nPIN = nightly-2026-10-30\n\n"
        'test:\n\tRUSTUP_TOOLCHAIN="$(TOOL)" cargo test\n',
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_recovered_parse_is_carried_as_an_error(tmp_path: pathlib.Path) -> None:
    """A recovered parse may have lost the recipe that names the date."""
    rust_repository(tmp_path)
    write(tmp_path, "Makefile", "test:\n\tcargo test\n")
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

    assert not facts["literals"], facts
    assert [error["path"] for error in facts["errors"]] == ["Makefile"], facts
    assert "recovered" in facts["errors"][0]["reason"], facts


def test_a_makefile_outside_the_checkout_is_an_error(tmp_path: pathlib.Path) -> None:
    """A symlink must not feed another repository's recipes into the audit."""
    checkout = tmp_path / "checkout"
    external = tmp_path / "external"
    rust_repository(checkout)
    external.mkdir()
    (external / "Makefile").write_text("test:\n\tcargo test\n", encoding="utf-8")
    (checkout / "Makefile").symlink_to(external / "Makefile")

    facts = read(checkout)

    assert not facts["literals"], facts
    assert [error["path"] for error in facts["errors"]] == ["Makefile"], facts
    assert "outside the checkout" in facts["errors"][0]["reason"], facts


def test_a_recipe_prefix_does_not_hide_an_assignment(tmp_path: pathlib.Path) -> None:
    """Make strips `@` before the shell, so the word is still an assignment."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        "test:\n\t@RUSTUP_TOOLCHAIN=nightly-2026-10-30 cargo test\n",
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_command_variable_is_read_as_a_shell_command(tmp_path: pathlib.Path) -> None:
    """A quoted override in a command variable is shell syntax, not text."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        'CHECK = cargo "+nightly-2026-10-30" check\n\ntest:\n\t$(CHECK)\n',
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_command_variable_may_hold_several_commands(
    tmp_path: pathlib.Path,
) -> None:
    """Every command a command variable holds is read, not only the first."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        "CHECK = cargo +nightly-2026-10-30 check && "
        "cargo +nightly-2026-09-01 test\n"
        "\n"
        "test:\n"
        "\t$(CHECK)\n",
    )

    assert [text for _, _, text in literals(read(tmp_path))] == [
        "nightly-2026-10-30",
        "nightly-2026-09-01",
    ]


def test_a_prefixed_assignment_before_a_command_variable_is_read(
    tmp_path: pathlib.Path,
) -> None:
    """`@RUSTUP_TOOLCHAIN=stable $(CHECK)` still expands `CHECK`."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        "CHECK = cargo +nightly-2026-10-30 check\n"
        "\n"
        "test:\n"
        "\t@RUSTUP_TOOLCHAIN=stable $(CHECK)\n",
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]


def test_a_wrapped_command_variable_is_expanded(tmp_path: pathlib.Path) -> None:
    """`env $(CHECK)` runs the command `CHECK` names, overrides included."""
    rust_repository(tmp_path)
    write(
        tmp_path,
        "Makefile",
        "CHECK = cargo +nightly-2026-10-30 test\n\ntest:\n\tenv $(CHECK)\n",
    )

    assert [text for _, _, text in literals(read(tmp_path))] == ["nightly-2026-10-30"]
