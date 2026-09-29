"""End-to-end and property tests for the rule's estate flag requirement.

PD-002 and PD-003 require every `mdtablefix` invocation to carry the selection
flags and the rewrite flags. These tests run the real `makeutil` and the real
Conftest: an end-to-end run of `concordat artefact rule run` over the fixture
scenarios that omit the rewrite flags, and a property over generated flag
parameters and invocation tokens, which is the only way to prove the rule reads
its parameters rather than a hard-coded list.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import typing as typ

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from concordat import cli
from concordat.rules import runner
from concordat.rules.packages import default_envelope_builder
from tests.helpers.github_api import git_object_routes

if typ.TYPE_CHECKING:
    import types

    from tests.helpers.github_api import FakeGithubApi

RULE_ID: typ.Final = "markdown-formatting-baseline"
PACKAGE: typ.Final = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform-standards/canon/lint-rules"
    / RULE_ID
)
ACTION: typ.Final = "DavidAnson/markdownlint-cli2-action"
ESTATE_FLAGS: typ.Final = "--wrap --renumber --breaks --ellipsis --fences"
# Distinct flag-shaped names, so a generated parameter never collides with a
# flag the tool itself understands.
POOL: typ.Final = [f"--flag-{name}" for name in "abcdefgh"]


def _generator() -> types.ModuleType:
    """Import the package's fixture generator by path."""
    spec = importlib.util.spec_from_file_location(
        "markdown_rule_flags_generate", PACKAGE / "fixtures/generate.py"
    )
    if spec is None or spec.loader is None:
        message = "could not load the fixture generator"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GENERATE: typ.Final = _generator()


def _main(argv: list[str]) -> int:
    """Run the CLI, returning its exit status however cyclopts reports it."""
    try:
        return cli.main(argv)
    except SystemExit as exit_:
        return int(exit_.code or 0)


@pytest.fixture(autouse=True)
def _no_gh_cli_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the host's `gh` login out of these runs."""
    monkeypatch.setattr(cli, "_gh_cli_token", lambda _host: None)


@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    ("scenario", "missing"),
    [
        pytest.param("missing_rule_flags", ESTATE_FLAGS, id="none-of-the-five"),
        pytest.param("partial_rule_flags", "--breaks --ellipsis --fences", id="two"),
    ],
)
def test_the_command_reports_the_missing_rewrite_flags(
    scenario: str,
    missing: str,
    tmp_path: pathlib.Path,
    fake_github_api: FakeGithubApi,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command exits 1 and names exactly the flags PD-002 and PD-003 lack."""
    fake_github_api.routes.update(
        git_object_routes(ACTION, commits=[GENERATE.V24_2_0_COMMIT])
    )
    checkout = tmp_path / scenario
    checkout.mkdir()
    GENERATE.lay_out(GENERATE.SCENARIOS[scenario], checkout)
    returncode = _main([
        "artefact",
        "rule",
        "run",
        RULE_ID,
        "--repo",
        str(checkout),
        "--format",
        "json",
        "--github-api-url",
        fake_github_api.url,
        "--no-pin-cache",
    ])
    document = json.loads(capsys.readouterr().out)
    messages = {
        finding["rule_id"]: finding["message"]
        for finding in document["findings"]
        if finding["verdict"] == "noncompliant"
    }
    assert returncode == 1, document
    assert set(messages) == {"PD-002", "PD-003"}, document
    assert messages["PD-002"].endswith(f"mdtablefix --check without {missing}"), (
        messages
    )
    assert messages["PD-003"].endswith(f"mdtablefix --in-place without {missing}"), (
        messages
    )


def _makefile(tokens: list[str]) -> str:
    """Return a Makefile whose two targets run mdtablefix with ``tokens``."""
    words = " ".join(tokens)
    lint = 'markdownlint-cli2 --fix "**/*.md"'
    return (
        ".PHONY: fmt check-fmt\n\n"
        f"fmt:\n\tmdtablefix --in-place {words}\n\t{lint}\n\n"
        f"check-fmt:\n\tmdtablefix --check {words}\n"
    )


@st.composite
def _cases(draw: st.DrawFn) -> tuple[list[str], list[str], list[str]]:
    """Draw selection flags, rewrite flags and the tokens an invocation carries.

    The two flag lists are disjoint and come from a shared pool; the invocation
    is any subset of the pool plus a decoy no parameter names, so complete,
    partial, empty, extra-token and custom-flag cases all occur.

    Returns
    -------
    tuple[list[str], list[str], list[str]]
        The selection flags, the rewrite flags, and the invocation's tokens.
    """
    chosen = draw(st.lists(st.sampled_from(POOL), min_size=1, unique=True, max_size=6))
    split = draw(st.integers(min_value=0, max_value=len(chosen)))
    tokens = draw(st.lists(st.sampled_from([*POOL, "--decoy"]), unique=True))
    return chosen[:split], chosen[split:], tokens


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(case=_cases())
def test_the_rule_reads_both_flag_parameters(
    case: tuple[list[str], list[str], list[str]],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever the parameters, PD-002 and PD-003 name exactly the missing flags.

    Required flags are the selection list then the rewrite list; an invocation
    is compliant precisely when it carries all of them, and otherwise the
    message lists the absent ones in that order.
    """
    select, rewrite, tokens = case
    required = [*select, *rewrite]
    missing = [flag for flag in required if flag not in tokens]
    checkout = tmp_path_factory.mktemp("flags")
    (checkout / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (checkout / "Makefile").write_text(_makefile(tokens), encoding="utf-8")
    original = runner._rule_parameters
    monkeypatch.setattr(
        runner,
        "_rule_parameters",
        lambda rule_dir: {
            **original(rule_dir),
            "mdtablefix_select_flags": select,
            "mdtablefix_rule_flags": rewrite,
        },
    )
    envelope = default_envelope_builder(RULE_ID, checkout)
    findings = runner._findings_from_results(runner._invoke_conftest(RULE_ID, envelope))
    flagged = {
        finding.rule_id: finding.message
        for finding in findings
        if finding.rule_id in {"PD-002", "PD-003"} and finding.verdict == "noncompliant"
    }
    if missing:
        assert set(flagged) == {"PD-002", "PD-003"}, (required, tokens, findings)
        assert all(message.endswith(" ".join(missing)) for message in flagged.values())
    else:
        assert flagged == {}, (required, tokens, findings)
