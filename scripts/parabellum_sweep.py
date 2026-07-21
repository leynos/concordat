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
import subprocess
import tempfile
import typing as typ

from cyclopts import App
from ruamel.yaml import YAML

from concordat.errors import OperationalRuleError
from concordat.rules import RuleRunResult, run_rule

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


@dataclasses.dataclass(frozen=True, slots=True)
class SweepOptions:
    """Filtering and execution controls for an estate sweep."""

    only: frozenset[str] | None = None
    limit: int | None = None
    force: bool = False


# A frozen singleton so `run_sweep`'s default is a real, shareable value
# rather than a per-call construction in the signature (ruff B008).
_DEFAULT_SWEEP_OPTIONS: typ.Final = SweepOptions()


def load_estate(path: pathlib.Path) -> Estate:
    """Parse the estate inventory YAML document."""
    document = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    try:
        entries = tuple(
            EstateEntry(name=item["name"], excluded=item.get("excluded"))
            for item in document["repositories"]
        )
        return Estate(owner=document["owner"], repositories=entries)
    except KeyError as error:
        message = f"estate manifest {path} is missing key {error.args[0]!r}"
        raise OperationalRuleError(message) from error


def _load_ledger(path: pathlib.Path) -> list[dict[str, typ.Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _git(args: list[str], *, cwd: pathlib.Path | None = None) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],  # noqa: S607 - resolved from PATH
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        message = f"git {args[0]} failed: {detail}"
        raise OperationalRuleError(message)
    return completed.stdout


def resolve_head(owner: str, name: str) -> str:
    """Return the default-branch head SHA without cloning."""
    output = _git(["ls-remote", f"https://github.com/{owner}/{name}", "HEAD"])
    try:
        return output.split()[0]
    except IndexError as error:
        message = f"could not resolve HEAD for {owner}/{name}"
        raise OperationalRuleError(message) from error


def clone_and_audit(owner: str, name: str) -> tuple[str, RuleRunResult]:
    """Shallow-clone the repository, audit it, and return (sha, result)."""
    with tempfile.TemporaryDirectory(prefix="parabellum-") as scratch:
        checkout = pathlib.Path(scratch) / name
        _git(
            [
                "clone",
                "--depth",
                "1",
                "--quiet",
                f"https://github.com/{owner}/{name}",
                str(checkout),
            ]
        )
        sha = _git(["rev-parse", "HEAD"], cwd=checkout).strip()
        result = run_rule(RULE_PACKAGE, checkout)
        return sha, result


def _timestamp() -> str:
    return (
        dt.datetime.now(dt.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _base_record(repository: str) -> dict[str, typ.Any]:
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


def _excluded_record(repository: str, reason: str) -> dict[str, typ.Any]:
    record = _base_record(repository)
    record["verdict"] = "excluded"
    record["exclusion_reason"] = reason
    return record


def _append_record(
    ledger_path: pathlib.Path,
    appended: list[dict[str, typ.Any]],
    record: dict[str, typ.Any],
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


def _record_exclusion(
    *,
    ledger: list[dict[str, typ.Any]],
    ledger_path: pathlib.Path,
    appended: list[dict[str, typ.Any]],
    repository: str,
    reason: str,
) -> None:
    """Append an exclusion record unless the repository already has one."""
    if _already_ledgered(ledger, repository, commit_sha=None):
        return
    _append_record(ledger_path, appended, _excluded_record(repository, reason))


def _audit_record(owner: str, entry: EstateEntry) -> dict[str, typ.Any]:
    repository = f"{owner}/{entry.name}"
    record = _base_record(repository)
    try:
        sha, result = clone_and_audit(owner, entry.name)
    except OperationalRuleError as error:
        record["error_detail"] = str(error)
        return record
    record["commit_sha"] = sha
    record["verdict"] = result.verdict
    record["findings"] = [dataclasses.asdict(finding) for finding in result.findings]
    return record


def _already_ledgered(
    ledger: list[dict[str, typ.Any]],
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


def _sweep_auditable_entry(
    *,
    owner: str,
    entry: EstateEntry,
    ledger: list[dict[str, typ.Any]],
    ledger_path: pathlib.Path,
    appended: list[dict[str, typ.Any]],
    force: bool,
) -> bool:
    """Audit one non-excluded entry, returning whether it consumed a slot.

    A head-resolution failure and a completed audit both consume an audit
    slot; an idempotent skip of an already-ledgered commit does not.
    """
    repository = f"{owner}/{entry.name}"
    try:
        head = resolve_head(owner, entry.name)
    except OperationalRuleError as error:
        record = _base_record(repository)
        record["error_detail"] = str(error)
        _append_record(ledger_path, appended, record)
        return True

    if not force and _already_ledgered(ledger, repository, commit_sha=head):
        print(f"{repository}: already ledgered at {head[:12]}, skipping")
        return False

    record = _audit_record(owner, entry)
    _append_record(ledger_path, appended, record)
    print(f"{repository}: {record['verdict']}")
    return True


def run_sweep(
    *,
    estate_path: pathlib.Path = DEFAULT_ESTATE_PATH,
    ledger_path: pathlib.Path = DEFAULT_LEDGER_PATH,
    options: SweepOptions = _DEFAULT_SWEEP_OPTIONS,
) -> list[dict[str, typ.Any]]:
    """Sweep the estate and append new records to the ledger.

    Returns the records appended by this invocation.
    """
    estate = load_estate(estate_path)
    ledger = _load_ledger(ledger_path)
    appended: list[dict[str, typ.Any]] = []
    audited = 0

    for entry in estate.repositories:
        if options.only is not None and entry.name not in options.only:
            continue
        repository = f"{estate.owner}/{entry.name}"

        if entry.excluded is not None:
            _record_exclusion(
                ledger=ledger,
                ledger_path=ledger_path,
                appended=appended,
                repository=repository,
                reason=entry.excluded,
            )
            continue

        if options.limit is not None and audited >= options.limit:
            break

        audited += _sweep_auditable_entry(
            owner=estate.owner,
            entry=entry,
            ledger=ledger,
            ledger_path=ledger_path,
            appended=appended,
            force=options.force,
        )

    return appended


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
    ledger: list[dict[str, typ.Any]],
) -> dict[str, dict[str, typ.Any]]:
    latest: dict[str, dict[str, typ.Any]] = {}
    for record in ledger:
        latest[record["repository"]] = record
    return latest


def _finding_summary(record: dict[str, typ.Any]) -> str:
    if record["verdict"] == "excluded":
        return record.get("exclusion_reason", "")
    if record["verdict"] == "error":
        return record.get("error_detail", "")
    parts = [
        f"{finding['rule_id']} ({finding['verdict']}) {finding['message']}"
        for finding in record["findings"]
    ]
    # A visible placeholder keeps every table row at the full column count;
    # Markdown formatters collapse empty trailing cells, which then trips
    # MD056 (table-column-count).
    return "; ".join(parts) or "none"


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
def sweep_command(
    *,
    only: str | None = None,
    limit: int | None = None,
    force: bool = False,
    estate: pathlib.Path = DEFAULT_ESTATE_PATH,
    ledger: pathlib.Path = DEFAULT_LEDGER_PATH,
) -> int:
    """Audit the Rust estate and append results to the campaign ledger.

    ``--only`` takes a comma-separated list of repository names;
    ``--limit`` bounds how many repositories are audited this run.
    """
    only_set = (
        {name.strip() for name in only.split(",") if name.strip()} if only else None
    )
    options = SweepOptions(
        only=frozenset(only_set) if only_set else None,
        limit=limit,
        force=force,
    )
    appended = run_sweep(
        estate_path=estate,
        ledger_path=ledger,
        options=options,
    )
    print(f"appended {len(appended)} record(s) to {ledger}")
    return 0


def main() -> None:  # pragma: no cover - exercised via CLI
    """Entry point for `python -m scripts.parabellum_sweep`."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
