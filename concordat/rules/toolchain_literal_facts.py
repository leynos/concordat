"""Read the dated toolchain literals a workflow or Makefile selects, as data.

RT-028 asks whether CI or a build file restates the date of the nightly that
`rust-toolchain.toml` pins. A restated date drifts the moment Dependabot moves
the pin, so the question is "which dated nightlies does this file select", not
"does this date appear in the text".

Five decisions are made here rather than in the policy:

* workflows are walked as decoded documents. A nightly in a comment or in a
  step's display name is not CI using it. Only the places a workflow selects a
  toolchain are read: a step's `run` command, a toolchain action's `toolchain`
  input, and the action's own ref. A `run` script is split into shell commands,
  so `echo "cargo +nightly-..."` prints a date rather than selecting it, and a
  `${{ matrix.NAME }}` value is resolved to every leg the matrix declares.
* a workflow value a command reaches through `${{ env.NAME }}` is a
  deliberately separate pin, and is not reported, because the file binds the
  date to a name. An inline date that happens to equal an `env` entry is still
  reported: the file writes the date twice, so the second copy can drift.
* a Makefile is read from the `makeutil` report, because a recipe is what Make
  runs. A variable whose whole value is the date, at the name the recipe
  writes, is a deliberate separate pin; a variable that merely contains a date
  in a command is not. A literal written inline is reported; a date reached
  through another variable is reported at its expanded value; and a command
  that is itself a variable is expanded so its selections are read. A
  reference the Makefile never assigns, where no included file could, leaves
  the toolchain unknown rather than dated, so it is carried as an error.
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
from .makefile_facts import (
    MakefileInspector,
    MakefileRefusedError,
    MakeutilReport,
    inspect_makefile,
)
from .markdown_envelope import (
    OPERATION_READ_MAKEFILE,
    is_file,
    load_workflows,
    resolved_root,
)
from .toolchain_literal_scan import shell_commands
from .toolchain_literal_shell import (
    Selection,
    command_selections,
    program_position,
    reference_name,
    selection_in,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib

    from .toolchain import ToolchainFacts

# A dated nightly, with any target-triple suffix rustup accepts
# (`nightly-2026-10-30-x86_64-unknown-linux-gnu`). The whole name is reported:
# it is the toolchain the file selected.
DATED_NIGHTLY: typ.Final = re.compile(
    r"(?<![\w-])nightly-\d{4}-\d{2}-\d{2}(?:-[A-Za-z0-9_]+)*(?![\w-])"
)
# A workflow expression naming an `env` or `matrix` value, and any expression.
_WORKFLOW_REFERENCE: typ.Final = re.compile(
    r"^\$\{\{\s*(?P<scope>env|matrix)\.(?P<name>[\w-]+)\s*\}\}$"
)
_EXPRESSION: typ.Final = re.compile(r"\$\{\{\s*(?P<body>[^}]*?)\s*\}\}")
# GNU Make reads these in this order, so the first present one is the file the
# repository's recipes run from.
_MAKEFILE_NAMES: typ.Final = ("GNUmakefile", "makefile", "Makefile")
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
        The dated nightly as written. A reference that expands to one is
        reported at its expanded value, so this is always a date.
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


def _literals_in(text: str) -> list[str]:
    """Return the dated nightlies *text* writes."""
    return [match.group(0) for match in DATED_NIGHTLY.finditer(text)]


def _steps(job: object) -> cabc.Iterator[dict[str, object]]:
    """Yield every step mapping of one job."""
    steps = job.get("steps") if isinstance(job, dict) else None
    for step in steps if isinstance(steps, list) else []:
        if isinstance(step, dict):
            yield typ.cast("dict[str, object]", step)


def _scalar_text(value: object) -> str | None:
    """Return a decoded YAML scalar as the text rustup would see, or None."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    return str(value)


def _include_values(entries: cabc.Sequence[object]) -> dict[str, list[str]]:
    """Return the values a matrix's `include` legs give each key.

    An `include` entry adds keys a job's `matrix.toolchain` reference can
    name, so they have to be read as thoroughly as an axis.

    Returns
    -------
    dict[str, list[str]]
        The values the `include` legs give each key.
    """
    pairs = [
        (str(name), text)
        for entry in entries
        if isinstance(entry, dict)
        for name, item in entry.items()
        if (text := _scalar_text(item))
    ]
    values: dict[str, list[str]] = {}
    for name, text in pairs:
        values.setdefault(name, []).append(text)
    return values


def _matrix_values(job: object) -> dict[str, list[str]]:
    """Map each matrix key to every scalar it takes, `include` legs too.

    A job may produce its toolchain from the matrix rather than name it, so
    the value a `${{ matrix.toolchain }}` reference can take has to be read
    from every leg. An axis that is not a list contributes nothing.

    Returns
    -------
    dict[str, list[str]]
        The values each matrix key can take.
    """
    strategy = job.get("strategy") if isinstance(job, dict) else None
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict):
        return {}
    values: dict[str, list[str]] = {}
    for key, listed in matrix.items():
        entries = listed if isinstance(listed, list) else []
        for name, texts in _axis_values(str(key), entries).items():
            # An `include` leg that the axis does not carry adds a value
            # GitHub will still run, so an axis must not replace it.
            values.setdefault(name, []).extend(texts)
    return values


def _axis_values(key: str, entries: cabc.Sequence[object]) -> dict[str, list[str]]:
    """Return the values one matrix axis or `include` list contributes.

    Returns
    -------
    dict[str, list[str]]
        Each name the entry gives and every value it takes.
    """
    if key == "include":
        return _include_values(entries)
    texts = [text for item in entries if (text := _scalar_text(item))]
    return {key: texts} if texts else {}


def _resolved(text: str, matrix: cabc.Mapping[str, list[str]]) -> list[str]:
    """Return the values *text* names, following a matrix reference.

    A `${{ env.NAME }}` reference is left as written: binding the date to a
    name is how a workflow declares a deliberately separate pin, so the
    reference is not a restated literal.

    Returns
    -------
    list[str]
        The values *text* can take; just *text* when it is not a matrix
        reference, and nothing when it names a key the matrix does not give.
    """
    reference = _WORKFLOW_REFERENCE.match(text.strip())
    if reference is None or reference.group("scope") != "matrix":
        return [text]
    return matrix.get(reference.group("name"), [])


def _collapsed(text: str) -> str:
    """Return *text* with each `${{ ... }}` expression made one word."""
    return _EXPRESSION.sub(
        lambda match: "${{" + match.group("body").replace(" ", "") + "}}", text
    )


def _command_literals(text: str, matrix: cabc.Mapping[str, list[str]]) -> list[str]:
    """Return the dated nightlies *text* selects as a toolchain."""
    return [
        literal
        for selection in command_selections(_collapsed(text))
        if not selection.is_reference
        for value in _resolved(selection.text, matrix)
        for literal in _literals_in(value)
    ]


def _step_literals(
    step: dict[str, object], matrix: cabc.Mapping[str, list[str]]
) -> list[str]:
    """Return the dated nightlies one step selects.

    A `run` script is a command, so its toolchain comes from the routes
    :func:`command_selections` knows. An action's `toolchain` input and its
    `@ref` are values in their own right: a date written there is the
    toolchain even though no command names it, and a matrix reference is
    resolved against every leg the job's matrix declares.

    Returns
    -------
    list[str]
        The dated nightlies the step selects.
    """
    literals: list[str] = []
    run = step.get("run")
    if isinstance(run, str):
        literals.extend(_command_literals(run, matrix))
    uses = step.get("uses")
    if isinstance(uses, str) and uses.startswith(TOOLCHAIN_ACTIONS):
        literals.extend(_literals_in(uses.rpartition("@")[2]))
        inputs = step.get("with")
        toolchain = inputs.get("toolchain") if isinstance(inputs, dict) else None
        if isinstance(toolchain, str):
            literals.extend(
                literal
                for value in _resolved(toolchain, matrix)
                for literal in _literals_in(value)
            )
    return literals


def _workflow_literals(document: object, path: str) -> list[LiteralFacts]:
    """Return the literals one decoded workflow selects.

    An `env` entry is not read here. A command that reaches it through
    `${{ env.NAME }}` writes no date, so there is nothing to report; a date
    written inline is reported even when an `env` entry carries the same date,
    because that second copy is the one that drifts.

    Returns
    -------
    list[LiteralFacts]
        The literals the workflow writes where it selects a toolchain.
    """
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, dict):
        return []
    found: list[LiteralFacts] = []
    for job in jobs.values():
        matrix = _matrix_values(job)
        found.extend(
            {"path": path, "kind": KIND_WORKFLOW, "text": literal}
            for step in _steps(job)
            for literal in _step_literals(step, matrix)
        )
    return found


def _is_declared_date(value: str) -> bool:
    """Return whether *value* is a dated nightly and nothing else.

    Only a variable whose whole value is the date declares a separate pin.
    `CHECK = cargo +nightly-... check` contains one, but it is a command, not
    a declaration of which toolchain the build deliberately uses.

    Returns
    -------
    bool
        Whether the whole value is one dated nightly.
    """
    return DATED_NIGHTLY.fullmatch(value.strip()) is not None


def _declares_its_own_date(name: str, table: VariableTable) -> bool:
    """Return whether *name* is declared where the recipe writes it.

    A variable assigned the date itself is a deliberate separate pin, as
    cuprum's `DEV_FAST_TOOLCHAIN` is. A name the table does not carry is
    declared when the Makefile assigns it some other way, or when an included
    file may assign it; neither says the value is a date.

    Returns
    -------
    bool
        Whether the reference names a declared separate pin.
    """
    written = table.values.get(name)
    if written is not None:
        return _is_declared_date(shown(written))
    if name in table.assigned:
        return True
    return table.has_includes


def _reported_texts(
    selection: Selection, table: VariableTable
) -> tuple[list[str], str | None]:
    """Return the texts to report for one recipe selection, and any unknown.

    A variable assigned the date itself, at the name the recipe writes, is a
    deliberate separate pin. A recipe that reaches the date through another
    variable is not: the date is not declared where it is used, so it is
    reported at its expanded value. A reference the Makefile never assigns,
    where no included file could, has a value the reader cannot see: it is
    carried as an unknown reference rather than guessed to be a date.

    Returns
    -------
    tuple[list[str], str | None]
        The literal texts to report, and the reference whose value is unknown.
    """
    if not selection.is_reference:
        return _literals_in(selection.text), None
    name = reference_name(selection.text)
    if name is None:
        return [], None
    if _declares_its_own_date(name, table):
        return [], None
    return _expanded_literals(selection, table)


def _expanded_literals(
    selection: Selection, table: VariableTable
) -> tuple[list[str], str | None]:
    """Return the literals a reference expands to, or the reference if unknown.

    Returns
    -------
    tuple[list[str], str | None]
        The literal texts the reference expands to, and the reference itself
        when it expands to a name whose value is not in the table.
    """
    expanded = shown(expand(selection.text, table.values).text).strip()
    literals = _literals_in(expanded)
    if literals:
        return literals, None
    if not expanded.startswith("$"):
        return [], None
    name = reference_name(expanded)
    if name is None:
        return [], None
    if _declares_its_own_date(name, table):
        return [], None
    return [], expanded


def _expanded_commands(
    words: list[str], table: VariableTable
) -> list[list[str]] | None:
    """Return the commands a variable-named recipe expands to, or None.

    `CHECK = cargo +nightly-... check` makes the recipe's command a variable,
    so the program has to be expanded before its toolchain can be read. A
    variable assigned the date itself is left out of the substitution table,
    so it stays a reference in the reconstructed command and is judged where
    the recipe writes it: that is what keeps `cargo +$(DEV_FAST_TOOLCHAIN)`
    declared rather than reported at its value.

    The expansion is `makeutil`'s text, so it is tokenized as a shell command:
    a quoted override is shell syntax, and a variable may hold several
    commands.

    Returns
    -------
    list[list[str]] | None
        The commands the recipe runs, or None when the program is not a
        reference or expands to nothing.
    """
    position = program_position(words)
    if position is None:
        return None
    index, program = position
    if reference_name(program) is None:
        return None
    substitutable = {
        name: value
        for name, value in table.values.items()
        if not _is_declared_date(shown(value))
    }
    expanded = shown(expand(program, substitutable).text)
    command = " ".join([expanded, *words[index + 1 :]]).strip()
    return shell_commands(command) if command else None


def _selection_texts(
    selections: cabc.Iterable[Selection], table: VariableTable
) -> tuple[list[str], list[str]]:
    """Return the texts to report for *selections* and the references left unknown.

    Returns
    -------
    tuple[list[str], list[str]]
        The literal texts to report, and the references whose value the
        Makefile never supplies.
    """
    texts: list[str] = []
    unknown: list[str] = []
    for selection in selections:
        found, missing = _reported_texts(selection, table)
        texts.extend(found)
        if missing is not None:
            unknown.append(missing)
    return texts, unknown


def _recipe_texts(text: str, table: VariableTable) -> tuple[list[str], list[str]]:
    """Return the literal texts one recipe line selects, and its unknowns.

    Returns
    -------
    tuple[list[str], list[str]]
        The literal texts to report, and the references whose value the
        Makefile never supplies.
    """
    raw = shown(text)
    texts, unknown = _selection_texts(command_selections(raw), table)
    for words in shell_commands(raw):
        for command in _expanded_commands(words, table) or []:
            found, missing = _selection_texts(selection_in(command), table)
            unknown.extend(missing)
            texts.extend(value for value in found if value not in texts)
    return texts, unknown


def _makefile_path(
    checkout: pathlib.Path,
) -> tuple[pathlib.Path | None, LiteralError | None]:
    """Return the root Makefile Make would read, or why it cannot be read."""
    try:
        root = checkout.resolve(strict=False)
    except OSError as error:
        return None, {
            "path": "Makefile",
            "reason": f"cannot resolve {checkout}: {error}",
        }
    for name in _MAKEFILE_NAMES:
        path = checkout / name
        try:
            present = is_file(path, OPERATION_READ_MAKEFILE)
        except OperationalRuleError as error:
            # A probe that cannot answer is a fact about this checkout, not a
            # reason to lose the rest of the envelope's evidence.
            return None, {"path": name, "reason": str(error)}
        if not present:
            continue
        try:
            resolved = path.resolve(strict=False)
        except OSError as error:
            return None, {"path": name, "reason": f"cannot resolve {path}: {error}"}
        if not resolved.is_relative_to(root):
            return None, {
                "path": name,
                "reason": f"{name} resolves outside the checkout, to {resolved}",
            }
        return path, None
    return None, None


def _unknown_reference(name: str, reference: str) -> LiteralError:
    """Return the indeterminate finding a recipe's unresolved reference makes."""
    return {
        "path": name,
        "reason": (
            f"a recipe selects {reference}, which the Makefile does not "
            "assign, so the toolchain it names cannot be determined"
        ),
    }


def _makefile_literals(
    checkout: pathlib.Path, inspect: MakefileInspector
) -> tuple[list[LiteralFacts], list[LiteralError]]:
    """Return the literals the root Makefile's recipes select.

    The recipes are read as written, so a reference is still a reference and
    can be judged against the variable table. The value a reference expands to
    is used only for the finding's text.

    Returns
    -------
    tuple[list[LiteralFacts], list[LiteralError]]
        The literals, and each reason the Makefile's selections could not be
        read: a refusal, an incomplete parse, or a reference whose value the
        Makefile never supplies.
    """
    makefile, unreadable = _makefile_path(checkout)
    if makefile is None:
        return [], [] if unreadable is None else [unreadable]
    try:
        facts = inspect(makefile)
    except MakefileRefusedError as refusal:
        return [], [{"path": makefile.name, "reason": str(refusal)}]
    except OperationalRuleError as failure:
        # An absent or unusable `makeutil` is a deployment fault that stops
        # every other package too. This one reports it as indeterminate rather
        # than making the package unusable, so a repository cannot silence
        # RT-028 by taking the tool away.
        return [], [{"path": makefile.name, "reason": str(failure)}]
    report = facts.report
    errors: list[LiteralError] = []
    if facts.status != "complete":
        # A recovered parse still emits facts, but they may be incomplete, so
        # a recipe that selects a dated nightly could be the part it lost.
        errors.append(
            LiteralError(
                path=makefile.name,
                reason=(
                    "makeutil recovered from a parse error, so a recipe that "
                    "selects a dated nightly may be missing"
                ),
            )
        )
    values = single_assignments(report["variables"])
    table = VariableTable(
        values=values,
        assigned=values.assigned,
        has_includes=bool(report["includes"]),
    )
    literals, recipe_errors = _recipe_findings(report, makefile.name, table)
    return literals, [*errors, *recipe_errors]


def _recipe_findings(
    report: MakeutilReport, name: str, table: VariableTable
) -> tuple[list[LiteralFacts], list[LiteralError]]:
    """Return the literals and unknowns every recipe of *report* selects.

    Returns
    -------
    tuple[list[LiteralFacts], list[LiteralError]]
        The literals the recipes select, and the references whose value the
        Makefile never supplies.
    """
    literals: list[LiteralFacts] = []
    errors: list[LiteralError] = []
    for rule in report["rules"]:
        for recipe in rule["recipes"]:
            texts, unknown = _recipe_texts(recipe["text"], table)
            literals.extend(
                {"path": name, "kind": KIND_MAKEFILE, "text": text} for text in texts
            )
            errors.extend(_unknown_reference(name, reference) for reference in unknown)
    return literals, errors


def toolchain_literal_facts(
    checkout: pathlib.Path,
    toolchain: ToolchainFacts | None,
    *,
    inspect: MakefileInspector = inspect_makefile,
) -> ToolchainLiteralFacts:
    """Read the dated nightlies the checkout's workflows and Makefile select.

    Parameters
    ----------
    checkout:
        Path to the local checkout to audit.
    toolchain:
        The pin facts the envelope already read, so this reader cannot see a
        different file from the one RT-022 judges.
    inspect:
        Reads the root Makefile's facts. The default is the pure query; the
        command boundary injects an observing one.

    Returns
    -------
    ToolchainLiteralFacts
        The literals, whether the checkout pins its toolchain, and the files
        that could not be read.
    """
    if not _pins_a_toolchain(toolchain):
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
    makefile_literals, makefile_errors = _makefile_literals(checkout, inspect)
    literals.extend(makefile_literals)
    errors.extend(makefile_errors)
    return {"reads_pin": True, "literals": literals, "errors": errors}


def _pins_a_toolchain(toolchain: ToolchainFacts | None) -> bool:
    """Return whether the envelope read a pinned channel.

    Returns
    -------
    bool
        False when there is no pin fact, it records a parse error, or its
        channel is not a string.
    """
    if toolchain is None:
        return False
    if toolchain["parse_error"] is not None:
        return False
    return isinstance(toolchain["channel"], str)
