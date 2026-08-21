"""Operation Parabellum estate sweep driver.

Clones each Rust repository named in ``docs/parabellum/estate.yaml`` at its
current default-branch head, audits it with the ``rust-makefile-baseline``
rule package, and appends one JSON record per repository to the append-only
campaign ledger ``docs/parabellum/ledger.jsonl``. A repository already
ledgered at the same commit is skipped unless ``--force`` is given, so an
interrupted sweep is resumed by re-running the same command.

Audit-only: the sweep never writes to any estate repository.

Manifest decoding, the ledger schema, and report rendering live in sibling
modules; this one keeps the git boundary, the sweep loop, and the CLI. The
git functions stay here deliberately: `_SweepSession` calls them by bare
name, so the tests' `monkeypatch.setattr(sweep, "resolve_head", ...)` seam
only works while definition and call site share a module.

Usage from the repository root::

    uv run python -m scripts.parabellum_sweep [--only a,b] [--limit N] [--force]
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
import tempfile
import typing as typ

from cyclopts import App, Parameter

from concordat.errors import OperationalRuleError
from concordat.rules import RuleRunResult, run_rule

# Re-exported with the redundant-alias form: the test suite reaches these
# through `parabellum_sweep.<name>`, so they must stay module attributes
# even where this module does not call them itself.
from scripts.parabellum_ledger import (
    LEDGER_SCHEMA_VERSION as LEDGER_SCHEMA_VERSION,
)
from scripts.parabellum_ledger import (
    MAKEUTIL_REV as MAKEUTIL_REV,
)
from scripts.parabellum_ledger import (
    RULE_PACKAGE as RULE_PACKAGE,
)
from scripts.parabellum_ledger import (
    RULE_VERSION as RULE_VERSION,
)
from scripts.parabellum_ledger import (
    FindingRecord as FindingRecord,
)
from scripts.parabellum_ledger import (
    Ledger as Ledger,
)
from scripts.parabellum_ledger import (
    LedgerOptionalFields as LedgerOptionalFields,
)
from scripts.parabellum_ledger import (
    LedgerRecord as LedgerRecord,
)
from scripts.parabellum_ledger import (
    LedgerRequiredFields as LedgerRequiredFields,
)
from scripts.parabellum_ledger import (
    _already_ledgered as _already_ledgered,
)
from scripts.parabellum_ledger import (
    _append_record as _append_record,
)
from scripts.parabellum_ledger import (
    _base_record as _base_record,
)
from scripts.parabellum_ledger import (
    _excluded_record as _excluded_record,
)
from scripts.parabellum_ledger import (
    _finding_record as _finding_record,
)
from scripts.parabellum_ledger import (
    _ledger_record as _ledger_record,
)
from scripts.parabellum_ledger import (
    _load_ledger as _load_ledger,
)
from scripts.parabellum_ledger import (
    _timestamp as _timestamp,
)
from scripts.parabellum_manifest import (
    _OWNER_PATTERN as _OWNER_PATTERN,
)
from scripts.parabellum_manifest import (
    _REPO_NAME_PATTERN as _REPO_NAME_PATTERN,
)
from scripts.parabellum_manifest import (
    Estate as Estate,
)
from scripts.parabellum_manifest import (
    EstateEntry as EstateEntry,
)
from scripts.parabellum_manifest import (
    _manifest_error as _manifest_error,
)
from scripts.parabellum_manifest import (
    _repository_entries as _repository_entries,
)
from scripts.parabellum_manifest import (
    _repository_entry as _repository_entry,
)
from scripts.parabellum_manifest import (
    _validated_identifier as _validated_identifier,
)
from scripts.parabellum_manifest import (
    load_estate as load_estate,
)
from scripts.parabellum_paths import (
    DEFAULT_ESTATE_PATH as DEFAULT_ESTATE_PATH,
)
from scripts.parabellum_paths import (
    DEFAULT_LEDGER_PATH as DEFAULT_LEDGER_PATH,
)
from scripts.parabellum_paths import (
    DEFAULT_REPORT_PATH as DEFAULT_REPORT_PATH,
)
from scripts.parabellum_paths import (
    REPO_ROOT as REPO_ROOT,
)
from scripts.parabellum_report import (
    VERDICT_ORDER as VERDICT_ORDER,
)
from scripts.parabellum_report import (
    _cell as _cell,
)
from scripts.parabellum_report import (
    _finding_summary as _finding_summary,
)
from scripts.parabellum_report import (
    _findings_summary as _findings_summary,
)
from scripts.parabellum_report import (
    _latest_records as _latest_records,
)
from scripts.parabellum_report import (
    render_report as render_report,
)

__all__ = [
    "Estate",
    "EstateEntry",
    "OperationalRuleError",
    "RuleRunResult",
    "SweepOptions",
    "clone_and_audit",
    "load_estate",
    "main",
    "render_report",
    "report_command",
    "resolve_head",
    "run_sweep",
]

GIT_TIMEOUT: typ.Final = 300.0

app = App(name="parabellum-sweep")


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


@dataclasses.dataclass(slots=True)
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
    # Repositories this run has already handled, whatever the outcome. The
    # ledger checks answer "was this done before?"; `--force` deliberately
    # overrides them, which would otherwise let a name repeated in one
    # manifest be resolved, cloned, and appended twice in a single run.
    processed: set[str] = dataclasses.field(default_factory=set)

    def process(self, entry: EstateEntry) -> bool:
        """Handle one estate entry, returning whether the sweep should stop."""
        if self.only is not None and entry.name not in self.only:
            return False
        if entry.name in self.processed:
            return False
        self.processed.add(entry.name)
        if entry.excluded is not None:
            self._record_exclusion(entry)
            return False
        return self._process_auditable(entry)

    def _persist(self, record: LedgerRecord) -> None:
        """Append *record* to the ledger file and to both in-memory views."""
        # `_already_ledgered` consults `self.ledger`, which is loaded once
        # before the run, so a record written during the run has to enter it
        # too or the ledger checks cannot see this run's own work.
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
        """Process one auditable entry and report whether it consumed an audit slot."""
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

    Parameters
    ----------
    estate_path : pathlib.Path
        Path to the estate manifest.
    ledger_path : pathlib.Path
        Path to the append-only campaign ledger.
    options : SweepOptions
        Filters and execution options for the sweep.

    Returns
    -------
    Ledger
        Records appended during this invocation.
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

    Parameters
    ----------
    options : SweepCommandOptions
        Parsed command-line options controlling the sweep.

    Returns
    -------
    int
        Zero after the sweep completes successfully.
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
