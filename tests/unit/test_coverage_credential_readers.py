"""Drive the upload guard and credential readers against synthetic publishers.

The repository's own publisher complies, so a contract run over it passes
whether or not a reader can see the hazard it exists for. Each case here
builds the hazard directly and requires the reader to see it.
"""

from __future__ import annotations

import pytest

from tests.unit.coverage_credential_support import (
    TOKEN_CHECK_COMMAND,
    is_guarded_upload,
    token_environments,
    unguarded_uploads,
    unpassed_credentials,
)
from tests.unit.coverage_topology_support import Workflow

_GUARD = "steps.t.outputs.available == 'true'"
_REF = "github.ref == 'refs/heads/main'"
_CHECK_STEP: dict[str, object] = {"id": "t", "run": TOKEN_CHECK_COMMAND}
_UPLOAD_STEP: dict[str, object] = {
    "name": "Upload",
    "if": f"{_GUARD} && {_REF}",
    "uses": "leynos/shared-actions/.github/actions/upload-codescene-coverage@abc",
    "with": {"access-token": "${{secrets.CS_ACCESS_TOKEN}}"},
}


def _publisher(*steps: dict[str, object]) -> Workflow:
    """Return a one-job publisher running ``steps`` in order."""
    return Workflow("w.yml", {"jobs": {"upload": {"steps": list(steps)}}})


@pytest.mark.parametrize(
    "condition",
    [
        f"{_GUARD} && {_REF}",
        f"${{{{ {_REF} && {_GUARD} }}}}",
        f"{_GUARD}  &&  'refs/heads/main' == github.ref",
    ],
)
def test_a_conjunctive_guard_is_accepted(condition: str) -> None:
    """Both requirements as whole conjuncts, in either order, is a guard."""
    assert is_guarded_upload(condition, {"t"})


@pytest.mark.parametrize(
    "condition",
    [
        # The sweep's named mutation: the disjunct makes both conjuncts
        # optional, while a substring test still finds the main ref.
        f"{_GUARD} && {_REF} || github.event_name == 'workflow_dispatch'",
        # The same disjunct landing on the credential's conjunct, which a
        # reader matching the ref conjunct exactly would still accept.
        f"{_REF} && {_GUARD} || github.event_name == 'workflow_dispatch'",
        # A disjunct after a third conjunct leaves both guards whole, yet
        # `||` binds loosest, so the dispatch alone uploads.
        (
            f"{_GUARD} && {_REF} && github.actor != 'bot'"
            " || github.event_name == 'workflow_dispatch'"
        ),
        _GUARD,
        _REF,
        f"steps.t.outputs.available == 'false' && {_REF}",
        f"steps.other.outputs.available == 'true' && {_REF}",
        f"{_GUARD} && github.ref != 'refs/heads/main'",
        f"{_GUARD} && github.ref == 'refs/heads/main-backup'",
        f"{_GUARD} && ({_REF})",
        # The retired shape: it needs the token in the step's `env`.
        f"env.CS_ACCESS_TOKEN != '' && {_REF}",
        f"secrets.CS_ACCESS_TOKEN != '' && {_REF}",
        "",
    ],
)
def test_an_insufficient_guard_is_refused(condition: str) -> None:
    """Anything but the two whole conjuncts joined by `&&` is refused."""
    assert not is_guarded_upload(condition, {"t"})


def test_a_quoted_operator_does_not_split_the_guard() -> None:
    """An operator inside a string literal is text, not logic."""
    condition = f"{_GUARD} && {_REF} && github.actor != 'a || b'"
    assert is_guarded_upload(condition, {"t"})


def test_a_guarded_upload_after_its_check_is_accepted() -> None:
    """The compliant shape: an unconditional check, then the guarded upload."""
    assert unguarded_uploads(_publisher(_CHECK_STEP, _UPLOAD_STEP)) == {}


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param([_UPLOAD_STEP], id="check deleted"),
        pytest.param([_UPLOAD_STEP, _CHECK_STEP], id="check after the upload"),
        pytest.param(
            [_CHECK_STEP | {"if": "github.actor != 'x'"}, _UPLOAD_STEP],
            id="check skipped by its own condition",
        ),
        pytest.param(
            [
                _CHECK_STEP | {"run": 'echo "available=true" >> "$GITHUB_OUTPUT"'},
                _UPLOAD_STEP,
            ],
            id="command altered",
        ),
        pytest.param(
            [
                _CHECK_STEP | {"run": f"{TOKEN_CHECK_COMMAND}\necho x >> y"},
                _UPLOAD_STEP,
            ],
            id="command not the sole command",
        ),
        pytest.param(
            [{"run": TOKEN_CHECK_COMMAND}, _UPLOAD_STEP], id="check has no id"
        ),
    ],
)
def test_the_upload_needs_an_unconditional_check_before_it(
    steps: list[dict[str, object]],
) -> None:
    """A missing, late, skipped or altered check leaves the upload dead.

    Its output is then empty, so the guard is false on every run and the
    upload skips without failing anything.
    """
    assert list(unguarded_uploads(_publisher(*steps))) == ["upload 0: Upload"]


def test_a_check_in_another_job_does_not_guard_the_upload() -> None:
    """Another job's step outputs are not in scope for this job's `if:`."""
    publisher = Workflow(
        "w.yml",
        {
            "jobs": {
                "check": {"steps": [_CHECK_STEP]},
                "upload": {"steps": [_UPLOAD_STEP]},
            }
        },
    )
    assert list(unguarded_uploads(publisher)) == ["upload 0: Upload"]


@pytest.mark.parametrize(
    "access_token",
    [None, "${{ env.CS_ACCESS_TOKEN }}", "${{ secrets.OTHER }}", "literal"],
)
def test_the_upload_must_pass_the_secret_itself(access_token: str | None) -> None:
    """Only the secret, spelled as an expression, reaches the action."""
    inputs = {} if access_token is None else {"access-token": access_token}
    publisher = _publisher(_CHECK_STEP, _UPLOAD_STEP | {"with": inputs})
    assert list(unpassed_credentials(publisher)) == ["upload 0: Upload"]


def test_a_compliant_upload_does_not_mask_an_unpassed_one() -> None:
    """Two unnamed upload steps are judged separately, in either order.

    Keyed on the name alone, the later compliant step overwrote the earlier
    offending one and the clause reported nothing.
    """
    unnamed = {key: value for key, value in _UPLOAD_STEP.items() if key != "name"}
    unpassed = unnamed | {"with": {}}
    for steps, offending in (([unpassed, unnamed], 0), ([unnamed, unpassed], 1)):
        publisher = _publisher(_CHECK_STEP, *steps)
        assert list(unpassed_credentials(publisher)) == [f"upload {offending}: None"], (
            steps
        )


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (
            {"env": {"CS_ACCESS_TOKEN": "${{ secrets.CS_ACCESS_TOKEN }}"}},
            ["workflow"],
        ),
        (
            {"jobs": {"upload": {"env": {"TOKEN": "${{ secrets.CS_ACCESS_TOKEN }}"}}}},
            ["job 'upload'"],
        ),
        (
            {
                "jobs": {
                    "upload": {
                        "steps": [
                            _CHECK_STEP,
                            _UPLOAD_STEP
                            | {"env": {"CS_ACCESS_TOKEN": "${{ secrets.X }}"}},
                        ]
                    }
                }
            },
            ["job 'upload' step 1"],
        ),
        ({"jobs": {"upload": {"steps": [_CHECK_STEP, _UPLOAD_STEP]}}}, []),
    ],
)
def test_the_token_in_any_env_block_is_seen(
    document: dict[object, object], expected: list[str]
) -> None:
    """A binding at workflow, job or step scope reaches steps beyond the upload."""
    assert token_environments(Workflow("w.yml", document)) == expected
