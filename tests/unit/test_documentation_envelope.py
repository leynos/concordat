"""Specify the documentation-presence policy-input envelope."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.errors import OperationalRuleError
from concordat.rules import documentation_envelope as module
from concordat.rules import packages
from concordat.rules.documentation_envelope import (
    ENVELOPE_KIND,
    ENVELOPE_SCHEMA_VERSION,
    build_documentation_envelope,
)

if typ.TYPE_CHECKING:
    import pathlib

RULE_ID: typ.Final = "documentation-presence"


def _write(checkout: pathlib.Path, relative: str, text: str) -> pathlib.Path:
    """Write *text* at *relative* under *checkout* and return the path."""
    path = checkout / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _runs(checkout: pathlib.Path, makefile: str) -> list[module.InterrogateRun]:
    """Return the runs recorded for a checkout holding only *makefile*."""
    _write(checkout, "Makefile", makefile)
    return build_documentation_envelope(checkout)["runs"]


def test_an_empty_checkout_carries_no_facts(tmp_path: pathlib.Path) -> None:
    """Nothing to read is recorded as absence, never as a decoding error."""
    envelope = build_documentation_envelope(tmp_path)

    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert envelope["kind"] == ENVELOPE_KIND
    assert envelope["makefile_path"] is None
    assert envelope["lint_target"] is False
    assert envelope["runs"] == []
    assert envelope["configs"] == []
    assert envelope["python_files"] == []
    assert envelope["applicability"] == {
        "root_makefile": False,
        "python_files": False,
        "pyproject": False,
        "rust_library": False,
    }
    assert envelope["rust"] == {"crates": [], "errors": []}
    assert envelope["clippy_runs"] == []
    assert envelope["clippy_exceptions"] == {"records": [], "errors": []}


def test_python_files_are_listed_outside_the_pruned_trees(
    tmp_path: pathlib.Path,
) -> None:
    """Vendored, cached and virtual-environment trees are not owned source."""
    for relative in (
        "pkg/a.py",
        "tests/test_a.py",
        ".github/scripts/h.py",
        "vendor/dep/x.py",
        ".venv/lib/y.py",
        "node_modules/z/w.py",
        "notes.txt",
    ):
        _write(tmp_path, relative, "")

    files = build_documentation_envelope(tmp_path)["python_files"]

    assert files == [".github/scripts/h.py", "pkg/a.py", "tests/test_a.py"]


def test_a_symbolic_link_to_python_is_not_owned_source(
    tmp_path: pathlib.Path,
) -> None:
    """A link is judged by its target in its own right, never twice."""
    _write(tmp_path, "pkg/real.py", "")
    (tmp_path / "pkg" / "alias.py").symlink_to("real.py")

    assert build_documentation_envelope(tmp_path)["python_files"] == ["pkg/real.py"]


def test_variables_are_expanded_and_lint_is_followed(tmp_path: pathlib.Path) -> None:
    """A run in a prerequisite's recipe, spelled through variables, is found."""
    runs = _runs(
        tmp_path,
        "T ?= pkg tests\n"
        "I = uv run interrogate\n"
        "lint: docs\n\truff check .\n"
        "docs:\n\t$(I) -vv --fail-under 100 $(T)\n",
    )

    assert len(runs) == 1
    assert runs[0]["target"] == "docs"
    assert runs[0]["paths"] == ["pkg", "tests"]
    assert runs[0]["fail_under"] == "100"
    assert runs[0]["verbose"] == 2
    assert runs[0]["resolved"] is True


def test_a_target_lint_does_not_reach_is_reported_as_elsewhere(
    tmp_path: pathlib.Path,
) -> None:
    """A gate outside `lint` is a different defect from no gate."""
    _write(
        tmp_path,
        "Makefile",
        "lint:\n\truff check .\ndocs:\n\tinterrogate -f 100 pkg\n",
    )

    envelope = build_documentation_envelope(tmp_path)

    assert envelope["runs"] == []
    assert envelope["interrogate_elsewhere"] == ["docs"]


def test_a_sub_make_call_is_followed_and_is_not_a_run(tmp_path: pathlib.Path) -> None:
    """`$(MAKE) interrogate` reaches the target; it does not run the tool."""
    runs = _runs(
        tmp_path,
        "lint:\n\t+$(MAKE) interrogate\ninterrogate:\n\tinterrogate -f 100 pkg\n",
    )

    assert [run["target"] for run in runs] == ["interrogate"]


def test_text_that_only_names_the_tool_is_not_a_run(tmp_path: pathlib.Path) -> None:
    """Printing a message, or installing the package, runs nothing."""
    runs = _runs(
        tmp_path,
        "lint:\n\t@echo interrogate ran\n\tuv tool install interrogate==1.7.0\n"
        "\tuv run --with interrogate interrogate -f 100 pkg\n",
    )

    assert [run["paths"] for run in runs] == [["pkg"]]


def test_an_unresolvable_tool_variable_is_an_unresolved_run(
    tmp_path: pathlib.Path,
) -> None:
    """Losing `$(INTERROGATE)` would read as no gate at all."""
    runs = _runs(
        tmp_path,
        "INTERROGATE = $(shell find-it)\nlint:\n\t$(INTERROGATE) -f 100 pkg\n",
    )

    assert [run["resolved"] for run in runs] == [False]


def test_an_ambiguous_variable_leaves_the_run_unresolved(
    tmp_path: pathlib.Path,
) -> None:
    """A variable assigned twice has no one value to substitute."""
    runs = _runs(
        tmp_path,
        "T = a\nT = b\nlint:\n\tinterrogate -f 100 $(T)\n",
    )

    assert [run["resolved"] for run in runs] == [False]


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ("-vv", 2),
        ("-v -v", 2),
        ("--verbose --verbose", 2),
        ("-v", 1),
        ("", 0),
    ],
)
def test_the_verbose_level_counts_every_spelling(
    tmp_path: pathlib.Path, flags: str, expected: int
) -> None:
    """`-vv`, `-v -v` and `--verbose --verbose` all print every definition."""
    runs = _runs(tmp_path, f"lint:\n\tinterrogate {flags} -f 100 pkg\n")

    assert runs[0]["verbose"] == expected


def test_clustered_and_attached_options_are_read(tmp_path: pathlib.Path) -> None:
    """`-nC` relaxes twice; `-f100` and `--fail-under=100` carry a threshold."""
    runs = _runs(
        tmp_path,
        "lint:\n\tinterrogate -nC -f100 pkg\n\tinterrogate --fail-under=95 tests\n",
    )

    assert runs[0]["ignores"] == [
        "--ignore-nested-functions",
        "--ignore-nested-classes",
    ]
    assert runs[0]["fail_under"] == "100"
    assert runs[1]["fail_under"] == "95"


def test_ignoring_overloaded_stubs_is_not_a_relaxation(tmp_path: pathlib.Path) -> None:
    """Ruff forbids a docstring on an overload, so the flag is not reported."""
    runs = _runs(tmp_path, "lint:\n\tinterrogate -O -f 100 pkg\n")

    assert runs[0]["ignores"] == []


def test_a_shell_dollar_survives_as_a_literal(tmp_path: pathlib.Path) -> None:
    """`$$` in a variable's value is a shell `$`, as in an ignore regex."""
    runs = _runs(
        tmp_path,
        "R ?= ^main$$$$\nlint:\n\tinterrogate -f 100 -r '$(R)' pkg\n",
    )

    assert runs[0]["ignores"] == ["--ignore-regex ^main$$"]


def test_a_missed_guard_is_recorded_for_the_whole_recipe(
    tmp_path: pathlib.Path,
) -> None:
    """The guard may sit on a later line of the same target's recipe."""
    runs = _runs(
        tmp_path,
        'lint:\n\t@report="$$(interrogate -vv -f 100 pkg)"; echo "$$report" \\\n'
        "\t  | grep -q '| *MISSED |' && exit 1 || true\n",
    )

    assert runs[0]["guarded"] is True
    assert runs[0]["paths"] == ["pkg"]


@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        ("interrogate -vv -f 100 pkg > r.txt\n\t! grep -q MISSED r.txt\n", True),
        (
            "interrogate -vv -f 100 pkg > r.txt\n\tgrep -q MISSED r.txt && exit 1\n",
            True,
        ),
        (
            (
                "interrogate -vv -f 100 pkg > r.txt\n"
                "\tif grep -q '| *MISSED |' r.txt; then echo bad >&2; exit 2; fi\n"
            ),
            True,
        ),
        ("interrogate -vv -f 100 pkg > r.txt\n\tgrep -q MISSED r.txt || true\n", False),
        ("interrogate -vv -f 100 pkg > r.txt\n\tgrep -q MISSED r.txt\n", False),
        (
            (
                "interrogate -vv -f 100 pkg > r.txt\n"
                "\tif grep -q MISSED r.txt; then echo bad; fi\n"
            ),
            False,
        ),
    ],
)
def test_only_a_check_that_fails_on_a_match_is_a_guard(
    tmp_path: pathlib.Path, recipe: str, *, expected: bool
) -> None:
    """A grep whose result is ignored does not make the run exact."""
    runs = _runs(tmp_path, f"lint:\n\t{recipe}")

    assert runs[0]["guarded"] is expected


def test_a_redirection_destination_is_not_a_scanned_path(
    tmp_path: pathlib.Path,
) -> None:
    """`> report.txt` names a file the shell opens, not a path to measure."""
    runs = _runs(tmp_path, "lint:\n\tinterrogate -f 100 pkg > report.txt 2>&1\n")

    assert runs[0]["paths"] == ["pkg"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("uv run interrogate -f 100 pkg", 1),
        ("uv tool run --from 'interrogate==1.7.0' interrogate -f 100 pkg", 1),
        ("uvx --from interrogate==1.7.0 interrogate -f 100 pkg", 1),
        ("uv run --with interrogate --python 3.14 interrogate -f 100 pkg", 1),
        ("python3 -m interrogate -f 100 pkg", 1),
        ("true interrogate -f 100 pkg", 0),
        ("echo interrogate -f 100 pkg", 0),
        ("uv run true interrogate -f 100 pkg", 0),
        ("uv tool install interrogate==1.7.0", 0),
        ("uv run --with interrogate", 0),
    ],
)
def test_only_a_launcher_or_the_tool_itself_runs_interrogate(
    tmp_path: pathlib.Path, command: str, expected: int
) -> None:
    """A command that merely has `interrogate` among its arguments is no run."""
    runs = _runs(tmp_path, f"lint:\n\t{command}\n")

    assert len(runs) == expected


def test_make_in_another_directory_is_not_followed_and_is_unresolved(
    tmp_path: pathlib.Path,
) -> None:
    """`make -C sub interrogate` runs sub's target, which this walk cannot read."""
    _write(
        tmp_path,
        "Makefile",
        "lint:\n\t$(MAKE) -C sub interrogate\ninterrogate:\n\tinterrogate pkg\n",
    )

    envelope = build_documentation_envelope(tmp_path)

    assert envelope["reachability_resolved"] is False


def test_a_make_command_in_text_is_not_followed(tmp_path: pathlib.Path) -> None:
    """Only a `make` in command position reaches its target."""
    _write(
        tmp_path,
        "Makefile",
        (
            'lint:\n\t@echo "run make interrogate"\n'
            "interrogate:\n\tinterrogate -f 100 pkg\n"
        ),
    )

    envelope = build_documentation_envelope(tmp_path)

    assert envelope["runs"] == []
    assert envelope["interrogate_elsewhere"] == ["interrogate"]


def test_an_unguarded_run_is_recorded_as_such(tmp_path: pathlib.Path) -> None:
    """A recipe with no grep for MISSED is not exact."""
    runs = _runs(tmp_path, "lint:\n\tinterrogate -vv -f 100 pkg\n")

    assert runs[0]["guarded"] is False


def test_make_functions_build_the_config_path(tmp_path: pathlib.Path) -> None:
    """`$(abspath $(dir $(lastword $(MAKEFILE_LIST)))pyproject.toml)` resolves."""
    _write(tmp_path, "pyproject.toml", "[tool.interrogate]\nfail-under = 100\n")
    runs = _runs(
        tmp_path,
        "C = $(abspath $(dir $(lastword $(MAKEFILE_LIST)))pyproject.toml)\n"
        "lint:\n\tinterrogate --config $(C) pkg\n",
    )

    assert runs[0]["config"] == "pyproject.toml"
    assert runs[0]["resolved"] is True


def test_the_interrogate_table_is_read_from_pyproject(tmp_path: pathlib.Path) -> None:
    """The table is the fact; an absent table is an empty one."""
    _write(tmp_path, "Makefile", "lint:\n\tinterrogate pkg\n")
    _write(tmp_path, "pyproject.toml", "[tool.interrogate]\nfail-under = 100\n")

    envelope = build_documentation_envelope(tmp_path)

    assert envelope["configs"] == [
        {"path": "pyproject.toml", "table": {"fail-under": 100}, "error": None}
    ]
    assert envelope["applicability"]["pyproject"] is True


def test_a_malformed_pyproject_is_carried_with_its_error(
    tmp_path: pathlib.Path,
) -> None:
    """The policy reports indeterminate rather than reading the file as absent."""
    _write(tmp_path, "pyproject.toml", "[tool.interrogate\n")

    config = build_documentation_envelope(tmp_path)["configs"][0]

    assert config["table"] is None
    assert str(config["error"]).startswith("invalid TOML")


def test_a_non_toml_config_is_carried_with_an_error(tmp_path: pathlib.Path) -> None:
    """An `.ini` configuration cannot be read, so it is not guessed at."""
    _write(tmp_path, "Makefile", "lint:\n\tinterrogate --config setup.cfg pkg\n")
    _write(tmp_path, "setup.cfg", "[tool:interrogate]\n")

    configs = build_documentation_envelope(tmp_path)["configs"]

    assert [c["path"] for c in configs] == ["setup.cfg"]
    assert configs[0]["error"] is not None


def test_a_config_outside_the_checkout_is_refused(tmp_path: pathlib.Path) -> None:
    """No policy input is read from outside the checkout."""
    outside = tmp_path / "outside.toml"
    outside.write_text("[tool.interrogate]\n", encoding="utf-8")
    checkout = tmp_path / "checkout"
    _write(checkout, "Makefile", "lint:\n\tinterrogate --config ../outside.toml pkg\n")

    with pytest.raises(OperationalRuleError):
        build_documentation_envelope(checkout)


def test_the_package_registers_its_builder() -> None:
    """The rule id and the envelope kind both resolve to this builder."""
    assert RULE_ID in packages.PACKAGE_ENVELOPE_BUILDERS
    assert ENVELOPE_KIND in packages.INPUT_KIND_ENVELOPE_BUILDERS
