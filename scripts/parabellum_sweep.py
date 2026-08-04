"""Operation Parabellum estate sweep driver.

Clones each Rust repository named in ``docs/parabellum/estate.yaml`` at its
current default-branch head, audits it with the ``rust-makefile-baseline``
rule package, and appends one JSON record per repository to the append-only
campaign ledger ``docs/parabellum/ledger.jsonl``. A repository already
ledgered at the same commit is skipped unless ``--force`` is given, so an
interrupted sweep is resumed by re-running the same command.

Audit-only: the sweep never writes to any estate repository.

Usage from the repository root::

    uv run python -m scripts.parabellum_sweep [--only a,b] [--limit N] [--force]
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
import re
import subprocess
import tempfile
import typing as typ

from cyclopts import App, Parameter
from ruamel.yaml import YAML

from concordat.errors import OperationalRuleError
from concordat.rules import Finding, RuleRunResult, run_rule

__all__ = [
    "Estate",
    "EstateEntry",
    "OperationalRuleError",
    "RuleRunResult",
    "SweepOptions",
    "clone_and_audit",
    "load_estate",
    "main",
    "resolve_head",
    "run_sweep",
]

REPO_ROOT: typ.Final = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_ESTATE_PATH: typ.Final = REPO_ROOT / "docs" / "parabellum" / "estate.yaml"
DEFAULT_LEDGER_PATH: typ.Final = REPO_ROOT / "docs" / "parabellum" / "ledger.jsonl"
RULE_PACKAGE: typ.Final = "rust-makefile-baseline"
RULE_VERSION: typ.Final = "0.2.0"
MAKEUTIL_REV: typ.Final = "29fc5a1634ffbaa18a773eed9dff1b2838a45d9c"
LEDGER_SCHEMA_VERSION: typ.Final = 1
GIT_TIMEOUT: typ.Final = 300.0

app = App(name="parabellum-sweep")


@dataclasses.dataclass(frozen=True, slots=True)
class EstateEntry:
    """One repository in the campaign inventory."""

    name: str
    excluded: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Estate:
    """The parsed campaign inventory."""

    owner: str
    repositories: tuple[EstateEntry, ...]


class FindingRecord(typ.TypedDict):
    """One serialized :class:`concordat.rules.Finding` as stored in the ledger."""

    rule_id: str
    severity: str
    verdict: str
    path: str
    line: int
    message: str


class LedgerRequiredFields(typ.TypedDict):
    """The keys every ledger record carries."""

    schema_version: int
    repository: str
    commit_sha: str | None
    audited_at: str
    rule_package: str
    rule_version: str
    makeutil_rev: str
    verdict: str
    findings: list[FindingRecord]


class LedgerOptionalFields(typ.TypedDict, total=False):
    """Keys present only on excluded and errored records."""

    exclusion_reason: str
    error_detail: str


class LedgerRecord(LedgerRequiredFields, LedgerOptionalFields):
    """One append-only ledger line."""


type Ledger = list[LedgerRecord]

# Derived from the TypedDict so a new required key cannot be forgotten here.
_LEDGER_REQUIRED_KEYS: typ.Final = frozenset(LedgerRequiredFields.__annotations__)


@dataclasses.dataclass(frozen=True, slots=True)
class SweepOptions:
    """Filtering and execution controls for an estate sweep."""

    only: frozenset[str] | None = None
    limit: int | None = None
    force: bool = False


# A frozen singleton so `run_sweep`'s default is a real, shareable value
# rather than a per-call construction in the signature (ruff B008).
_DEFAULT_SWEEP_OPTIONS: typ.Final = SweepOptions()


# `name="*"` flattens the fields onto the command, so the CLI keeps its
# top-level `--only`/`--limit`/`--force`/`--estate`/`--ledger` flags rather
# than gaining a nested `--options.` prefix.
# `kw_only` keeps the flags keyword-only, as the previous `*`-marked signature
# made them: without it Cyclopts would also accept them positionally.
@Parameter(name="*")
@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SweepCommandOptions:
    """CLI inputs for one Parabellum estate sweep."""

    only: str | None = None
    limit: int | None = None
    force: bool = False
    estate: pathlib.Path = DEFAULT_ESTATE_PATH
    ledger: pathlib.Path = DEFAULT_LEDGER_PATH


# Every field is optional, so Cyclopts requires the parameter itself to carry a
# default. A frozen singleton supplies one without calling the constructor in
# the signature (ruff B008), matching `_DEFAULT_SWEEP_OPTIONS` above.
_DEFAULT_COMMAND_OPTIONS: typ.Final = SweepCommandOptions()


# GitHub owners are alphanumerics and hyphens, no leading or trailing hyphen.
# Repository names additionally allow dot and underscore, but must remain a
# single path component that is safe to use as a clone directory: `.` and `..`
# are excluded by requiring at least one character that is not a dot.
_OWNER_PATTERN: typ.Final = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"
)
_REPO_NAME_PATTERN: typ.Final = re.compile(r"^(?=.*[^.])[A-Za-z0-9._-]{1,100}$")


def _validated_identifier(
    value: object,
    pattern: re.Pattern[str],
    kind: str,
    resource: pathlib.Path | str,
) -> str:
    """Return *value* when it matches *pattern*, else reject the manifest.

    Names from the manifest become URL segments and clone-directory
    components, so they are validated here, at the boundary, rather than
    trusted downstream.
    """
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    message = f"estate manifest declares an invalid {kind}: {value!r}"
    raise OperationalRuleError(
        message,
        operation="load-estate-manifest",
        resource=resource,
    )


def _manifest_error(path: pathlib.Path, detail: str) -> OperationalRuleError:
    """Return the rejection for a manifest whose shape cannot be decoded."""
    message = f"estate manifest {path} {detail}"
    return OperationalRuleError(
        message,
        operation="load-estate-manifest",
        resource=path,
    )


def _repository_entry(item: object, path: pathlib.Path) -> EstateEntry:
    """Decode one repository entry from an estate manifest."""
    if not isinstance(item, dict):
        raise _manifest_error(path, f"has a non-mapping repository entry: {item!r}")
    entry = typ.cast("dict[str, object]", item)
    if "name" not in entry:
        raise _manifest_error(path, "has a repository entry missing key 'name'")
    excluded = entry.get("excluded")
    # An exclusion reason is prose the ledger records verbatim. Dropping a
    # non-string would silently un-exclude the repository and audit it, so
    # the manifest is refused instead.
    if excluded is not None and not isinstance(excluded, str):
        raise _manifest_error(
            path,
            f"has a non-string exclusion reason: {excluded!r}",
        )
    return EstateEntry(
        name=_validated_identifier(
            entry["name"], _REPO_NAME_PATTERN, "repository name", path
        ),
        excluded=excluded,
    )


def _repository_entries(
    repositories: object,
    path: pathlib.Path,
) -> tuple[EstateEntry, ...]:
    """Decode the repository collection from an estate manifest.

    Only a list is a repository collection. A scalar raises on iteration and a
    bare string is walked character by character, so both are refused here
    rather than reaching the entry decoding as `TypeError`.
    """
    if not isinstance(repositories, list):
        raise _manifest_error(
            path,
            f"has a `repositories` value that is not a list: {repositories!r}",
        )
    return tuple(_repository_entry(item, path) for item in repositories)


def load_estate(path: pathlib.Path) -> Estate:
    """Parse the estate inventory YAML document.

    Every shape the document can take is checked before it is indexed. The
    file is operator-supplied, so a malformed one must surface as an
    operational error naming the manifest, not as a `TypeError` from a
    subscript.
    """
    document = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise _manifest_error(
            path,
            f"is not a mapping: {type(document).__name__}",
        )
    for key in ("repositories", "owner"):
        if key not in document:
            raise _manifest_error(path, f"is missing key {key!r}")
    entries = _repository_entries(document["repositories"], path)
    owner = _validated_identifier(document["owner"], _OWNER_PATTERN, "owner", path)
    return Estate(owner=owner, repositories=entries)


def _ledger_record(
    decoded: object, path: pathlib.Path, line_number: int
) -> LedgerRecord:
    """Return *decoded* as a ledger record, or reject the malformed line.

    The ledger is append-only and read back on every sweep, so a truncated or
    hand-edited line has to surface as an operational error rather than be
    trusted into the typed flow by a cast.
    """
    if not isinstance(decoded, dict):
        message = f"ledger {path} line {line_number} is not a JSON object"
        raise OperationalRuleError(
            message,
            operation="load-ledger",
            resource=path,
        )
    missing = sorted(_LEDGER_REQUIRED_KEYS - decoded.keys())
    if missing:
        message = f"ledger {path} line {line_number} is missing {missing}"
        raise OperationalRuleError(
            message,
            operation="load-ledger",
            resource=path,
        )
    return typ.cast("LedgerRecord", decoded)


def _load_ledger(path: pathlib.Path) -> Ledger:
    if not path.exists():
        return []
    return [
        _ledger_record(json.loads(line), path, number)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip()
    ]


def _git(
    args: list[str],
    *,
    cwd: pathlib.Path | None = None,
    operation: str,
    resource: str,
) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],  # noqa: S607 - resolved from PATH
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as error:
        message = "git is required but was not found on PATH"
        raise OperationalRuleError(
            message,
            operation=operation,
            tool="git",
            resource=resource,
        ) from error
    except subprocess.TimeoutExpired as error:
        message = f"git {args[0]} timed out after {GIT_TIMEOUT}s"
        raise OperationalRuleError(
            message,
            operation=operation,
            tool="git",
            resource=resource,
        ) from error
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        message = f"git {args[0]} failed: {detail}"
        raise OperationalRuleError(
            message,
            operation=operation,
            tool="git",
            resource=resource,
        )
    return completed.stdout


def resolve_head(owner: str, name: str) -> str:
    """Return the default-branch head SHA without cloning."""
    repository = f"{owner}/{name}"
    output = _git(
        ["ls-remote", f"https://github.com/{owner}/{name}", "HEAD"],
        operation="resolve-git-head",
        resource=repository,
    )
    try:
        return output.split()[0]
    except IndexError as error:
        message = f"could not resolve HEAD for {repository}"
        raise OperationalRuleError(
            message,
            operation="resolve-git-head",
            tool="git",
            resource=repository,
        ) from error


def clone_and_audit(owner: str, name: str) -> tuple[str, RuleRunResult]:
    """Shallow-clone the repository, audit it, and return (sha, result)."""
    repository = f"{owner}/{name}"
    _validated_identifier(owner, _OWNER_PATTERN, "owner", repository)
    _validated_identifier(name, _REPO_NAME_PATTERN, "repository name", repository)
    with tempfile.TemporaryDirectory(prefix="parabellum-") as scratch:
        root = pathlib.Path(scratch).resolve()
        checkout = (root / name).resolve()
        # `load_estate` already rejects separators and dot segments, so this
        # cannot trigger today; it is here so a caller reaching
        # `clone_and_audit` directly cannot write outside the scratch root.
        if not checkout.is_relative_to(root):
            message = f"clone destination for {repository} escapes {root}"
            raise OperationalRuleError(
                message,
                operation="clone-repository",
                resource=repository,
            )
        _git(
            [
                "clone",
                "--depth",
                "1",
                "--quiet",
                f"https://github.com/{owner}/{name}",
                str(checkout),
            ],
            operation="clone-repository",
            resource=repository,
        )
        sha = _git(
            ["rev-parse", "HEAD"],
            cwd=checkout,
            operation="clone-repository",
            resource=repository,
        ).strip()
        result = run_rule(RULE_PACKAGE, checkout)
        return sha, result


def _timestamp() -> str:
    return (
        dt.datetime.now(dt.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _base_record(repository: str) -> LedgerRecord:
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "repository": repository,
        "commit_sha": None,
        "audited_at": _timestamp(),
        "rule_package": RULE_PACKAGE,
        "rule_version": RULE_VERSION,
        "makeutil_rev": MAKEUTIL_REV,
        "verdict": "error",
        "findings": [],
    }


def _excluded_record(repository: str, reason: str) -> LedgerRecord:
    record = _base_record(repository)
    record["verdict"] = "excluded"
    record["exclusion_reason"] = reason
    return record


def _append_record(
    ledger_path: pathlib.Path,
    appended: Ledger,
    record: LedgerRecord,
) -> None:
    """Append one record to the ledger immediately.

    Auditing the estate takes many minutes and clones over the network, so
    each record is durable before the next repository is attempted; an
    interrupted sweep resumes rather than restarts.
    """
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    appended.append(record)


def _finding_record(finding: Finding) -> FindingRecord:
    """Serialize one finding, naming each field so the result stays typed.

    `dataclasses.asdict` returns `dict[str, Any]`, which would defeat the
    point of the record types.
    """
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity,
        "verdict": finding.verdict,
        "path": finding.path,
        "line": finding.line,
        "message": finding.message,
    }


def _audit_record(owner: str, entry: EstateEntry) -> LedgerRecord:
    repository = f"{owner}/{entry.name}"
    record = _base_record(repository)
    try:
        sha, result = clone_and_audit(owner, entry.name)
    except OperationalRuleError as error:
        record["error_detail"] = str(error)
        return record
    record["commit_sha"] = sha
    record["verdict"] = result.verdict
    record["findings"] = [_finding_record(finding) for finding in result.findings]
    return record


def _already_ledgered(
    ledger: Ledger,
    repository: str,
    *,
    commit_sha: str | None,
) -> bool:
    if commit_sha is None:
        return any(
            record["repository"] == repository and record["verdict"] == "excluded"
            for record in ledger
        )
    return any(
        record["repository"] == repository and record["commit_sha"] == commit_sha
        for record in ledger
    )


@dataclasses.dataclass
class _SweepSession:
    """Mutable per-invocation state for one estate sweep.

    Holding the loop state on an object keeps ``run_sweep`` a flat sequence of
    calls: ``process`` decides each entry's fate and reports back only whether
    the sweep should stop, so the branching no longer piles up in the loop.
    """

    owner: str
    ledger: Ledger
    ledger_path: pathlib.Path
    only: frozenset[str] | None
    limit: int | None
    force: bool
    appended: Ledger = dataclasses.field(default_factory=list)
    audited: int = 0

    def process(self, entry: EstateEntry) -> bool:
        """Handle one estate entry, returning whether the sweep should stop."""
        if self.only is not None and entry.name not in self.only:
            return False
        if entry.excluded is not None:
            self._record_exclusion(entry)
            return False
        return self._process_auditable(entry)

    def _persist(self, record: LedgerRecord) -> None:
        """Append *record* to the ledger file and to both in-memory views.

        `_already_ledgered` consults `self.ledger`, which is loaded once
        before the run. Without adding the record there too, a repository
        named twice in one manifest would be processed twice: the first pass
        never enters the view the duplicate check reads.
        """
        _append_record(self.ledger_path, self.appended, record)
        self.ledger.append(record)

    def _record_exclusion(self, entry: EstateEntry) -> None:
        """Append an exclusion record unless the repository already has one.

        ``process`` only routes excluded entries here, but that guard's
        narrowing of ``entry.excluded`` does not cross the method boundary, so
        the reason is re-checked rather than asserted.
        """
        if entry.excluded is None:
            return
        repository = f"{self.owner}/{entry.name}"
        if _already_ledgered(self.ledger, repository, commit_sha=None):
            return
        self._persist(_excluded_record(repository, entry.excluded))

    def _process_auditable(self, entry: EstateEntry) -> bool:
        """Audit a non-excluded entry; stop once the audit budget is spent."""
        if self.limit is not None and self.audited >= self.limit:
            return True
        self.audited += self._sweep_auditable_entry(entry)
        return False

    def _sweep_auditable_entry(self, entry: EstateEntry) -> bool:
        """Audit one non-excluded entry and report audit-slot consumption.

        A head-resolution failure and a completed audit both consume an audit
        slot; an idempotent skip of an already-ledgered commit does not.
        """
        repository = f"{self.owner}/{entry.name}"
        try:
            head = resolve_head(self.owner, entry.name)
        except OperationalRuleError as error:
            record = _base_record(repository)
            record["error_detail"] = str(error)
            self._persist(record)
            return True

        if not self.force and _already_ledgered(
            self.ledger, repository, commit_sha=head
        ):
            print(f"{repository}: already ledgered at {head[:12]}, skipping")
            return False

        record = _audit_record(self.owner, entry)
        self._persist(record)
        print(f"{repository}: {record['verdict']}")
        return True


def run_sweep(
    *,
    estate_path: pathlib.Path = DEFAULT_ESTATE_PATH,
    ledger_path: pathlib.Path = DEFAULT_LEDGER_PATH,
    options: SweepOptions = _DEFAULT_SWEEP_OPTIONS,
) -> Ledger:
    """Sweep the estate and append new records to the ledger.

    Returns the records appended by this invocation.
    """
    estate = load_estate(estate_path)
    session = _SweepSession(
        owner=estate.owner,
        ledger=_load_ledger(ledger_path),
        ledger_path=ledger_path,
        only=options.only,
        limit=options.limit,
        force=options.force,
    )

    for entry in estate.repositories:
        if session.process(entry):
            break

    return session.appended


DEFAULT_REPORT_PATH: typ.Final = (
    REPO_ROOT / "docs" / "parabellum" / "baseline-report.md"
)

VERDICT_ORDER: typ.Final = (
    "noncompliant",
    "indeterminate",
    "error",
    "compliant",
    "excluded",
)


def _latest_records(
    ledger: Ledger,
) -> dict[str, LedgerRecord]:
    latest: dict[str, LedgerRecord] = {}
    for record in ledger:
        latest[record["repository"]] = record
    return latest


# A table cell ends at an unescaped `|`, and a newline ends the row. Ledger
# prose — an exclusion reason from the manifest, an error detail from a tool,
# a finding message from the policy — is free text that may contain either.
_CELL_ESCAPES: typ.Final = str.maketrans({"\\": "\\\\", "|": "\\|"})


def _cell(value: str) -> str:
    """Return *value* rendered safely inside a Markdown table cell."""
    # `split()` with no argument folds every run of whitespace, newlines
    # included, so a multi-line detail becomes one line rather than breaking
    # the table. Backslash is escaped in the same pass as the pipe, so an
    # already-escaped pipe cannot be produced by escaping twice.
    return " ".join(value.split()).translate(_CELL_ESCAPES)


def _findings_summary(findings: list[FindingRecord]) -> str:
    """Render rule findings as one Markdown table-cell value."""
    parts = [
        f"{_cell(finding['rule_id'])} ({_cell(finding['verdict'])}) "
        f"{_cell(finding['message'])}"
        for finding in findings
    ]
    return "; ".join(parts) or "none"


def _finding_summary(record: LedgerRecord) -> str:
    """Return the final Markdown table-cell value for one ledger record."""
    # Every branch falls back to the same placeholder, `_findings_summary`
    # included. A blank final cell is ambiguous to a reader — it does not
    # distinguish "nothing to report" from "the detail is missing" — so each
    # verdict says which it is.
    if record["verdict"] == "excluded":
        return _cell(record.get("exclusion_reason") or "") or "none"
    if record["verdict"] == "error":
        return _cell(record.get("error_detail") or "") or "none"
    return _findings_summary(record["findings"])


def render_report(ledger_path: pathlib.Path = DEFAULT_LEDGER_PATH) -> str:
    """Render the campaign baseline report from the ledger."""
    latest = _latest_records(_load_ledger(ledger_path))
    counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    for record in latest.values():
        counts[record["verdict"]] = counts.get(record["verdict"], 0) + 1
        for finding in record["findings"]:
            rule_id = finding["rule_id"]
            rule_counts[rule_id] = rule_counts.get(rule_id, 0) + 1

    lines = [
        "# Operation Parabellum baseline report",
        "",
        "Generated from `docs/parabellum/ledger.jsonl` by",
        "`python -m scripts.parabellum_sweep report`. Do not edit by hand.",
        "",
        f"Rule package: `{RULE_PACKAGE}` v{RULE_VERSION}; "
        f"makeutil `{MAKEUTIL_REV[:12]}`.",
        "",
        "## Summary",
        "",
    ]
    lines.extend(
        f"- {verdict}: {counts[verdict]}"
        for verdict in VERDICT_ORDER
        if verdict in counts
    )
    lines.extend(["", "Findings by rule:", ""])
    lines.extend(
        f"- {rule_id}: {rule_counts[rule_id]}" for rule_id in sorted(rule_counts)
    )
    lines.extend(
        [
            "",
            "## Repositories",
            "",
            "Table 1: Latest verdict and findings per estate repository.",
            "",
            "| Repository | Verdict | Commit | Findings |",
            "| ---------- | ------- | ------ | -------- |",
        ]
    )
    for repository in sorted(latest):
        record = latest[repository]
        commit = (record["commit_sha"] or "")[:12]
        summary = _finding_summary(record)
        lines.append(f"| {repository} | {record['verdict']} | {commit} | {summary} |")
    lines.append("")
    return "\n".join(lines)


@app.command(name="report")
def report_command(
    *,
    ledger: pathlib.Path = DEFAULT_LEDGER_PATH,
    output: pathlib.Path = DEFAULT_REPORT_PATH,
) -> int:
    """Regenerate the baseline report from the campaign ledger."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(ledger), encoding="utf-8")
    print(f"wrote {output}")
    return 0


@app.default
def sweep_command(options: SweepCommandOptions = _DEFAULT_COMMAND_OPTIONS) -> int:
    """Audit the Rust estate and append results to the campaign ledger.

    ``--only`` takes a comma-separated list of repository names;
    ``--limit`` bounds how many repositories are audited this run.
    """
    only_set = (
        {name.strip() for name in options.only.split(",") if name.strip()}
        if options.only
        else None
    )
    sweep_options = SweepOptions(
        only=frozenset(only_set) if only_set else None,
        limit=options.limit,
        force=options.force,
    )
    appended = run_sweep(
        estate_path=options.estate,
        ledger_path=options.ledger,
        options=sweep_options,
    )
    print(f"appended {len(appended)} record(s) to {options.ledger}")
    return 0


def main() -> None:  # pragma: no cover - exercised via CLI
    """Entry point for `python -m scripts.parabellum_sweep`."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
