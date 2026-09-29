"""Command line entry points for the concordat tooling."""

from __future__ import annotations

import functools
import logging
import os
import pathlib
import subprocess
import sys
import typing as typ
import urllib.parse

from cyclopts import App, Parameter

from . import credentials, xdg
from .auditor.github import DEFAULT_API_URL, GithubClient
from .enrol import disenrol_repositories, enrol_repositories
from .errors import ConcordatError, OperationalRuleError
from .estate import (
    DEFAULT_BRANCH as ESTATE_DEFAULT_BRANCH,
)
from .estate import (
    DEFAULT_INVENTORY_PATH as ESTATE_DEFAULT_INVENTORY,
)
from .estate import (
    EstateNotConfiguredError,
    EstateRecord,
    get_active_estate,
    get_estate,
    init_estate,
    list_enrolled_repositories,
    list_estates,
    migrate_legacy_config,
    set_active_estate,
)
from .estate_execution import ExecutionIO, ExecutionOptions, run_apply, run_plan
from .listing import list_namespace_repositories
from .persistence import PersistenceOptions, persist_estate
from .platform_standards import PlatformStandardsConfig

_logger = logging.getLogger(__name__)

app = App()


estate_app = App()

artefact_app = App()

rule_app = App()

ERROR_NO_ACTIVE_ESTATE = (
    "No active estate configured. Run `concordat estate init --github-owner "
    "<owner>` followed by `concordat estate use <alias>` before enrolling "
    "repositories."
)
ERROR_ACTIVE_ESTATE_OWNER = (
    "Active estate {alias!r} is missing github_owner. Re-initialise the estate "
    "with --github-owner or update the config before enrolling repositories."
)
ERROR_NAMESPACE_REQUIRED = (
    "Specify one or more namespaces or activate an estate with "
    "`concordat estate use <alias>`."
)
ERROR_OWNER_LOOKUP_FAILED = (
    "Estate {alias!r} is missing github_owner; re-run "
    "`concordat estate init --github-owner <owner>` to record it."
)
ERROR_NO_ESTATES = "No estates configured. Run `concordat estate init` first."
ERROR_MISSING_GITHUB_TOKEN = (
    "GITHUB_TOKEN is required for concordat plan/apply; "  # noqa: S105  # Error text only; no secret value.
    "pass --github-token or export the environment variable."
)
ERROR_AUTO_APPROVE_REQUIRED = "concordat apply requires --auto-approve to continue."
ENV_SKIP_PLATFORM_PR = "CONCORDAT_SKIP_PLATFORM_PR"


def _github_token_fallback() -> str | None:
    """Resolve the GitHub token: environment first, then credentials file."""
    return credentials.github_token()


def _env_flag(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_platform_config(
    estate: EstateRecord | None,
    explicit_url: str | None,
    branch: str,
    inventory: str,
    token: str | None,
) -> PlatformStandardsConfig | None:
    """Return the platform-standards config if PR automation should run."""
    if _env_flag(ENV_SKIP_PLATFORM_PR):
        return None

    platform_url = explicit_url or os.getenv("CONCORDAT_PLATFORM_STANDARDS_URL")
    base_branch = branch
    inventory_path = inventory
    branch_is_default = not branch or branch == ESTATE_DEFAULT_BRANCH
    inventory_is_default = not inventory or inventory == ESTATE_DEFAULT_INVENTORY
    if not platform_url and estate is not None:
        platform_url = estate.repo_url
        if branch_is_default:
            base_branch = estate.branch
        if inventory_is_default:
            inventory_path = estate.inventory_path

    if not platform_url:
        return None

    return PlatformStandardsConfig(
        repo_url=platform_url,
        base_branch=base_branch,
        inventory_path=inventory_path,
        github_token=token,
    )


def _ensure_auto_approve_flag(args: tuple[str, ...]) -> tuple[str, ...]:
    """Ensure -auto-approve is the first argument when not already present."""
    filtered = tuple(arg for arg in args if arg)
    lowered = {arg.lower() for arg in filtered}
    if "-auto-approve" not in lowered and "-auto-approve=true" not in lowered:
        return ("-auto-approve", *filtered)
    return filtered


def _resolve_namespaces(namespaces: tuple[str, ...]) -> tuple[str, ...]:
    """Return namespaces or fall back to the active estate owner."""
    if namespaces:
        return namespaces
    if (estate := get_active_estate()) is None:
        raise ConcordatError(ERROR_NAMESPACE_REQUIRED)
    if owner := estate.github_owner:
        return (owner,)
    raise ConcordatError(ERROR_OWNER_LOOKUP_FAILED.format(alias=estate.alias))


@app.command()
def enrol(
    *repositories: str,
    push: bool = False,
    force: bool = False,
    author_name: str | None = None,
    author_email: str | None = None,
    platform_standards_url: str | None = None,
    platform_standards_branch: str = "main",
    platform_standards_inventory: str = "tofu/inventory/repositories.yaml",
    github_token: str | None = None,
) -> None:
    """Create the concordat enrolment document in each repository."""
    estate = _require_active_estate()
    token = github_token or _github_token_fallback()

    owner_guard = estate.github_owner
    if not owner_guard:
        raise ConcordatError(ERROR_ACTIVE_ESTATE_OWNER.format(alias=estate.alias))

    platform_config = _resolve_platform_config(
        estate=estate,
        explicit_url=platform_standards_url,
        branch=platform_standards_branch,
        inventory=platform_standards_inventory,
        token=token,
    )

    outcomes = enrol_repositories(
        repositories,
        push_remote=push,
        author_name=author_name,
        author_email=author_email,
        platform_standards=platform_config,
        github_owner=owner_guard,
        force=force,
    )
    for outcome in outcomes:
        print(outcome.render())


@app.command()
def ls(*namespaces: str, token: str | None = None) -> None:
    """List SSH URLs for GitHub repositories within the given namespaces."""
    resolved_token = token or _github_token_fallback()
    effective_namespaces = _resolve_namespaces(tuple(namespaces))

    urls = list_namespace_repositories(
        effective_namespaces,
        token=resolved_token,
    )
    for url in urls:
        print(url)


@app.command()
def disenrol(
    *repositories: str,
    push: bool = False,
    author_name: str | None = None,
    author_email: str | None = None,
    platform_standards_url: str | None = None,
    platform_standards_branch: str = "main",
    platform_standards_inventory: str = "tofu/inventory/repositories.yaml",
    github_token: str | None = None,
) -> None:
    """Mark repositories as no longer enrolled in concordat."""
    estate = _require_active_estate()
    token = github_token or _github_token_fallback()

    owner_guard = estate.github_owner
    if not owner_guard:
        raise ConcordatError(ERROR_ACTIVE_ESTATE_OWNER.format(alias=estate.alias))

    platform_config = _resolve_platform_config(
        estate=estate,
        explicit_url=platform_standards_url,
        branch=platform_standards_branch,
        inventory=platform_standards_inventory,
        token=token,
    )

    outcomes = disenrol_repositories(
        repositories,
        push_remote=push,
        author_name=author_name,
        author_email=author_email,
        platform_standards=platform_config,
        github_owner=owner_guard,
        allow_missing_document=True,
    )
    for outcome in outcomes:
        print(outcome.render())


@estate_app.command()
def init(
    alias: str,
    repo_url: str,
    *,
    github_token: str | None = None,
    branch: str = ESTATE_DEFAULT_BRANCH,
    inventory_path: str = ESTATE_DEFAULT_INVENTORY,
    github_owner: str | None = None,
    yes: bool = False,
) -> None:
    """Initialise a platform-standards estate repository."""
    token = github_token or _github_token_fallback()
    confirmer = (lambda _: True) if yes else None
    record = init_estate(
        alias,
        repo_url,
        branch=branch,
        inventory_path=inventory_path,
        github_owner=github_owner,
        github_token=token,
        confirm=confirmer,
    )
    print(f"initialised estate {record.alias}: {record.repo_url}")


@estate_app.command()
def use(alias: str) -> None:
    """Activate an estate so other commands can reference it."""
    record = set_active_estate(alias)
    print(f"active estate: {record.alias}")


@estate_app.command(name="ls")
def estate_ls() -> None:
    """List configured estate aliases."""
    records = list_estates()
    if not records:
        raise ConcordatError(ERROR_NO_ESTATES)
    for record in records:
        print(f"{record.alias}\t{record.repo_url}")


@estate_app.command()
def show(alias: str | None = None) -> None:
    """Show the repositories enrolled in an estate."""
    urls = list_enrolled_repositories(alias)
    for url in urls:
        print(url)


@estate_app.command()
def persist(
    alias: str | None = None,
    *,
    force: bool = False,
    github_token: str | None = None,
    allow_insecure_endpoint: bool = False,
    bucket: str | None = None,
    region: str | None = None,
    endpoint: str | None = None,
    key_prefix: str | None = None,
    key_suffix: str | None = None,
    no_input: bool = False,
) -> None:
    """Configure remote state persistence for an estate."""
    record = _resolve_estate_record(alias)
    token = github_token or _github_token_fallback()
    bucket_env = os.getenv("CONCORDAT_PERSIST_BUCKET")
    region_env = os.getenv("CONCORDAT_PERSIST_REGION")
    endpoint_env = os.getenv("CONCORDAT_PERSIST_ENDPOINT")
    key_prefix_env = os.getenv("CONCORDAT_PERSIST_KEY_PREFIX")
    key_suffix_env = os.getenv("CONCORDAT_PERSIST_KEY_SUFFIX")
    options = PersistenceOptions(
        force=force,
        github_token=token,
        allow_insecure_endpoint=allow_insecure_endpoint,
        bucket=bucket or bucket_env,
        region=region or region_env,
        endpoint=endpoint or endpoint_env,
        key_prefix=key_prefix or key_prefix_env,
        key_suffix=key_suffix or key_suffix_env,
        no_input=no_input,
    )
    result = persist_estate(record, options)
    print(result.render())


app.command(estate_app, name="estate")


@rule_app.command(name="run")
def rule_run(
    rule_id: str,
    *,
    repo: pathlib.Path = pathlib.Path(),
    output_format: typ.Annotated[
        typ.Literal["table", "json"],
        Parameter(name="--format"),
    ] = "table",
    github_api_url: typ.Annotated[
        str,
        Parameter(name="--github-api-url"),
    ] = DEFAULT_API_URL,
    pin_cache_dir: typ.Annotated[
        pathlib.Path | None,
        Parameter(name="--pin-cache-dir"),
    ] = None,
    no_pin_cache: typ.Annotated[
        bool,
        Parameter(name="--no-pin-cache"),
    ] = False,
) -> int:
    """Audit a local checkout against a canon lint rule package.

    Exit codes: 0 compliant; 1 at least one finding (including
    indeterminate verdicts, which fail closed); 2 operational failure.

    The Markdown package asks the GitHub API at *github_api_url* what each
    pinned action SHA names; the client is built only if a pin needs it.
    Definite answers are kept on disk, so the next run asks nothing about a
    pin it has seen: in *pin_cache_dir*, else `CONCORDAT_PIN_CACHE_DIR`, else
    the user cache directory. *no_pin_cache* neither reads nor writes it.

    Returns
    -------
    int
        Exit code for the audit result.

    """
    from .rules import render_json, render_table, run_rule
    from .rules.github_pins import GithubPinResolver
    from .rules.packages import default_envelope_builder, resolving_pins
    from .rules.pin_cache import PinCache, cached_resolver, default_directory

    github = GithubPinResolver(functools.partial(_pin_client, github_api_url))
    resolver = (
        github
        if no_pin_cache
        else cached_resolver(
            github, PinCache(pin_cache_dir or default_directory(os.environ))
        )
    )
    builder = resolving_pins(default_envelope_builder, resolver)
    result = run_rule(rule_id, repo, envelope_builder=builder)
    rendered = render_json(result) if output_format == "json" else render_table(result)
    print(rendered)
    return result.exit_code


def _pin_client(api_url: str) -> GithubClient:
    """Build the GitHub client pin resolution uses, with the configured token.

    The token comes from `GITHUB_TOKEN` or the concordat credentials file,
    and otherwise from `gh auth token`: unauthenticated, GitHub allows 60
    requests an hour, which one estate sweep spends in a few repositories.
    An unreadable or insecure credentials file means the audit cannot run as
    configured, so it is an operational failure rather than a finding.

    Returns
    -------
    GithubClient
        A client for *api_url*, authenticated when a token is configured.

    Raises
    ------
    OperationalRuleError
        If the GitHub credentials cannot be read.
    """
    try:
        token = credentials.github_token()
    except ConcordatError as error:
        message = f"cannot read the GitHub credentials: {error}"
        raise OperationalRuleError(
            message, operation="read-github-credentials"
        ) from error
    return GithubClient(
        token=token or _gh_cli_token(_github_host(api_url)), api_url=api_url
    )


def _github_host(api_url: str) -> str:
    """Return the GitHub host that issues tokens for the API root *api_url*.

    `api.github.com` belongs to `github.com`; any other root, such as a GitHub
    Enterprise Server's `https://ghe.example.com/api/v3`, is its own host.

    Returns
    -------
    str
        The host name `gh auth token --hostname` expects.
    """
    host = urllib.parse.urlsplit(api_url).hostname or ""
    return "github.com" if host == "api.github.com" else host


def _gh_cli_token(hostname: str) -> str | None:
    """Return the GitHub CLI's token for *hostname*, or None when it has none.

    The host is named explicitly so a token issued for one GitHub host is
    never sent to another. A missing `gh`, a logged-out `gh`, or a slow one
    leaves the client unauthenticated rather than stopping the audit.

    Returns
    -------
    str | None
        The token `gh auth token --hostname` prints, or None.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv; the host comes from a parsed URL, and no shell runs
            ["gh", "auth", "token", "--hostname", hostname],  # noqa: S607 - gh is resolved on PATH by design
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _logger.debug(
            "gh auth token unavailable for %s: %s", hostname, type(error).__name__
        )
        return None
    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        _logger.debug(
            "gh auth token gave no token for %s (exit %d)",
            hostname,
            completed.returncode,
        )
        return None
    return token


artefact_app.command(rule_app, name="rule")
app.command(artefact_app, name="artefact")

owner_app = App()


@owner_app.command(name="use")
def owner_use(owner: str) -> None:
    """Record the active GitHub owner in the headline configuration."""
    xdg.set_active_owner(owner)
    print(f"active owner: {owner}")


@owner_app.command(name="show")
def owner_show() -> int:
    """Print the active GitHub owner."""
    if owner := xdg.get_active_owner():
        print(owner)
        return 0
    print("no active owner configured; run `concordat owner use <owner>`")
    return 1


app.command(owner_app, name="owner")


def _resolve_estate_or_active(
    alias: str | None = None, *, require_owner: bool = True
) -> EstateRecord:
    """Resolve an estate by alias or fall back to the active estate."""
    record = _get_estate_by_alias(alias) if alias else _get_active_estate_required()

    _ensure_github_owner_if_required(record, require_owner=require_owner)

    return record


def _get_estate_by_alias(alias: str) -> EstateRecord:
    """Get an estate by alias, raising if not found."""
    record = get_estate(alias)
    if record is None:
        raise EstateNotConfiguredError(alias)
    return record


def _get_active_estate_required() -> EstateRecord:
    """Get the active estate, raising if not configured."""
    record = get_active_estate()
    if record is None:
        raise ConcordatError(ERROR_NO_ACTIVE_ESTATE)
    return record


def _ensure_github_owner_if_required(
    record: EstateRecord, *, require_owner: bool
) -> None:
    """Validate that the estate has a github_owner if required."""
    if require_owner and not record.github_owner:
        raise ConcordatError(ERROR_ACTIVE_ESTATE_OWNER.format(alias=record.alias))


def _require_active_estate() -> EstateRecord:
    return _resolve_estate_or_active(require_owner=True)


def _resolve_estate_record(alias: str | None) -> EstateRecord:
    return _resolve_estate_or_active(alias, require_owner=False)


def _resolve_github_token(explicit: str | None = None) -> str:
    if not (token := explicit or _github_token_fallback()):
        raise ConcordatError(ERROR_MISSING_GITHUB_TOKEN)
    return token


@app.command()
def plan(
    *tofu_args: str,
    github_token: str | None = None,
    keep_workdir: bool = False,
) -> int:
    """Run `tofu plan` for the active estate."""
    record = _require_active_estate()
    token = _resolve_github_token(github_token)
    options = ExecutionOptions(
        github_owner=record.github_owner or "",
        github_token=token,
        extra_args=tofu_args,
        keep_workdir=keep_workdir,
    )
    io = ExecutionIO(stdout=sys.stdout, stderr=sys.stderr)
    exit_code, _ = run_plan(record, options, io)
    return exit_code


@app.command()
def apply(
    *tofu_args: str,
    github_token: str | None = None,
    auto_approve: bool = False,
    keep_workdir: bool = False,
) -> int:
    """Run `tofu apply` for the active estate."""
    if not auto_approve:
        raise ConcordatError(ERROR_AUTO_APPROVE_REQUIRED)
    record = _require_active_estate()
    token = _resolve_github_token(github_token)
    args = _ensure_auto_approve_flag(tuple(tofu_args))
    options = ExecutionOptions(
        github_owner=record.github_owner or "",
        github_token=token,
        extra_args=args,
        keep_workdir=keep_workdir,
    )
    io = ExecutionIO(stdout=sys.stdout, stderr=sys.stderr)
    exit_code, _ = run_apply(record, options, io)
    return exit_code


def main(argv: list[str] | tuple[str, ...] | None = None) -> int:
    """Entry point for the concordat CLI."""
    try:
        # One-time, explicit legacy-config migration at the bootstrap boundary,
        # so `default_config_path()` stays a read-only query for every command.
        migrate_legacy_config()
        result = app(argv)
    except OperationalRuleError as error:
        print(f"concordat: {error}", file=sys.stderr)
        return 2
    except ConcordatError as error:
        print(f"concordat: {error}")
        return 1
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
