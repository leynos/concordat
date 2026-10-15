"""Specify the documentation-presence policy-input envelope."""

from __future__ import annotations

import pathlib
import runpy
import shlex
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
        pytest.param(
            "python -m interrogate -f 100 pkg",
            1,
            id="python-module-launcher",
        ),
        pytest.param(
            "python3 run -- interrogate -f 100 pkg",
            0,
            id="python-script-named-run-is-not-a-launcher",
        ),
        pytest.param(
            "python3 tool run interrogate -f 100 pkg",
            0,
            id="python-script-named-tool-is-not-a-launcher",
        ),
        pytest.param(
            "python3 run -m interrogate -f 100 pkg",
            0,
            id="python-script-argument-is-not-a-module-switch",
        ),
        pytest.param(
            "python3 -I -m interrogate -f 100 pkg",
            0,
            id="unsupported-python-options-fail-closed",
        ),
        pytest.param(
            "pipx run interrogate -f 100 pkg",
            1,
            id="pipx-run-launcher-retained",
        ),
        (
            "UV_CACHE_DIR=.uv-cache uv tool run --from i interrogate pkg",
            1,
        ),
        ("env X=1 uv run interrogate -f 100 pkg", 1),
        ("@env X=1 uv run interrogate -f 100 pkg", 1),
        ("env -i -u HOME X=1 uv run interrogate -f 100 pkg", 1),
        ("env -- X=1 uv run interrogate -f 100 pkg", 1),
        ('"X=1" interrogate -f 100 pkg', 0),
        ("'X=1' uv run interrogate -f 100 pkg", 0),
        ("X=1 true interrogate -f 100 pkg", 0),
        pytest.param(
            "uv scripts/uv_gate.py run -- interrogate -f 100 pkg",
            0,
            id="uv-does-not-launch-the-python-helper-path",
        ),
        pytest.param(
            "pipx scripts/uv_gate.py run -- interrogate -f 100 pkg",
            0,
            id="pipx-does-not-launch-the-python-helper-path",
        ),
        (
            (
                "python3 scripts/uv_gate.py tool --from "
                "interrogate==1.7.0 -- interrogate pkg"
            ),
            1,
        ),
        ("python3 scripts/uv_gate.py run -- interrogate -f 100 pkg", 1),
        pytest.param(
            "python3 ./scripts/uv_gate.py run -- interrogate -f 100 pkg",
            1,
            id="helper-normalized-relative-path",
        ),
        pytest.param(
            "python3 scripts/sub/../uv_gate.py run -- interrogate -f 100 pkg",
            0,
            id="helper-parent-segment-rejected",
        ),
        pytest.param(
            "python3 tools/uv_gate.py run -- interrogate -f 100 pkg",
            0,
            id="other-directory-same-helper-basename",
        ),
        pytest.param(
            "python3 uv_gate.py run -- interrogate -f 100 pkg",
            0,
            id="bare-helper-basename",
        ),
        pytest.param(
            "python3 /scripts/uv_gate.py run -- interrogate -f 100 pkg",
            0,
            id="absolute-helper-path",
        ),
        pytest.param(
            "python3 scripts/uv_gate.py run interrogate -f 100 pkg",
            0,
            id="helper-run-missing-command-separator",
        ),
        pytest.param(
            "python3 scripts/uv_gate.py run --python 3.14 interrogate -f 100 pkg",
            0,
            id="helper-run-options-without-command-separator",
        ),
        pytest.param(
            "python3 scripts/uv_gate.py run --python 3.14 -- interrogate -f 100 pkg",
            1,
            id="helper-run-options-with-command-separator",
        ),
        pytest.param(
            "python3 scripts/uv_gate.py run -- -- interrogate -f 100 pkg",
            0,
            id="helper-run-repeated-command-separator",
        ),
        ("python3 scripts/uv_gate.py run -- true interrogate -f 100 pkg", 0),
        ("python3 scripts/other.py tool -- interrogate -f 100 pkg", 0),
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


def test_a_make_command_behind_an_environment_prefix_is_followed(
    tmp_path: pathlib.Path,
) -> None:
    """`X=1 make target` reaches the target like a bare `make target`."""
    _write(
        tmp_path,
        "Makefile",
        "lint:\n\tX=1 $(MAKE) interrogate\ninterrogate:\n\tinterrogate -f 100 pkg\n",
    )

    envelope = build_documentation_envelope(tmp_path)

    assert [run["target"] for run in envelope["runs"]] == ["interrogate"]


@pytest.mark.parametrize(
    "spec",
    [
        "interrogate==1.7.0",
        "interrogate@1.7.0",
        "other[extra]==1.0rc1",
        "other==1!2.0+local",
        "other@LATEST",
        "other@1.0@2",
        "other==1==2",
        "interrogate",
        "interrogate>=1.7",
        "interrogate~=1.7",
        "other@latest",
        "other==latest",
        "other==",
        "other@",
        "-other==1.0",
        "other===1.0",
        "other==1.0.*",
        "other[extra]==1.0",
        "other[]==1.0",
        "other[extra==value]==1.0",
        "other[extra@value]@1.0",
        "other[extra@value]==1.0",
        "other[extra==value]@1.0",
        "git+https://example.invalid/tool.git@" + "a" * 40,
        "git+ssh://git@example.invalid/tool.git@" + "a" * 40 + "#subdirectory=src",
        "other[extra] @ git+https://example.invalid/tool.git@" + "a" * 40,
        "git+https://example.invalid/tool.git@main",
        "git+@" + "a" * 40,
        "git+https://example.invalid/tool.git@" + "a" * 40 + "#extra@main",
        "other[extra] @ git+https://example.invalid/tool.git@" + "a" * 40 + "#src",
        "git+https://example.invalid/tool.git@" + "A" * 40,
        "git+https://example.invalid/tool.git@" + "a" * 39,
        "other @ git+https://example.invalid/tool.git@latest",
    ],
)
def test_helper_tool_specs_match_the_selected_trusted_canon(
    tmp_path: pathlib.Path,
    spec: str,
) -> None:
    """Credit only specifications the tracked canonical helper accepts.

    The oracle is repository-owned test data, never source from the checkout
    being audited. Its established canonical-digest test guards provenance.
    """
    canon = pathlib.Path(__file__).parents[1] / "fixtures/uv_gate/uv_gate.py.canon"
    trusted = runpy.run_path(str(canon))
    validate = trusted["validate_request"]
    refusal = trusted["GateError"]
    try:
        validate("tool", ["--from", spec, "--", "interrogate", "pkg"])
    except refusal:
        accepted = False
    else:
        accepted = True
    command = (
        "python3 scripts/uv_gate.py tool --from "
        + shlex.quote(spec)
        + " -- interrogate pkg"
    )

    assert bool(_runs(tmp_path, f"lint:\n\t{command}\n")) is accepted


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ("--from=interrogate==1.7.0 --", True),
        ("--from=interrogate --", False),
        ("--from interrogate --from other==1.0 --", False),
        ("--from other==1.0 --from interrogate --", True),
        ("-- --from other==1.0", False),
        ("-- --", False),
        ("--", False),
        ("--from --", False),
    ],
)
def test_helper_tool_uses_the_first_spec_before_the_separator(
    tmp_path: pathlib.Path,
    options: str,
    *,
    expected: bool,
) -> None:
    """The helper selects the first --from before the first separator."""
    command = f"python3 scripts/uv_gate.py tool {options} interrogate pkg"

    assert bool(_runs(tmp_path, f"lint:\n\t{command}\n")) is expected


def test_unresolved_helper_tool_spec_is_not_credited_as_a_resolved_run(
    tmp_path: pathlib.Path,
) -> None:
    """Unknown Make expansion remains visible as an indeterminate run."""
    runs = _runs(
        tmp_path,
        "SPEC = $(shell select-spec)\nlint:\n\tpython3 scripts/uv_gate.py tool "
        "--from $(SPEC) -- interrogate pkg\n",
    )

    assert len(runs) == 1
    assert runs[0]["resolved"] is False


@pytest.mark.parametrize(
    ("launcher", "expected"),
    [
        ("python3/", False),
        ("python3/.", False),
        ("/usr/bin/python3/", False),
        ("/usr/bin/python3/.", False),
        ("./python3", True),
        ("/usr/bin/python3", True),
    ],
)
def test_helper_launcher_keeps_its_raw_terminal_component(
    tmp_path: pathlib.Path,
    launcher: str,
    *,
    expected: bool,
) -> None:
    """Path normalization must not turn a directory operand into a launcher."""
    command = f"{launcher} scripts/uv_gate.py run -- interrogate pkg"

    assert bool(_runs(tmp_path, f"lint:\n\t{command}\n")) is expected


@pytest.mark.parametrize(
    "launcher",
    ["uv run --with", "uv tool run --from", "uvx --from", "pipx run --python"],
)
def test_unresolved_direct_launcher_options_remain_unresolved(
    tmp_path: pathlib.Path,
    launcher: str,
) -> None:
    """An unknown option value must not gain resolved credit from its suffix."""
    runs = _runs(
        tmp_path,
        f"SPEC = $(shell select-spec)\nlint:\n\t{launcher} $(SPEC) interrogate pkg\n",
    )

    assert len(runs) == 1
    assert runs[0]["resolved"] is False


@pytest.mark.parametrize(
    "prefix",
    [
        "python3 tools/uv_gate.py tool --from $(SPEC)",
        "python3/ scripts/uv_gate.py tool --from $(SPEC)",
        "echo scripts/uv_gate.py tool --from $(SPEC)",
        "uv scripts/uv_gate.py tool --from $(SPEC)",
        "python3 scripts/uv_gate.py tool --from interrogate --python $(SPEC)",
    ],
)
def test_unknown_spec_recovery_is_only_for_the_canonical_helper(
    tmp_path: pathlib.Path,
    prefix: str,
) -> None:
    """An unknown value cannot resurrect an invalid helper or unpinned spec."""
    runs = _runs(
        tmp_path,
        f"SPEC = $(shell select-spec)\nlint:\n\t{prefix} -- interrogate pkg\n",
    )

    assert runs == []


@pytest.mark.parametrize(
    "launcher",
    ["uv run --with", "uv tool run --from", "uvx --from", "pipx run --python"],
)
def test_unresolved_direct_launcher_with_separator_is_not_lost(
    tmp_path: pathlib.Path,
    launcher: str,
) -> None:
    """Explicit command separators do not hide unmeasured direct routes."""
    runs = _runs(
        tmp_path,
        "SPEC = $(shell select-spec)\nlint:\n\t"
        f"{launcher} $(SPEC) -- interrogate pkg\n",
    )

    assert len(runs) == 1
    assert runs[0]["resolved"] is False


@pytest.mark.parametrize(
    ("launcher", "value"),
    [
        ("uv run --with", "interrogate==1.7.0"),
        ("uv tool run --from", "interrogate==1.7.0"),
        ("uvx --from", "interrogate==1.7.0"),
        ("pipx run --python", "3.13"),
    ],
)
def test_known_direct_launcher_option_expansions_stay_resolved(
    tmp_path: pathlib.Path,
    launcher: str,
    value: str,
) -> None:
    """A single known Make value retains the complete resolved invocation."""
    runs = _runs(
        tmp_path,
        f"SPEC = {value}\nlint:\n\t{launcher} $(SPEC) interrogate pkg\n",
    )

    assert len(runs) == 1
    assert runs[0]["resolved"] is True


@pytest.mark.parametrize(
    "command",
    [
        "echo uv run --with $(SPEC) interrogate pkg",
        "uv run true --with $(SPEC) interrogate pkg",
        "uv wrong --with $(SPEC) interrogate pkg",
        "uv/ run --with $(SPEC) interrogate pkg",
    ],
)
def test_unknown_expansion_cannot_credit_wrong_or_invalid_commands(
    tmp_path: pathlib.Path,
    command: str,
) -> None:
    """Unprovable suffixes are conservative facts, never resolved gate credit."""
    runs = _runs(
        tmp_path,
        f"SPEC = $(shell select-spec)\nlint:\n\t{command}\n",
    )

    assert not any(run["resolved"] for run in runs)


def test_unresolved_recipe_preserves_detected_run_identities(
    tmp_path: pathlib.Path,
) -> None:
    """Conservative recipe provenance changes resolution, not run identities."""
    runs = _runs(
        tmp_path,
        "SPEC = $(shell select-spec)\nlint:\n\tuv run --with $(SPEC) "
        "interrogate first; interrogate second\n",
    )

    assert [run["paths"] for run in runs] == [["first"], ["second"]]
    assert [run["resolved"] for run in runs] == [False, False]


def test_plain_python_helper_records_the_complete_interrogate_run(
    tmp_path: pathlib.Path,
) -> None:
    """Plain python launches the helper and retains the complete audit facts."""
    runs = _runs(
        tmp_path,
        "lint:\n\tpython scripts/uv_gate.py run -- interrogate -f 100 pkg\n",
    )

    assert runs == [
        {
            "target": "lint",
            "line": 2,
            "command": "interrogate -f 100 pkg",
            "resolved": True,
            "paths": ["pkg"],
            "fail_under": "100",
            "verbose": 0,
            "config": None,
            "excludes": [],
            "ignores": [],
            "guarded": False,
        }
    ]
