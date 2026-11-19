"""Read the dated toolchain literals a workflow or Makefile selects, as data.

RT-028 asks whether CI or a build file restates the date of the nightly that
`rust-toolchain.toml` pins. A restated date drifts the moment Dependabot moves
the pin, so the question is "which dated nightlies does this file select", not
"does this date appear in the text".

Five decisions are made here rather than in the policy:

* workflows are walked as decoded documents. A nightly in a comment or in a
  step's display name is not CI using it. Only the places a workflow selects a
  toolchain are read: a step's `run` command, a toolchain action's `toolchain`
  input, and the action's own ref.
* a literal the workflow's document, job or step `env` declares is a
  deliberately separate pin, and is not reported. Declaring it there is the
  explicit way for a repository to say the pin is separate.
* a Makefile is read from the `makeutil` report, because a recipe is what Make
  runs. A variable assigned the date itself, at the name the recipe writes, is
  a deliberate separate pin. A literal written inline is reported; a date
  reached through another variable is reported at its expanded value; and a
  reference to a variable the Makefile never assigns, where no included file
  could, is reported as the reference, since nothing declares it.
* only dated nightlies are read. A numeric literal in CI is usually a host
  tool's toolchain, which is why RT-027 judges dated nightlies only too.
* the findings belong to the envelope builder. The facts readers run at the
  builder's command boundary, so this module lives in the package rather than
  being reached across the boundary from a query.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from concordat.errors import OperationalRuleError

from .makefile_expansion import expand, shown, single_assignments
from .makefile_facts import MakefileInspector, MakefileRefusedError, inspect_makefile
from .markdown_envelope import (
    OPERATION_READ_MAKEFILE,
    is_file,
    load_workflows,
    resolved_root,
)
from .toolchain import inspect_toolchain

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

DATED_NIGHTLY: typ.Final = re.compile(r"(?<![\w-])nightly-\d{4}-\d{2}-\d{2}(?![\w-])")
# A value that is a Make reference rather than a literal: `$(NAME)`/`${NAME}`.
_REFERENCE: typ.Final = re.compile(
    r"^\$(?:\((?P<paren>[A-Za-z_][\w-]*)\)|\{(?P<brace>[A-Za-z_][\w-]*)\})$"
)
# The programs that take cargo's `+toolchain` override. A reference is one too
# when its name says cargo, which is what `$(CARGO)` is in a Makefile.
_TOOLCHAINS: typ.Final = (
    "cargo",
    "cargo-nextest",
    "clippy-driver",
    "cross",
    "miri",
    "rustc",
    "rustup",
)
_RUSTUP_VERBS: typ.Final = ("default", "install", "override", "run")
# rustup options that take a value, so the value is not read as a toolchain.
_VALUE_OPTIONS: typ.Final = frozenset({
    "--profile",
    "-c",
    "--component",
    "-t",
    "--target",
})
_SHELL_SEPARATORS: typ.Final = frozenset({";", ";;", "&", "&&", "|", "||", "#"})
_MAKEFILE_NAMES: typ.Final = ("GNUmakefile", "Makefile", "makefile")
TOOLCHAIN_ACTIONS: typ.Final = (
    "dtolnay/rust-toolchain",
    "actions-rust-lang/setup-rust-toolchain",
    "leynos/shared-actions/.github/actions/setup-rust",
)

KIND_WORKFLOW: typ.Final = "workflow"
KIND_MAKEFILE: typ.Final = "makefile"


class LiteralFacts(typ.TypedDict):
    """One dated nightly literal a workflow or Makefile selects.

    Attributes
    ----------
    path:
        The file naming it, relative to the checkout.
    kind:
        ``workflow`` or ``makefile``.
    text:
        The literal as written, or the reference that stands for a value the
        reader cannot see (``$(DEV_FAST_TOOLCHAIN)``).
    """

    path: str
    kind: str
    text: str


class LiteralError(typ.TypedDict):
    """One file whose literals could not be read.

    Attributes
    ----------
    path:
        The file, relative to the checkout.
    reason:
        Why it could not be read.
    """

    path: str
    reason: str


class ToolchainLiteralFacts(typ.TypedDict):
    """Store the dated nightlies the CI and build files select.

    Attributes
    ----------
    reads_pin:
        True when `rust-toolchain.toml` is present and readable. Without a pin
        there is nothing for a literal to bypass, so the rule is silent.
    literals:
        Every dated nightly literal the files select.
    errors:
        Files that could not be read, each with its reason. The policy reports
        them as indeterminate rather than as a clean absence.
    """

    reads_pin: bool
    literals: list[LiteralFacts]
    errors: list[LiteralError]


@dataclasses.dataclass(frozen=True, slots=True)
class Selection:
    """One toolchain a command selects, as the command writes it."""

    text: str
    is_reference: bool


@dataclasses.dataclass(frozen=True, slots=True)
class VariableTable:
    """A Makefile's substitutable variables, and the names it leaves unknown.

    Attributes
    ----------
    values:
        Each variable with one plain assignment, mapped to its raw value.
    assigned:
        Every name the Makefile assigns, whether or not it can be substituted.
    has_includes:
        Whether the Makefile includes another file, which may assign a name
        this table does not carry.
    """

    values: cabc.Mapping[str, str]
    assigned: cabc.Container[str]
    has_includes: bool


def _trim(word: str) -> str:
    """Return *word* without the quoting and separators glued to it."""
    return word.strip("\"'`").rstrip(";|&")


def _tokens(text: str) -> list[str]:
    """Return the words of a command text, backslash continuations folded."""
    return re.sub(r"\\\n[ \t]*", " ", text).split()


def _is_tool(word: str) -> bool:
    """Return whether *word* is a program that takes the `+toolchain` override."""
    bare = _trim(word).rpartition("/")[2]
    if bare in _TOOLCHAINS:
        return True
    return bool(_REFERENCE.match(bare)) and "cargo" in bare.lower()


def _overrides(tokens: list[str]) -> cabc.Iterator[Selection]:
    """Yield each `+toolchain` override whose program is a known tool."""
    for index, word in enumerate(tokens):
        current = _trim(word)
        if index and _is_tool(tokens[index - 1]) and current.startswith("+"):
            value = current[1:]
            yield Selection(text=value, is_reference=bool(_REFERENCE.match(value)))


def _environment(tokens: list[str]) -> cabc.Iterator[Selection]:
    """Yield each `RUSTUP_TOOLCHAIN=` value assigned on a command."""
    for word in tokens:
        name, separator, value = _trim(word).partition("=")
        if separator and name == "RUSTUP_TOOLCHAIN" and value:
            yield Selection(text=value, is_reference=bool(_REFERENCE.match(value)))


def _option_values(tokens: list[str]) -> cabc.Iterator[Selection]:
    """Yield each `--toolchain` value, in either spelling."""
    for index, word in enumerate(tokens):
        current = _trim(word)
        value = ""
        if current == "--toolchain" and index + 1 < len(tokens):
            value = _trim(tokens[index + 1])
        elif current.startswith("--toolchain="):
            value = current.partition("=")[2]
        if value:
            yield Selection(text=value, is_reference=bool(_REFERENCE.match(value)))


def _positional(args: list[str]) -> list[str]:
    """Return the non-option words of a `rustup` argument list."""
    names: list[str] = []
    words = iter(args)
    for word in words:
        if word in _VALUE_OPTIONS:
            next(words, None)
        elif not word.startswith("-"):
            names.append(word)
    return names


def _rustup_arguments(rest: list[str]) -> cabc.Iterator[Selection]:
    """Yield the positional toolchains of one `rustup` invocation."""
    if rest[:2] == ["toolchain", "install"]:
        rest = rest[1:]
    if not rest:
        return
    verb, args = rest[0], rest[1:]
    if verb == "override" and args[:1] == ["set"]:
        args = args[1:]
    elif verb not in _RUSTUP_VERBS:
        # `rustup component add ... --toolchain X` names its toolchain through
        # the option, which `_option_values` reads instead.
        return
    names = _positional(args)
    for name in names if verb == "install" else names[:1]:
        yield Selection(text=name, is_reference=bool(_REFERENCE.match(name)))


def _rustup(tokens: list[str]) -> cabc.Iterator[Selection]:
    """Yield the toolchains each `rustup` command in *tokens* selects."""
    for index, word in enumerate(tokens):
        if _trim(word).rpartition("/")[2] != "rustup":
            continue
        rest: list[str] = []
        for following in tokens[index + 1 :]:
            current = _trim(following)
            if current in _SHELL_SEPARATORS:
                break
            rest.append(current)
        yield from _rustup_arguments(rest)


def command_selections(text: str) -> list[Selection]:
    """Return each toolchain a command text selects, as written.

    The four routes are cargo's `+toolchain` override, a `RUSTUP_TOOLCHAIN=`
    assignment, rustup's `--toolchain` option, and rustup's positional
    toolchain argument. A value that is a reference is returned as one, so the
    caller can decide whether the reference is a declaration.

    Returns
    -------
    list[Selection]
        The selected toolchains, in the order the text writes them.
    """
    tokens = _tokens(text)
    return [
        *_overrides(tokens),
        *_environment(tokens),
        *_option_values(tokens),
        *_rustup(tokens),
    ]


def _literals_in(text: str) -> list[str]:
    """Return the dated nightlies *text* writes."""
    return [match.group(0) for match in DATED_NIGHTLY.finditer(text)]


def _scalar_texts(value: object) -> cabc.Iterator[str]:
    """Yield every scalar in a decoded YAML node as text."""
    # `bool()` must precede `int()`: YAML decodes `true` to a bool, and
    # `isinstance(True, int)` holds, so a later branch would render it as 1.
    match value:
        case bool():
            yield str(value).lower()
        case str() | int() | float():
            yield str(value)
        case dict():
            for key, item in typ.cast("dict[object, object]", value).items():
                yield from _scalar_texts(key)
                yield from _scalar_texts(item)
        case list():
            for item in typ.cast("list[object]", value):
                yield from _scalar_texts(item)


def _env_literals(node: object) -> frozenset[str]:
    """Return the dated nightlies a mapping's `env` table declares."""
    if not isinstance(node, dict):
        return frozenset()
    env = node.get("env")
    return frozenset(
        literal for text in _scalar_texts(env) for literal in _literals_in(text)
    )


def _steps(job: object) -> cabc.Iterator[dict[str, object]]:
    """Yield every step mapping of one job."""
    steps = job.get("steps") if isinstance(job, dict) else None
    for step in steps if isinstance(steps, list) else []:
        if isinstance(step, dict):
            yield typ.cast("dict[str, object]", step)


def _step_literals(step: dict[str, object]) -> list[str]:
    """Return the dated nightlies one step selects.

    A `run` script is a command, so its toolchain comes from the routes
    :func:`command_selections` knows. An action's `toolchain` input and its
    `@ref` are values in their own right: a date written there is the
    toolchain even though no command names it.

    Returns
    -------
    list[str]
        The dated nightlies the step selects.
    """
    literals: list[str] = []
    run = step.get("run")
    if isinstance(run, str):
        literals.extend(_command_literals(run))
    uses = step.get("uses")
    if isinstance(uses, str) and uses.startswith(TOOLCHAIN_ACTIONS):
        literals.extend(_literals_in(uses.rpartition("@")[2]))
        inputs = step.get("with")
        toolchain = inputs.get("toolchain") if isinstance(inputs, dict) else None
        if isinstance(toolchain, str):
            literals.extend(_literals_in(toolchain))
    return literals


def _workflow_literals(document: object, path: str) -> list[LiteralFacts]:
    """Return the literals one decoded workflow selects.

    Returns
    -------
    list[LiteralFacts]
        The literals the workflow names outside what its `env` tables declare.
    """
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, dict):
        return []
    document_declared = _env_literals(document)
    found: list[LiteralFacts] = []
    for job in jobs.values():
        job_declared = document_declared | _env_literals(job)
        for step in _steps(job):
            declared = job_declared | _env_literals(step)
            found.extend(
                {"path": path, "kind": KIND_WORKFLOW, "text": literal}
                for literal in _step_literals(step)
                if literal not in declared
            )
    return found


def _command_literals(text: str) -> list[str]:
    """Return the dated nightlies *text* selects as a toolchain."""
    return [
        literal
        for selection in command_selections(text)
        if not selection.is_reference
        for literal in _literals_in(selection.text)
    ]


def _root_makefile(checkout: pathlib.Path) -> pathlib.Path | None:
    """Return the root Makefile Make would read, or None when there is none."""
    for name in _MAKEFILE_NAMES:
        path = checkout / name
        if is_file(path, OPERATION_READ_MAKEFILE):
            return path
    return None


def _reference_name(text: str) -> str | None:
    """Return the variable a `$(NAME)`/`${NAME}` text names, if it is one."""
    match = _REFERENCE.match(text)
    return (match.group("paren") or match.group("brace")) if match else None


def _reported_texts(selection: Selection, table: VariableTable) -> list[str]:
    """Return the texts to report for one recipe selection; empty when declared.

    A variable assigned the date itself, at the name the recipe writes, is a
    deliberate separate pin, as cuprum's `DEV_FAST_TOOLCHAIN` is. A recipe
    that reaches the date through another variable is not: the date is not
    declared where it is used, so it is reported at its expanded value. A
    reference to a name the Makefile never assigns is reported as written,
    because nothing declares it. When the Makefile includes other files, a
    name the report does not carry may be assigned by one of them, so it is
    treated as declared rather than guessed at.

    Returns
    -------
    list[str]
        The literal texts to report, or an empty list when the selection is
        declared as deliberately separate.
    """
    if not selection.is_reference:
        return _literals_in(selection.text)
    name = _reference_name(selection.text)
    if name is None:
        return []
    written = table.values.get(name)
    if written is not None and _literals_in(shown(written)):
        return []
    if written is None and (name in table.assigned or table.has_includes):
        return []
    return _expanded_literals(selection, table)


def _expanded_literals(selection: Selection, table: VariableTable) -> list[str]:
    """Return the literals a reference expands to, or the reference itself."""
    expanded = shown(expand(selection.text, table.values).text).strip()
    literals = _literals_in(expanded)
    if literals:
        return literals
    if not expanded.startswith("$"):
        return []
    name = _reference_name(expanded)
    if name is None or name in table.assigned or table.has_includes:
        return []
    return [expanded]


def _makefile_literals(
    checkout: pathlib.Path, inspect: MakefileInspector
) -> tuple[list[LiteralFacts], LiteralError | None]:
    """Return the literals the root Makefile's recipes select.

    The recipes are read as written, so a reference is still a reference and
    can be judged against the variable table. The value a reference expands to
    is used only for the finding's text.

    Returns
    -------
    tuple[list[LiteralFacts], LiteralError | None]
        The literals, and why the Makefile could not be read (None when it read
        cleanly, or when the checkout has no Makefile).
    """
    makefile = _root_makefile(checkout)
    if makefile is None:
        return [], None
    try:
        report = inspect(makefile).report
    except MakefileRefusedError as error:
        return [], {"path": makefile.name, "reason": str(error)}
    except OperationalRuleError as error:
        # An absent or unusable `makeutil` is a deployment fault that stops
        # every other package too. This one reports it as indeterminate rather
        # than making the package unusable, so a repository cannot silence
        # RT-028 by taking the tool away.
        return [], {"path": makefile.name, "reason": str(error)}
    values = single_assignments(report["variables"])
    table = VariableTable(
        values=values,
        assigned=values.assigned,
        has_includes=bool(report["includes"]),
    )
    return (
        [
            {"path": makefile.name, "kind": KIND_MAKEFILE, "text": text}
            for rule in report["rules"]
            for recipe in rule["recipes"]
            for selection in command_selections(shown(recipe["text"]))
            for text in _reported_texts(selection, table)
        ],
        None,
    )


def toolchain_literal_facts(
    checkout: pathlib.Path, *, inspect: MakefileInspector = inspect_makefile
) -> ToolchainLiteralFacts:
    """Read the dated nightlies the checkout's workflows and Makefile select.

    Parameters
    ----------
    checkout:
        Path to the local checkout to audit.
    inspect:
        Reads the root Makefile's facts. The default is the pure query; the
        command boundary injects an observing one.

    Returns
    -------
    ToolchainLiteralFacts
        The literals, whether the checkout pins its toolchain, and the files
        that could not be read.
    """
    toolchain = inspect_toolchain(checkout)
    if (
        toolchain is None
        or toolchain["parse_error"] is not None
        or not isinstance(toolchain["channel"], str)
    ):
        # Without a pin there is nothing for a literal to bypass, and RT-022
        # already reports the pin that is missing or unreadable.
        return {"reads_pin": False, "literals": [], "errors": []}
    errors: list[LiteralError] = []
    literals: list[LiteralFacts] = []
    for workflow in load_workflows(checkout, resolved_root(checkout)):
        if workflow["error"] is None:
            literals.extend(_workflow_literals(workflow["parsed"], workflow["path"]))
        else:
            errors.append({"path": workflow["path"], "reason": workflow["error"]})
    makefile_literals, makefile_error = _makefile_literals(checkout, inspect)
    literals.extend(makefile_literals)
    if makefile_error is not None:
        errors.append(makefile_error)
    return {"reads_pin": True, "literals": literals, "errors": errors}
