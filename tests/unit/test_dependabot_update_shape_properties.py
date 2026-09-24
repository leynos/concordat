"""Independent property tests for DB-005, the Dependabot update shape.

Each generated case is a set of semantic decisions about a repository's
Dependabot configuration: each entry's ecosystem, cadence, group layout and
versioning strategy, which local actions exist, and how the github-actions
entry lists its directories. The case is rendered to YAML in a scratch
checkout, audited end to end by the real envelope builder and Conftest policy,
and compared with expectations computed directly from the decisions. The
expectations are predicates over the case, not a second reading of the
rendered document, so a defect in the policy's reader cannot be mirrored here.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import tempfile
import typing as typ

from hypothesis import example, given, settings
from hypothesis import strategies as st
from ruamel.yaml import YAML

from concordat.rules import runner

_RULE_ID: typ.Final = "dependabot-update-shape"
_CATCH_ALL: typ.Final[dict[str, object]] = {
    "patterns": ["*"],
    "update-types": ["minor", "patch"],
}

# How the entry's final group departs from the catch-all, if at all.
_CATCH_ALL_FORMS: typ.Final[dict[str, dict[str, object]]] = {
    "exact": _CATCH_ALL,
    "reordered_types": {"patterns": ["*"], "update-types": ["patch", "minor"]},
    "version_updates": {**_CATCH_ALL, "applies-to": "version-updates"},
    "no_update_types": {"patterns": ["*"]},
    "with_major": {"patterns": ["*"], "update-types": ["major", "minor", "patch"]},
    "security_only": {**_CATCH_ALL, "applies-to": "security-updates"},
    "excludes": {**_CATCH_ALL, "exclude-patterns": ["ruff"]},
    "named_pattern": {"patterns": ["serde*"], "update-types": ["minor", "patch"]},
}
_GOOD_CATCH_ALLS: typ.Final = frozenset({"exact", "reordered_types", "version_updates"})

# Groups that may precede the catch-all, and whether each is narrow.
_LEADING_FORMS: typ.Final[dict[str, tuple[dict[str, object], bool]]] = {
    "prefix": ({"patterns": ["rstest-bdd*"]}, True),
    "named_list": ({"patterns": ["digest", "sha2"], "update-types": ["major"]}, True),
    "bare_wildcard": ({"patterns": ["*"]}, False),
    "double_wildcard": ({"patterns": ["serde", "**"]}, False),
    "type_only": ({"dependency-type": "production"}, False),
}

_STRATEGIES: typ.Final = (None, "auto", "lockfile-only", "increase", "widen")
_ACTIONS: typ.Final = ("/.github/actions/setup", "/.github/actions/rust/build")

# The github-actions entry's directory listing, and which paths each covers.
_LISTINGS: typ.Final = {
    "scalar_root": ({"directory": "/"}, {"/"}),
    "root_only": ({"directories": ["/"]}, {"/"}),
    "root_and_star": (
        {"directories": ["/", "/.github/actions/*"]},
        {"/", "/.github/actions/setup"},
    ),
    "root_and_globstar": (
        {"directories": ["/", "/.github/actions/**"]},
        {"/", *_ACTIONS},
    ),
    "each_listed": (
        {"directories": ["/", ".github/actions/setup/", "/.github/actions/rust/build"]},
        {"/", *_ACTIONS},
    ),
    "star_only": (
        {"directories": ["/.github/actions/*"]},
        {"/.github/actions/setup"},
    ),
}


@dataclasses.dataclass(frozen=True)
class EntryCase:
    """One `updates` entry, stated as decisions rather than syntax."""

    ecosystem: str
    interval: str | None
    leading: tuple[str, ...]
    catch_all: str | None
    strategy: str | None


@dataclasses.dataclass(frozen=True)
class RepositoryCase:
    """One repository's Dependabot configuration and local actions."""

    entries: tuple[EntryCase, ...]
    actions: tuple[str, ...]
    listing: str


_ENTRIES = st.builds(
    EntryCase,
    ecosystem=st.sampled_from(["cargo", "uv", "npm"]),
    interval=st.sampled_from(["daily", "daily", "weekly", "monthly", None]),
    leading=st.lists(st.sampled_from(sorted(_LEADING_FORMS)), max_size=2).map(tuple),
    catch_all=st.sampled_from([*_CATCH_ALL_FORMS, None]),
    strategy=st.sampled_from(_STRATEGIES),
)

_CASES = st.builds(
    RepositoryCase,
    entries=st.lists(_ENTRIES, min_size=1, max_size=3).map(tuple),
    actions=st.lists(st.sampled_from(_ACTIONS), unique=True).map(tuple),
    listing=st.sampled_from(sorted(_LISTINGS)),
)


def _entry_document(case: EntryCase) -> dict[str, object]:
    """Render one entry case as a Dependabot `updates` mapping."""
    entry: dict[str, object] = {"package-ecosystem": case.ecosystem, "directory": "/"}
    if case.interval is not None:
        entry["schedule"] = {"interval": case.interval}
    groups: dict[str, dict[str, object]] = {
        f"lead-{position}": _LEADING_FORMS[form][0]
        for position, form in enumerate(case.leading)
    }
    if case.catch_all is not None:
        groups["minor-and-patch"] = _CATCH_ALL_FORMS[case.catch_all]
    if groups:
        entry["groups"] = groups
    if case.strategy is not None:
        entry["versioning-strategy"] = case.strategy
    return entry


def _actions_entry(case: RepositoryCase) -> dict[str, object]:
    """Render the github-actions entry, compliant but for its directories."""
    return {
        "package-ecosystem": "github-actions",
        **_LISTINGS[case.listing][0],
        "schedule": {"interval": "daily"},
        "groups": {"minor-and-patch": _CATCH_ALL},
    }


def _write_checkout(root: pathlib.Path, case: RepositoryCase) -> None:
    """Write the configuration and stub action manifests under *root*."""
    document = {
        "version": 2,
        "updates": [*map(_entry_document, case.entries), _actions_entry(case)],
    }
    config = root / ".github" / "dependabot.yml"
    config.parent.mkdir(parents=True)
    with config.open("w", encoding="utf-8") as stream:
        YAML().dump(document, stream)
    for directory in case.actions:
        manifest = root / directory.lstrip("/") / "action.yml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("name: stub\n", encoding="utf-8")


def _entry_expected(index: int, case: EntryCase) -> set[tuple[str, ...]]:
    """Decide which of one entry's clauses fail, from its decisions alone."""
    expected: set[tuple[str, ...]] = set()
    if case.interval != "daily":
        expected.add((str(index), "interval"))
    names = [f"lead-{position}" for position in range(len(case.leading))]
    if case.catch_all is not None:
        names.append("minor-and-patch")
    if not names:
        expected.add((str(index), "no groups"))
        return expected | _strategy_expected(index, case)
    final_is_catch_all = case.catch_all in _GOOD_CATCH_ALLS
    if not final_is_catch_all:
        expected.add((str(index), "last", names[-1]))
    for position, name in enumerate(names[:-1]):
        if not _LEADING_FORMS[case.leading[position]][1]:
            expected.add((str(index), "not narrow", name))
    return expected | _strategy_expected(index, case)


def _strategy_expected(index: int, case: EntryCase) -> set[tuple[str, ...]]:
    """Decide the cargo versioning clause from the entry's decisions."""
    if case.ecosystem == "cargo" and case.strategy in {"increase", "widen"}:
        return {(str(index), "strategy")}
    return set()


def _expected(case: RepositoryCase) -> set[tuple[str, ...]]:
    """Decide every clause's outcome for one repository case."""
    expected: set[tuple[str, ...]] = set()
    for index, entry in enumerate(case.entries):
        expected |= _entry_expected(index, entry)
    if case.actions:
        covered = _LISTINGS[case.listing][1]
        expected |= {
            ("actions", path) for path in {"/", *case.actions} if path not in covered
        }
    return expected


_ENTRY_CLAUSES: typ.Final = (
    (re.compile(r"^updates\[(\d+)\] \([^)]*\) schedule\.interval"), "interval"),
    (re.compile(r"^updates\[(\d+)\] \([^)]*\) has no groups"), "no groups"),
    (re.compile(r"^updates\[(\d+)\] \([^)]*\) versioning-strategy"), "strategy"),
)
_NAMED_CLAUSES: typ.Final = (
    (re.compile(r'^updates\[(\d+)\] \([^)]*\) last group "([^"]+)"'), "last"),
    (re.compile(r'^updates\[(\d+)\] \([^)]*\) group "([^"]+)" precedes'), "not narrow"),
)
_ACTIONS_CLAUSE: typ.Final = re.compile(r"^github-actions updates do not cover (\S+);")


def _classify(message: str) -> tuple[str, ...]:
    """Name the clause and subject a finding message reports.

    Returns
    -------
    tuple[str, ...]
        The clause key the expectations are written in.
    """
    for pattern, clause in _ENTRY_CLAUSES:
        if matched := pattern.match(message):
            return (matched[1], clause)
    for pattern, clause in _NAMED_CLAUSES:
        if matched := pattern.match(message):
            return (matched[1], clause, matched[2])
    if matched := _ACTIONS_CLAUSE.match(message):
        return ("actions", matched[1])
    return ("unclassified", message)


def _findings(case: RepositoryCase) -> set[tuple[str, ...]]:
    """Audit the rendered case end to end and classify each finding.

    Returns
    -------
    set[tuple[str, ...]]
        The clause keys the policy reported.
    """
    with tempfile.TemporaryDirectory() as scratch:
        root = pathlib.Path(scratch)
        _write_checkout(root, case)
        result = runner.run_rule(_RULE_ID, root)
    return {_classify(finding.message) for finding in result.findings}


_COMPLIANT: typ.Final = RepositoryCase(
    entries=(EntryCase("cargo", "daily", ("prefix",), "exact", "lockfile-only"),),
    actions=_ACTIONS,
    listing="root_and_globstar",
)


def test_the_compliant_corner_has_no_findings() -> None:
    """Anchor the generator: its compliant corner really is compliant."""
    assert _expected(_COMPLIANT) == set()
    assert _findings(_COMPLIANT) == set()


def test_every_clause_can_fire_together() -> None:
    """Anchor the other corner: each clause fires, and only where decided."""
    case = RepositoryCase(
        entries=(
            EntryCase("cargo", "weekly", ("bare_wildcard",), "with_major", "increase"),
            EntryCase("uv", None, (), None, None),
        ),
        actions=_ACTIONS,
        listing="star_only",
    )
    findings = _findings(case)

    assert findings == _expected(case), findings
    assert len(findings) == 8, findings


@settings(max_examples=60, deadline=None)
@example(case=_COMPLIANT)
@given(case=_CASES)
def test_policy_findings_match_the_generated_configuration(
    case: RepositoryCase,
) -> None:
    """Every generated configuration yields exactly the findings it implies."""
    assert _findings(case) == _expected(case), case
