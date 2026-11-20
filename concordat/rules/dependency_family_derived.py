"""Collect consumer-declared bounds and derived-input equality facts.

A relationship is only as strong as the places a repository copies its version
into. This module reads the two `.concordat` declarations that anchor it: a
documented bound that must not be relaxed, and a canonical version or action
pin that scripts, workflows and action inputs must still match.

Selectors resolve outside policy; anything unestablished is indeterminate.
"""

from __future__ import annotations

import re
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .action_pins import FULL_SHA
from .dependency_family_coverage import mapping, sequence
from .dependency_family_versions import bound_status, satisfies, upper_bound
from .interrogate_invocation import lex_segments
from .make_commands import blank_references, command_start
from .makefile_expansion import expand, shown, single_assignments
from .makefile_facts import MakefileFacts, inspect_makefile

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .dependency_family_facts import Declaration
    from .makefile_expansion import Assignments

_yaml = YAML(typ="safe")

RESOLVED: typ.Final = "resolved"
UNRESOLVED: typ.Final = "unresolved"
AMBIGUOUS: typ.Final = "ambiguous"
MISSING: typ.Final = "missing"

_TOOL_VERSION: typ.Final = re.compile(
    r"^(?P<tool>[A-Za-z0-9][A-Za-z0-9._-]*)(?:==|@)(?P<version>[^\s]+)$"
)
_EXACT_PIN: typ.Final = re.compile(r"^(?:==|=)\s*(?P<version>[^,\s]+)$")
_VERSION_TOKEN: typ.Final = re.compile(
    r"(?<![\w.-])(?P<version>\d+(?:\.\d+)+(?:[-+][^\s,;)]+)?)(?![\w.-])"
)


class ProtectedBound(typ.TypedDict):
    """One documented compatibility bound and how the declaration now reads."""

    relationship: str
    package: str
    manifest: str | None
    bound: str
    status: str
    current: str | None
    evidence: list[str]
    rationale: str
    review_trigger: str


class Canonical(typ.TypedDict):
    """The value every derived occurrence must still match."""

    kind: str
    value: str | None
    requirement: str | None
    ecosystem: str | None
    status: str
    location: str | None
    reason: str | None


class Selector(typ.TypedDict):
    """One place a derived value is copied into."""

    kind: str
    path: str
    status: str
    raw: str | None
    expanded: str | None
    value: str | None
    location: str | None
    matches: bool | None
    reason: str | None
    override: str | None
    override_rationale: str | None
    override_review_trigger: str | None


class Reviewed(typ.TypedDict):
    """One reviewed action SHA and where its review is recorded."""

    sha: str
    provenance: str


class DerivedInput(typ.TypedDict):
    """One canonical value and every place that must agree with it."""

    id: str
    comparator: str
    canonical: Canonical
    selectors: list[Selector]
    siblings: list[Selector]
    reviewed: list[Reviewed]
    rationale: str
    review_trigger: str


def _text(value: object) -> str | None:
    """Return a non-blank scalar as text, or None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strings(value: object) -> list[str]:
    """Return a list's non-blank string items, in order."""
    return [
        item.strip()
        for item in sequence(value) or []
        if isinstance(item, str) and item.strip()
    ]


def _evidence(value: object) -> list[str]:
    """Return an evidence field as a list of non-blank strings."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    return _strings(value)


def _selector_base(kind: str, path: str, *, location: str | None = None) -> Selector:
    """Return an empty selector fact for *kind* and *path*."""
    return {
        "kind": kind,
        "path": path,
        "status": MISSING,
        "raw": None,
        "expanded": None,
        "value": None,
        "location": location,
        "matches": None,
        "reason": None,
        "override": None,
        "override_rationale": None,
        "override_review_trigger": None,
    }


def _first_version(text: str) -> str | None:
    """Return the first dotted version in *text*, when there is one."""
    match = _VERSION_TOKEN.search(text)
    return None if match is None else match.group("version")


def _single_value(selector: Selector, values: list[tuple[str, str]]) -> Selector:
    """Return *selector* carrying the one distinct value the occurrences give.

    Parameters
    ----------
    selector:
        The selector fact to complete.
    values:
        Each occurrence's value and a location describing where it was read.

    Returns
    -------
    Selector
        The selector with `resolved`, `ambiguous` or `missing` status.
    """
    distinct = sorted({value for value, _ in values})
    if not distinct:
        return {**selector, "status": MISSING, "reason": "no occurrence was found"}
    if len(distinct) > 1:
        return {
            **selector,
            "status": AMBIGUOUS,
            "reason": f"occurrences disagree: {', '.join(distinct)}",
        }
    location = next((where for value, where in values if value == distinct[0]), None)
    return {
        **selector,
        "status": RESOLVED,
        "value": distinct[0],
        "location": location or selector["location"],
    }


def _matches(canonical: Canonical, selector: Selector, comparator: str) -> bool | None:
    """Return whether *selector* still agrees with *canonical*."""
    if canonical["status"] != RESOLVED or selector["value"] is None:
        return None
    if comparator == "equal":
        return selector["value"] == canonical["value"]
    requirement = canonical["requirement"]
    ecosystem = canonical["ecosystem"]
    if requirement is None or ecosystem is None:
        return None
    return satisfies(requirement, selector["value"], ecosystem)


def _manifest_canonical(
    entry: dict[str, object],
    declarations: cabc.Sequence[Declaration],
    comparator: str,
) -> Canonical:
    """Return the canonical value a manifest declaration states."""
    manifest = _text(entry.get("manifest")) or "pyproject.toml"
    package = _text(entry.get("package"))
    if package is None:
        return {
            "kind": "manifest",
            "value": None,
            "requirement": None,
            "ecosystem": None,
            "status": MISSING,
            "location": manifest,
            "reason": "the canonical manifest declaration names no package",
        }
    matches = [
        declaration
        for declaration in declarations
        if declaration["manifest"] == manifest and declaration["package"] == package
    ]
    if not matches:
        return {
            "kind": "manifest",
            "value": None,
            "requirement": None,
            "ecosystem": None,
            "status": MISSING,
            "location": manifest,
            "reason": f"{manifest} declares no {package}",
        }
    requirements = sorted({declaration["requirement"] for declaration in matches})
    if len(requirements) != 1:
        return {
            "kind": "manifest",
            "value": None,
            "requirement": None,
            "ecosystem": matches[0]["ecosystem"],
            "status": AMBIGUOUS,
            "location": manifest,
            "reason": f"{manifest} declares {package} with several requirements",
        }
    requirement = requirements[0]
    if comparator == "equal":
        exact = _EXACT_PIN.match(requirement)
        if exact is None:
            return {
                "kind": "manifest",
                "value": None,
                "requirement": requirement,
                "ecosystem": matches[0]["ecosystem"],
                "status": UNRESOLVED,
                "location": manifest,
                "reason": "an equal comparison needs an exact canonical pin",
            }
        value = exact.group("version")
    else:
        value = requirement
    return {
        "kind": "manifest",
        "value": value,
        "requirement": requirement,
        "ecosystem": matches[0]["ecosystem"],
        "status": RESOLVED,
        "location": manifest,
        "reason": None,
    }


def _workflow_steps(
    document: object,
) -> cabc.Iterator[tuple[str, str, dict[str, object]]]:
    """Yield each workflow step with its job name and index."""
    jobs = mapping(document)
    for job_name, job in (mapping((jobs or {}).get("jobs")) or {}).items():
        steps = sequence((mapping(job) or {}).get("steps")) or []
        for index, step in enumerate(steps):
            table = mapping(step)
            if table is not None:
                yield str(job_name), str(index), table


def _load_workflow(path: pathlib.Path) -> tuple[object | None, str | None]:
    """Return one workflow document, or why it could not be decoded."""
    try:
        return _yaml.load(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, YAMLError) as error:
        return None, str(error)


def _workflow_paths(checkout: pathlib.Path, path: str | None) -> list[pathlib.Path]:
    """Return the named workflow, or every workflow when no path is named."""
    if path is not None:
        return [checkout / path]
    directory = checkout / ".github" / "workflows"
    try:
        return sorted(
            entry
            for entry in directory.iterdir()
            if entry.suffix in {".yml", ".yaml"} and entry.is_file()
        )
    except OSError:
        return []


def _action_canonical(
    checkout: pathlib.Path,
    entry: dict[str, object],
) -> Canonical:
    """Return the canonical action SHA one workflow's step pins."""
    path = _text(entry.get("path"))
    prefix = _text(entry.get("uses")) or _text(entry.get("uses_prefix"))
    if prefix is None:
        return {
            "kind": "action",
            "value": None,
            "requirement": None,
            "ecosystem": None,
            "status": MISSING,
            "location": path,
            "reason": "the canonical action names no uses prefix",
        }
    refs: list[tuple[str, str]] = []
    for workflow in _workflow_paths(checkout, path):
        document, error = _load_workflow(workflow)
        if error is not None:
            continue
        for job, index, step in _workflow_steps(document):
            uses = _text(step.get("uses"))
            if uses is None or not uses.startswith(f"{prefix}@"):
                continue
            ref = uses.removeprefix(f"{prefix}@")
            if FULL_SHA.match(ref):
                where = (
                    f"{workflow.relative_to(checkout).as_posix()} "
                    f"job {job} step {index}"
                )
                refs.append((ref, where))
    selector = _single_value(_selector_base("action-ref", path or ""), refs)
    return {
        "kind": "action",
        "value": selector["value"],
        "requirement": None,
        "ecosystem": None,
        "status": selector["status"],
        "location": selector["location"] or path,
        "reason": selector["reason"],
    }


def _canonical(
    checkout: pathlib.Path,
    entry: object,
    declarations: cabc.Sequence[Declaration],
    comparator: str,
) -> Canonical:
    """Return the canonical fact of one derived-input declaration."""
    table = mapping(entry) or {}
    if "action" in table:
        action = mapping(table.get("action"))
        if action is not None:
            return _action_canonical(checkout, action)
        prefix = _text(table.get("action"))
        if prefix is not None:
            return _action_canonical(checkout, {"uses": prefix})
    return _manifest_canonical(table, declarations, comparator)


def _makefile_facts(
    checkout: pathlib.Path, path: str
) -> tuple[MakefileFacts | None, str | None]:
    """Return a Makefile's facts, or why they could not be read."""
    try:
        return inspect_makefile(checkout / path), None
    except Exception as error:  # noqa: BLE001 - a selector fact, not a crash
        return None, str(error)


def _makefile_variable(checkout: pathlib.Path, table: dict[str, object]) -> Selector:
    """Resolve a variable assignment to its expanded value."""
    path = _text(table.get("path")) or "Makefile"
    variable = _text(table.get("variable"))
    selector = _selector_base("makefile-variable", path)
    if variable is None:
        return {**selector, "reason": "the selector names no variable"}
    facts, error = _makefile_facts(checkout, path)
    if facts is None:
        return {**selector, "status": UNRESOLVED, "reason": error}
    values = single_assignments(facts.report["variables"])
    if variable not in values:
        assigned = any(
            item.get("name") == variable for item in facts.report["variables"]
        )
        return {
            **selector,
            "status": AMBIGUOUS if assigned else MISSING,
            "reason": (
                f"{variable} has no single unconditional assignment"
                if assigned
                else f"{path} does not assign {variable}"
            ),
        }
    raw = values[variable]
    expansion = expand(raw, values)
    return {
        **selector,
        "status": RESOLVED if expansion.is_resolved else UNRESOLVED,
        "raw": raw,
        "expanded": expansion.text,
        "value": _first_version(expansion.text),
        "reason": None if expansion.is_resolved else "the value did not expand",
    }


def _recipe_occurrences(
    facts: MakefileFacts,
    path: str,
    tool: str,
    target: str | None,
    values: Assignments,
) -> tuple[list[tuple[str, str]], bool]:
    """Return the tool's version occurrences and whether one failed to expand."""
    occurrences: list[tuple[str, str]] = []
    unresolved = False
    for rule in facts.report["rules"]:
        if target is not None and target not in rule["targets"]:
            continue
        for recipe in rule["recipes"]:
            expansion = expand(recipe["text"], values)
            line = shown(blank_references(expansion.text))
            if not expansion.is_resolved:
                unresolved = True
            for words in lex_segments(line) or []:
                start = command_start(words)
                for word in words[start:]:
                    match = _TOOL_VERSION.match(word)
                    if match is not None and match.group("tool") == tool:
                        where = f"{path}:{recipe['location']['start_line']}"
                        occurrences.append((match.group("version"), where))
    return occurrences, unresolved


def _makefile_recipe(checkout: pathlib.Path, table: dict[str, object]) -> Selector:
    """Resolve a tool's version as a recipe command states it."""
    path = _text(table.get("path")) or "Makefile"
    tool = _text(table.get("tool"))
    target = _text(table.get("target"))
    selector = _selector_base("makefile-recipe", path)
    if tool is None:
        return {**selector, "reason": "the selector names no tool"}
    facts, error = _makefile_facts(checkout, path)
    if facts is None:
        return {**selector, "status": UNRESOLVED, "reason": error}
    values = single_assignments(facts.report["variables"])
    occurrences, unresolved = _recipe_occurrences(facts, path, tool, target, values)
    resolved = _single_value(selector, occurrences)
    if unresolved and resolved["status"] == RESOLVED:
        return {
            **resolved,
            "status": UNRESOLVED,
            "reason": "a recipe value did not expand",
        }
    return resolved


def _workflow_env_occurrences(
    checkout: pathlib.Path, path: str, variable: str, tool: str | None
) -> list[tuple[str, str]]:
    """Return each effective step value of *variable* and where it was read."""
    occurrences: list[tuple[str, str]] = []
    for workflow in _workflow_paths(checkout, path or None):
        document, error = _load_workflow(workflow)
        if error is not None:
            continue
        root = mapping(document) or {}
        workflow_env = mapping(root.get("env")) or {}
        for job_name, job in (mapping(root.get("jobs")) or {}).items():
            job_table = mapping(job) or {}
            job_env = {**(workflow_env or {}), **(mapping(job_table.get("env")) or {})}
            for index, step in enumerate(sequence(job_table.get("steps")) or []):
                step_table = mapping(step) or {}
                if tool is not None and tool not in str(step_table.get("run", "")):
                    continue
                effective = {**job_env, **(mapping(step_table.get("env")) or {})}
                value = effective.get(variable)
                if value is None:
                    continue
                where = (
                    f"{workflow.relative_to(checkout).as_posix()} "
                    f"job {job_name} step {index}"
                )
                occurrences.append((str(value), where))
    return occurrences


def _workflow_env(checkout: pathlib.Path, table: dict[str, object]) -> Selector:
    """Resolve an environment variable's effective value for a step."""
    path = _text(table.get("path")) or ""
    variable = _text(table.get("variable"))
    tool = _text(table.get("tool"))
    selector = _selector_base("workflow-env", path, location=path or None)
    if variable is None:
        return {**selector, "reason": "the selector names no variable"}
    occurrences = _workflow_env_occurrences(checkout, path, variable, tool)
    resolved = _single_value(selector, occurrences)
    if resolved["value"] is not None and "${{" in resolved["value"]:
        return {
            **resolved,
            "status": UNRESOLVED,
            "reason": "the value is an expression the audit does not evaluate",
        }
    return resolved


def _action_steps(
    checkout: pathlib.Path, path: str, prefix: str
) -> cabc.Iterator[tuple[dict[str, object], str]]:
    """Yield each step using *prefix* and the location that names it."""
    for workflow in _workflow_paths(checkout, path or None):
        document, error = _load_workflow(workflow)
        if error is not None:
            continue
        for job, index, step in _workflow_steps(document):
            uses = _text(step.get("uses"))
            if uses is None or not uses.startswith(f"{prefix}@"):
                continue
            where = (
                f"{workflow.relative_to(checkout).as_posix()} job {job} step {index}"
            )
            yield step, where


def _action_input(checkout: pathlib.Path, table: dict[str, object]) -> Selector:
    """Resolve one `with:` input of the steps that use an action."""
    path = _text(table.get("path")) or ""
    prefix = _text(table.get("uses")) or _text(table.get("uses_prefix"))
    input_name = _text(table.get("input"))
    selector = _selector_base("action-input", path, location=path or None)
    if prefix is None or input_name is None:
        return {**selector, "reason": "the selector names no action or input"}
    occurrences = [
        (str(value), where)
        for step, where in _action_steps(checkout, path, prefix)
        if (value := (mapping(step.get("with")) or {}).get(input_name)) is not None
    ]
    return _single_value(selector, occurrences)


def _action_ref(checkout: pathlib.Path, table: dict[str, object]) -> Selector:
    """Resolve the SHA an action reference is pinned to."""
    path = _text(table.get("path")) or ""
    prefix = _text(table.get("uses")) or _text(table.get("uses_prefix"))
    selector = _selector_base("action-ref", path, location=path or None)
    if prefix is None:
        return {**selector, "reason": "the selector names no action"}
    occurrences = [
        (ref, where)
        for step, where in _action_steps(checkout, path, prefix)
        if FULL_SHA.match(
            ref := (_text(step.get("uses")) or "").removeprefix(f"{prefix}@")
        )
    ]
    return _single_value(selector, occurrences)


_SELECTORS: typ.Final = {
    "makefile-variable": _makefile_variable,
    "makefile-recipe": _makefile_recipe,
    "workflow-env": _workflow_env,
    "action-input": _action_input,
    "action-ref": _action_ref,
}


def _selector(
    checkout: pathlib.Path,
    entry: object,
    canonical: Canonical,
    comparator: str,
) -> Selector | None:
    """Return one selector fact, or None when its declaration is malformed."""
    table = mapping(entry)
    if table is None:
        return None
    kind = _text(table.get("kind"))
    resolver = _SELECTORS.get(kind or "")
    if resolver is None:
        return None
    selector = resolver(checkout, table)
    override = _text(table.get("override"))
    selector["matches"] = _matches(canonical, selector, comparator)
    if override is not None:
        selector["override"] = override
        selector["override_rationale"] = _text(table.get("override_rationale"))
        selector["override_review_trigger"] = _text(
            table.get("override_review_trigger")
        )
    return selector


def _required_text(
    table: dict[str, object] | None, names: cabc.Sequence[str]
) -> tuple[str, ...] | None:
    """Return the named fields as text, or None when any is missing.

    Returns
    -------
    tuple[str, ...] | None
        The fields in order, or None when any is missing.
    """
    if table is None:
        return None
    values = tuple(_text(table.get(name)) for name in names)
    if any(value is None for value in values):
        return None
    return typ.cast("tuple[str, ...]", values)


def _bound_status(
    table: dict[str, object],
    package: str,
    bound: str,
    declarations: cabc.Sequence[Declaration],
) -> tuple[str | None, str]:
    """Return the declaration's current requirement and the bound's status."""
    manifest = _text(table.get("manifest"))
    matches = [
        declaration
        for declaration in declarations
        if declaration["package"] == package
        and (manifest is None or declaration["manifest"] == manifest)
    ]
    requirements = sorted({declaration["requirement"] for declaration in matches})
    # Two declarations of the same package under one relationship leave no
    # single requirement to compare, so the bound is indeterminate.
    if len(requirements) != 1:
        return None, UNKNOWN_STATUS
    current = requirements[0]
    if upper_bound(bound, matches[0]["ecosystem"]) is None:
        return current, UNKNOWN_STATUS
    return current, bound_status(bound, current, matches[0]["ecosystem"])


def protected_bounds(
    section: dict[str, object],
    declarations: cabc.Sequence[Declaration],
    relationship_ids: cabc.Collection[str],
    member_packages: cabc.Mapping[str, set[str]],
    problems: list[str],
) -> list[ProtectedBound]:
    """Return the consumer-declared compatibility bounds and their status.

    Returns
    -------
    list[ProtectedBound]
        The valid declarations. A malformed one contributes a problem and no
        fact, so the policy cannot read it as held.
    """
    found: list[ProtectedBound] = []
    label = "a dependencies.protected_bounds entry"
    for raw in sequence(section.get("protected_bounds")) or []:
        table = mapping(raw)
        if (
            table is None
            or (
                fields := _required_text(
                    table,
                    ("relationship", "package", "bound", "rationale", "review_trigger"),
                )
            )
            is None
        ):
            problems.append(
                f"{label} in .concordat needs relationship, package, bound, "
                "rationale and review_trigger"
            )
            continue
        relationship, package, bound, rationale, review_trigger = fields
        if relationship not in relationship_ids:
            problems.append(f"{label} names the unknown relationship {relationship}")
            continue
        if package not in member_packages.get(relationship, set()):
            problems.append(
                f"{label} names {package}, which is not a member of {relationship}"
            )
            continue
        current, status = _bound_status(table, package, bound, declarations)
        found.append({
            "relationship": relationship,
            "package": package,
            "manifest": _text(table.get("manifest")),
            "bound": bound,
            "status": status,
            "current": current,
            "evidence": _evidence(table.get("evidence")),
            "rationale": rationale,
            "review_trigger": review_trigger,
        })
    return found


UNKNOWN_STATUS: typ.Final = "unknown"


def _selector_list(
    checkout: pathlib.Path,
    value: object,
    canonical: Canonical,
    comparator: str,
) -> list[Selector]:
    """Return every selector of a known kind, dropping the unknown ones."""
    return [
        selector
        for entry in sequence(value) or []
        if (selector := _selector(checkout, entry, canonical, comparator)) is not None
    ]


def _reviewed_pins(table: dict[str, object]) -> list[Reviewed]:
    """Return the reviewed action commits, dropping incomplete entries."""
    reviewed: list[Reviewed] = []
    for entry in sequence(table.get("reviewed")) or []:
        reviewed_entry = mapping(entry)
        sha = _text((reviewed_entry or {}).get("sha"))
        provenance = _text((reviewed_entry or {}).get("provenance"))
        if sha is None or provenance is None or not FULL_SHA.match(sha):
            continue
        reviewed.append({"sha": sha, "provenance": provenance})
    return reviewed


def derived_inputs(
    checkout: pathlib.Path,
    section: dict[str, object],
    declarations: cabc.Sequence[Declaration],
    problems: list[str],
) -> list[DerivedInput]:
    """Return the consumer's canonical values and every checked occurrence.

    Returns
    -------
    list[DerivedInput]
        The valid declarations, each with resolved selector facts.
    """
    found: list[DerivedInput] = []
    label = "a dependencies.derived_inputs entry"
    for raw in sequence(section.get("derived_inputs")) or []:
        table = mapping(raw)
        comparator = _text((table or {}).get("comparator")) or "equal"
        if (
            table is None
            or (fields := _required_text(table, ("id", "rationale", "review_trigger")))
            is None
            or comparator not in {"equal", "satisfies"}
        ):
            problems.append(
                f"{label} in .concordat needs id, comparator (equal or "
                "satisfies), rationale and review_trigger"
            )
            continue
        identifier, rationale, review_trigger = fields
        canonical = _canonical(
            checkout, table.get("canonical"), declarations, comparator
        )
        selectors = _selector_list(
            checkout, table.get("selectors"), canonical, comparator
        )
        malformed = len(sequence(table.get("selectors")) or []) - len(selectors)
        if malformed:
            problems.append(
                f"{label} {identifier} has {malformed} selector(s) with an unknown kind"
            )
        found.append({
            "id": identifier,
            "comparator": comparator,
            "canonical": canonical,
            "selectors": selectors,
            "siblings": _selector_list(
                checkout, table.get("siblings"), canonical, comparator
            ),
            "reviewed": _reviewed_pins(table),
            "rationale": rationale,
            "review_trigger": review_trigger,
        })
    return found
