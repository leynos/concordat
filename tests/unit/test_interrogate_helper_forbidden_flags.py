"""Specify canonical helper flag refusals in documentation gate detection.

The local recipe fixture writes only a Makefile and calls the public envelope
builder. Only the tracked canonical fixture supplies the executable oracle;
consumer helper source is never imported or executed by the auditor.
"""

from __future__ import annotations

import pathlib
import runpy
import shlex
import typing as typ

import pytest

from concordat.rules.documentation_envelope import build_documentation_envelope

if typ.TYPE_CHECKING:
    from concordat.rules.interrogate_invocation import InterrogateRun


def _runs(checkout: pathlib.Path, makefile: str) -> list[InterrogateRun]:
    """Read one local Makefile through the public documentation envelope."""
    (checkout / "Makefile").write_text(makefile, encoding="utf-8")
    return build_documentation_envelope(checkout)["runs"]


@pytest.mark.parametrize("mode", ["run", "tool"])
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param("--offline", False, id="offline-regression"),
        pytest.param("--no-offline", False, id="no-offline"),
        pytest.param("--no-cache", False, id="no-cache"),
        pytest.param("--no-frozen", False, id="no-frozen"),
        pytest.param("--no-locked", False, id="no-locked"),
        pytest.param("--locked", False, id="locked"),
        pytest.param("--frozen", False, id="frozen"),
        pytest.param("--offline=true", False, id="equals-name"),
        pytest.param("--refresh", False, id="refresh"),
        pytest.param("--refresh-package=other", False, id="refresh-package"),
        pytest.param("--refresh-extra", False, id="refresh-family"),
        pytest.param("--upgrade", False, id="upgrade"),
        pytest.param("--upgrade-package=other", False, id="upgrade-package"),
        pytest.param("--upgrade-extra", False, id="upgrade-family"),
        pytest.param("--reinstall", False, id="reinstall"),
        pytest.param("--reinstall-package=other", False, id="reinstall-package"),
        pytest.param("--reinstall-extra", False, id="reinstall-family"),
        pytest.param("-U", False, id="short-upgrade"),
        pytest.param("-Pother", False, id="short-upgrade-package"),
        pytest.param("-n", False, id="short-no-cache"),
        pytest.param("-qU", False, id="cluster-upgrade"),
        pytest.param("-qP", False, id="cluster-upgrade-package"),
        pytest.param("-qn", False, id="cluster-no-cache"),
        pytest.param("-pPyPy", False, id="alphabetic-attached-value-fails-closed"),
        pytest.param("--python --offline", False, id="forbidden-option-value"),
        pytest.param("", True, id="no-extra-options"),
        pytest.param("--python 3.14", True, id="benign-long-option"),
        pytest.param("-p PyPy", True, id="separated-short-value"),
        pytest.param("-p3.14", True, id="numeric-attached-value"),
        pytest.param("-qv", True, id="benign-cluster"),
        pytest.param("--directory .", True, id="benign-directory"),
    ],
)
def test_helper_flags_match_the_selected_trusted_canon(
    tmp_path: pathlib.Path,
    mode: str,
    options: str,
    *,
    expected: bool,
) -> None:
    """Refused pre-separator flags cannot earn credit in either helper mode.

    Only the tracked, repository-owned canonical fixture is executed as the
    oracle; the auditor never imports the consumer's helper.
    """
    canon = pathlib.Path(__file__).parents[1] / "fixtures/uv_gate/uv_gate.py.canon"
    trusted = runpy.run_path(str(canon))
    validate = trusted["validate_request"]
    refusal = trusted["GateError"]
    specification = ["--from", "interrogate==1.7.0"] if mode == "tool" else []
    arguments = [
        *specification,
        *shlex.split(options),
        "--",
        "interrogate",
        "-vv",
        "-f100",
        "pkg",
    ]
    try:
        validate(mode, arguments)
    except refusal:
        accepted = False
    else:
        accepted = True
    command = shlex.join(["python3", "scripts/uv_gate.py", mode, *arguments])

    assert accepted is expected
    assert bool(_runs(tmp_path, f"lint:\n\t{command}\n")) is accepted


@pytest.mark.parametrize("mode", ["run", "tool"])
def test_helper_forbidden_flags_after_separator_belong_to_interrogate(
    tmp_path: pathlib.Path,
    mode: str,
) -> None:
    """Interrogate's own -P alias is not a pre-separator helper override."""
    specification = "--from interrogate==1.7.0 " if mode == "tool" else ""
    command = f"python3 scripts/uv_gate.py {mode} {specification}-- interrogate -P pkg"

    runs = _runs(tmp_path, f"lint:\n\t{command}\n")

    assert len(runs) == 1
    assert runs[0]["resolved"] is True
    assert runs[0]["ignores"] == ["--ignore-property-decorators"]


def test_forbidden_flag_cannot_recover_an_unknown_helper_spec(
    tmp_path: pathlib.Path,
) -> None:
    """A known canonical refusal remains refused before an unresolved spec."""
    runs = _runs(
        tmp_path,
        "SPEC = $(shell select-spec)\nlint:\n\tpython3 scripts/uv_gate.py tool "
        "--offline --from $(SPEC) -- interrogate pkg\n",
    )

    assert runs == []
