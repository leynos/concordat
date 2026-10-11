"""Build the Rust toolchain facts for the toolchain-policy rule.

The policy (RT-022 to RT-027) judges five things about a repository's Rust
toolchain, and no single file holds them all: the pin lives in
`rust-toolchain.toml`, the minimum supported Rust version (MSRV) in the Cargo
manifests, the means of moving the pin in `.github/dependabot.yml`, and the
evidence that CI exercises either in the workflows. The envelope gathers each
as a small fact so the policy stays a comparison rather than a parser.

Three decisions are made here rather than in the policy, because Rego has no
good way to make them:

* "today" is an input. The nightly-age clause needs a clock, and a policy that
  reads the clock cannot be tested. The caller supplies the date; the command
  boundary supplies the UTC date.
* workflow files are walked as data, not searched as text. A toolchain named
  in a comment or a step's display name must not count as CI using it.
* the governed packages come from the shared Rust surface resolver, so a
  workspace's members are judged only where `.concordat` declares them, as for
  every other Rust rule package. A virtual root with no declared members has no
  package of its own to judge.

A document that cannot be decoded stays a fact with its reason, so the policy
reports it as indeterminate rather than as a clean absence.
"""

from __future__ import annotations

import datetime as dt
import re
import typing as typ

from .dependabot_envelope import build_dependabot_envelope
from .envelope import DEFAULT_EXCEPTION_DOCUMENTS
from .exception_docs import DocumentScan, find_exception_sections
from .fs_probe import probe_file
from .markdown_envelope import load_workflows, resolved_root
from .rust_surfaces import resolve_rust_surfaces
from .toolchain import (
    CHANNEL_VERSION,
    ToolchainFacts,
    inspect_toolchain,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .codescene_coverage_envelope import Repository
    from .rust_surfaces import CargoSurface

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/rust-toolchain-policy"
DEFAULT_EXCEPTION_KEYWORD: typ.Final = "Toolchain policy"
ROOT_DIRECTORY: typ.Final = "/"

_NIGHTLY: typ.Final = re.compile(r"^nightly-\d{4}-\d{2}-\d{2}$")
_NUMERIC: typ.Final = re.compile(r"^(?P<minor>\d+\.\d+)(?:\.\d+)?$")
_ENV_REFERENCE: typ.Final = re.compile(r"^\$\{\{\s*env\.(?P<name>\w+)\s*\}\}$")
# The three places a step names a toolchain in a command: rustup installing or
# selecting one, and cargo's `+toolchain` override.
_RUSTUP_COMMAND: typ.Final = re.compile(
    r"rustup\s+(?:toolchain\s+install|default|override\s+set)\s+(?P<value>[\w.-]+)"
)
_CARGO_OVERRIDE: typ.Final = re.compile(r"\bcargo\s+\+(?P<value>[\w.-]+)")
_MSRV_JOB: typ.Final = re.compile(r"(?i)\bmsrv\b|cargo[- ]msrv")
_READS_MANIFEST_MSRV: typ.Final = re.compile(r"(?i)rust[-_]version")
_SELECTS_TOOLCHAIN: typ.Final = re.compile(r"(?i)rustup|toolchain")
_TOOLCHAIN_ACTIONS: typ.Final = (
    "dtolnay/rust-toolchain",
    "actions-rust-lang/setup-rust-toolchain",
    "leynos/shared-actions/.github/actions/setup-rust",
)


class CrateFacts(typ.TypedDict):
    """Store what the policy needs to know about one Cargo package.

    Attributes
    ----------
    path:
        The manifest's path relative to the checkout, in POSIX form.
    name:
        The package name, or None when the manifest names none.
    is_publishable:
        True unless the manifest sets `publish = false` or an empty registry
        list. Cargo's default is publishable, so absence means yes.
    has_lib:
        True when the package builds a library: a `[lib]` table or a
        `src/lib.rs` beside the manifest.
    rust_version:
        The package's own `rust-version`, or None.
    inherits_workspace:
        True when the package writes `rust-version.workspace = true`.
    """

    path: str
    name: str | None
    is_publishable: bool
    has_lib: bool
    rust_version: str | None
    inherits_workspace: bool


class DependabotFacts(typ.TypedDict):
    """Store the `rust-toolchain` entries of the Dependabot configuration.

    Attributes
    ----------
    present:
        True when the repository has a Dependabot configuration at all.
    error:
        Why the configuration could not be decoded, or None.
    entries:
        Each `rust-toolchain` update entry as its directories, normalised to
        `/`-rooted form, and its schedule interval.
    """

    present: bool
    error: str | None
    entries: list[DependabotEntry]


class DependabotEntry(typ.TypedDict):
    """One `rust-toolchain` update entry."""

    directories: list[str]
    interval: str | None


class CiFacts(typ.TypedDict):
    """Store which toolchains the workflows name, read as data.

    Attributes
    ----------
    errors:
        Workflow paths that did not decode, each with its reason. The policy
        cannot judge CI from a workflow it could not read.
    dated_nightlies:
        Every distinct dated-nightly toolchain a workflow names.
    numeric_minors:
        Every distinct `major.minor` of a numeric release a workflow names.
    msrv_job:
        True when a job id or name, or a step command, is about the MSRV.
    reads_manifest_msrv:
        True when a toolchain-selecting step reads the manifest's
        `rust-version`, so every declared MSRV is exercised by construction.
    """

    errors: list[str]
    dated_nightlies: list[str]
    numeric_minors: list[str]
    msrv_job: bool
    reads_manifest_msrv: bool


class ToolchainPolicyEnvelope(typ.TypedDict):
    """Store the evidence evaluated by the toolchain-policy rule.

    Attributes
    ----------
    schema_version:
        The envelope's schema version, which the policy checks.
    kind:
        The policy-input identifier, which the policy checks.
    repository:
        The audited checkout's identity.
    applicable:
        True when the checkout has governed Rust surfaces. A repository with
        none is outside the rule.
    evaluated_on:
        The ISO date the nightly-age clause measures against.
    toolchain:
        The pinned channel facts, or None when there is no pin.
    pin_minor:
        The `major.minor` of a numeric pin, or None.
    workspace_rust_version:
        The workspace's shared `rust-version`, or None.
    crates:
        Every governed package, workspace members expanded.
    dependabot:
        The `rust-toolchain` update entries.
    ci:
        The toolchains the workflows name.
    exceptions:
        Recorded toolchain-policy exception sections, by document.
    """

    schema_version: int
    kind: str
    repository: Repository
    applicable: bool
    evaluated_on: str
    toolchain: ToolchainFacts | None
    pin_minor: str | None
    workspace_rust_version: str | None
    crates: list[CrateFacts]
    dependabot: DependabotFacts
    ci: CiFacts
    exceptions: list[DocumentScan]


def _minor(version: str) -> str | None:
    """Return the `major.minor` of a numeric version, or None."""
    match = _NUMERIC.match(version)
    return match.group("minor") if match else None


def _is_package_publishable(package: dict[str, object]) -> bool:
    """Return whether Cargo would let this package be published."""
    publish = package.get("publish", True)
    return publish is not False and publish != []


def _rust_version(package: dict[str, object]) -> tuple[str | None, bool]:
    """Return a package's own `rust-version` and whether it inherits one."""
    value = package.get("rust-version")
    if isinstance(value, str):
        return value, False
    inherits = isinstance(value, dict) and value.get("workspace") is True
    return None, inherits


def _crate_facts(checkout: pathlib.Path, surface: CargoSurface) -> CrateFacts | None:
    """Return the package facts for one manifest, or None for a virtual root."""
    parsed = surface["parsed"]
    package = parsed.get("package")
    if not isinstance(package, dict):
        return None
    typed = typ.cast("dict[str, object]", package)
    rust_version, inherits = _rust_version(typed)
    manifest_dir = (checkout / surface["path"]).parent
    has_lib = "lib" in parsed or probe_file(manifest_dir / "src" / "lib.rs").present
    name = typed.get("name")
    return {
        "path": surface["path"],
        "name": name if isinstance(name, str) else None,
        "is_publishable": _is_package_publishable(typed),
        "has_lib": has_lib,
        "rust_version": rust_version,
        "inherits_workspace": inherits,
    }


def _workspace_rust_version(surfaces: cabc.Iterable[CargoSurface]) -> str | None:
    """Return the first `[workspace.package] rust-version` among *surfaces*."""
    for surface in surfaces:
        workspace = surface["parsed"].get("workspace")
        package = workspace.get("package") if isinstance(workspace, dict) else None
        value = package.get("rust-version") if isinstance(package, dict) else None
        if isinstance(value, str):
            return value
    return None


def _toolchain_names(value: object, env: dict[str, str]) -> list[str]:
    """Resolve a toolchain value, following one `${{ env.NAME }}` reference."""
    if not isinstance(value, str):
        return []
    reference = _ENV_REFERENCE.match(value.strip())
    resolved = env.get(reference.group("name"), "") if reference else value.strip()
    return [resolved] if resolved else []


def _collect_env(node: object, env: dict[str, str]) -> None:
    """Gather every literal `env:` entry in a workflow into *env*."""
    match node:
        case dict():
            for key, value in node.items():
                if key == "env" and isinstance(value, dict):
                    env.update({
                        str(name): text
                        for name, text in value.items()
                        if isinstance(text, str)
                    })
                _collect_env(value, env)
        case list():
            for item in node:
                _collect_env(item, env)
        case _:
            pass


def _step_toolchains(step: dict[str, object], env: dict[str, str]) -> list[str]:
    """Return the toolchains one workflow step names."""
    names: list[str] = []
    uses = step.get("uses")
    inputs = step.get("with")
    if isinstance(uses, str) and uses.startswith(_TOOLCHAIN_ACTIONS):
        if isinstance(inputs, dict):
            names += _toolchain_names(inputs.get("toolchain"), env)
        # `dtolnay/rust-toolchain@nightly-2026-03-26` names its toolchain in
        # the ref, which is the action's documented way to select one.
        ref = uses.rpartition("@")[2]
        if ref and not re.fullmatch(r"[0-9a-f]{40}", ref):
            names.append(ref)
    run = step.get("run")
    if isinstance(run, str):
        names += [m.group("value") for m in _RUSTUP_COMMAND.finditer(run)]
        names += [m.group("value") for m in _CARGO_OVERRIDE.finditer(run)]
    return names


def _walk_steps(document: object) -> cabc.Iterator[tuple[str, dict[str, object]]]:
    """Yield `(job label, step)` for every step of every job in a workflow."""
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, dict):
        return
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        label = f"{job_id} {job.get('name', '')}"
        for step in steps:
            if isinstance(step, dict):
                yield label, typ.cast("dict[str, object]", step)


def _ci_facts(checkout: pathlib.Path) -> CiFacts:
    """Read the workflows' toolchain evidence, retaining decode failures."""
    errors: list[str] = []
    names: set[str] = set()
    msrv_job = False
    reads_manifest_msrv = False
    for workflow in load_workflows(checkout, resolved_root(checkout)):
        if workflow["error"] is not None:
            errors.append(f"{workflow['path']}: {workflow['error']}")
            continue
        document = workflow["parsed"]
        env: dict[str, str] = {}
        _collect_env(document, env)
        for label, step in _walk_steps(document):
            found = _step_toolchains(step, env)
            names.update(found)
            run = step.get("run")
            step_text = (
                f"{label} {step.get('name', '')} {run if isinstance(run, str) else ''}"
            )
            msrv_job = msrv_job or bool(_MSRV_JOB.search(step_text))
            reads_manifest_msrv = reads_manifest_msrv or (
                bool(_SELECTS_TOOLCHAIN.search(step_text))
                and bool(_READS_MANIFEST_MSRV.search(step_text))
            )
    return {
        "errors": errors,
        "dated_nightlies": sorted(n for n in names if _NIGHTLY.match(n)),
        "numeric_minors": sorted({m for n in names if (m := _minor(n)) is not None}),
        "msrv_job": msrv_job,
        "reads_manifest_msrv": reads_manifest_msrv,
    }


def _normalise_directory(directory: str) -> str:
    """Return a Dependabot directory in `/`-rooted form without a trailing slash."""
    stripped = directory.strip("/")
    return f"/{stripped}" if stripped else ROOT_DIRECTORY


def _dependabot_facts(checkout: pathlib.Path) -> DependabotFacts:
    """Return the `rust-toolchain` entries of the Dependabot configuration."""
    config = build_dependabot_envelope(checkout)["config"]
    if config is None:
        return {"present": False, "error": None, "entries": []}
    if config["error"] is not None:
        return {"present": True, "error": config["error"], "entries": []}
    parsed = config["parsed"]
    updates = parsed.get("updates") if isinstance(parsed, dict) else None
    entries: list[DependabotEntry] = []
    for entry in updates if isinstance(updates, list) else []:
        if not isinstance(entry, dict) or entry.get("package-ecosystem") != (
            "rust-toolchain"
        ):
            continue
        listed = entry.get("directories")
        directories = (
            listed if isinstance(listed, list) else [entry.get("directory", "/")]
        )
        schedule = entry.get("schedule")
        interval = schedule.get("interval") if isinstance(schedule, dict) else None
        entries.append({
            "directories": [_normalise_directory(str(d)) for d in directories],
            "interval": interval if isinstance(interval, str) else None,
        })
    return {"present": True, "error": None, "entries": entries}


def _exception_documents(parameters: cabc.Mapping[str, object]) -> list[str]:
    """Return the documents to scan for a recorded exception."""
    declared = parameters.get("exception_documents")
    if isinstance(declared, list):
        return [
            item for item in typ.cast("list[object]", declared) if isinstance(item, str)
        ]
    return list(DEFAULT_EXCEPTION_DOCUMENTS)


def _exception_keyword(parameters: cabc.Mapping[str, object]) -> str:
    """Return the word an exception heading has to contain."""
    declared = parameters.get("exception_keyword")
    return declared if isinstance(declared, str) else DEFAULT_EXCEPTION_KEYWORD


def build_toolchain_policy_envelope(
    checkout: pathlib.Path,
    parameters: cabc.Mapping[str, object] | None = None,
    *,
    today: dt.date | None = None,
) -> ToolchainPolicyEnvelope:
    """Assemble the toolchain-policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the local checkout to audit.
    parameters:
        The rule manifest's parameter defaults. Only the exception-document
        list and keyword are read here; `window_days` and the other tunables
        are policy decisions and reach Conftest through `data.parameters`.
    today:
        The date the nightly-age clause measures against. The default is the
        current UTC date, which is the command boundary's choice; tests pass a
        fixed one.

    Returns
    -------
    ToolchainPolicyEnvelope
        The policy input document assembled from the checkout.
    """
    resolved = parameters if parameters is not None else {}
    resolution = resolve_rust_surfaces(checkout)
    toolchain = inspect_toolchain(checkout)
    crates = [
        facts
        for surface in resolution.surfaces
        if (facts := _crate_facts(checkout, surface)) is not None
    ]
    channel = toolchain["channel"] if toolchain is not None else None
    is_version = toolchain is not None and toolchain["channel_kind"] == CHANNEL_VERSION
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicable": bool(resolution.surfaces),
        "evaluated_on": (today or dt.datetime.now(dt.UTC).date()).isoformat(),
        "toolchain": toolchain,
        "pin_minor": _minor(channel) if is_version and channel is not None else None,
        "workspace_rust_version": _workspace_rust_version(resolution.surfaces),
        "crates": crates,
        "dependabot": _dependabot_facts(checkout),
        "ci": _ci_facts(checkout),
        "exceptions": find_exception_sections(
            checkout,
            _exception_documents(resolved),
            _exception_keyword(resolved),
        ),
    }
