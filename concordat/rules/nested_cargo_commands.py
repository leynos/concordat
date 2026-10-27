"""Read the cargo commands a Rust source builds, and the network they reach.

The nested-cargo rule needs to know which cargo runs a test starts and whether
each one can reach the network. The compiler alone decides that, so a command
is read from masked text — where a literal cannot look like a call — and its
arguments and environment from the original text, where the literal is still
there to be read.

A command is `offline` only when it carries `--frozen`, carries both
`--offline` and `--locked`, or sets `CARGO_NET_OFFLINE` to a value cargo reads
as true (`"1"` or `"true"`) itself. A command whose argument or environment
source is not a literal is `unresolved`, because the reader cannot see what it
passes — including a `CARGO_NET_OFFLINE` name whose value is not a literal. A
literal value that cargo reads as false is visible evidence that the command
may use the network, so it stays `online` rather than becoming unreadable.
Every other literal cargo command is `online`. The reader never reports
`offline` on a guess and never reports `online` on one.

`Command::env` keeps the last value given for a name, so a `CARGO_NET_OFFLINE`
assignment replaces any earlier one rather than adding to it. The reader
therefore tracks the *latest* assignment, including a call site's own
assignment over a helper's, and reports `offline` only when that last
assignment is still one cargo reads as true. `--frozen` is not revocable this
way: no argument and no environment variable turns it off, so it remains
evidence whatever an environment assignment says.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from .nested_cargo_source import RustSource, enclosing_block
from .rust_source import scan_balanced, skip_trivia

OFFLINE_VARIABLE: typ.Final = "CARGO_NET_OFFLINE"
# Cargo reads this variable through its boolean parser, which accepts only `1`
# and `true`. Measured against cargo 1.99.1: `1` reported the offline-mode
# note that `--offline` prints, while `yes`, `on`, an empty value and `0` did
# not, so each of those leaves the network reachable.
_OFFLINE_VALUES: typ.Final = frozenset({"1", "true"})
# Flags that ask cargo, or the plugin it dispatches to, to identify itself or
# to print help. Both print and exit; neither builds anything, so neither can
# take the package-cache lock the rule exists to protect.
_PROBE_FLAGS: typ.Final = frozenset({"--version", "-V", "--help", "-h"})
ARGUMENT_METHODS: typ.Final = frozenset({"arg", "args"})
ENVIRONMENT_METHODS: typ.Final = frozenset({"env", "envs"})
_PUNCTUATION: typ.Final = frozenset(" \t\r\n,[]()&")

COMMAND_NEW: typ.Final = re.compile(r"\bCommand\s*::\s*new\s*\(")
_RETURN_COMMAND: typ.Final = re.compile(r"->\s*(?:[A-Za-z_]\w*\s*::\s*)*Command\b")
_IDENTIFIER: typ.Final = re.compile(r"[A-Za-z_]\w*")
_ENV_MACRO: typ.Final = re.compile(r"(?:option_)?env!\s*\(")
_ENV_LOOKUP: typ.Final = re.compile(r"(?:(?:std\s*::\s*)?env\s*::\s*)?var(?:_os)?\s*\(")
_FIRST_STRING: typ.Final = re.compile(r'"((?:[^"\\]|\\.)*)"')
# The window ends at the `Command::new` call, so a path-qualified
# construction leaves `std::process::` between the `=` and the window's end.
# Accepting only whitespace there would miss the binding, and the `cmd.arg`
# calls collected through the name would be lost: a command that does carry
# `--frozen` would be read as online.
_LET_BINDING: typ.Final = re.compile(
    r"\blet\s+(?:mut\s+)?(?P<name>[A-Za-z_]\w*)\s*(?::[^=;]*)?=\s*"
    r"(?:::)?\s*(?:[A-Za-z_]\w*\s*::\s*)*$"
)


def _rebinding(name: str) -> re.Pattern[str]:
    """Return a pattern matching a later `let` that replaces *name*'s value.

    Returns
    -------
    re.Pattern[str]
        The pattern, anchored on the binding rather than on a line end.
    """
    return re.compile(rf"\blet\s+(?:mut\s+)?{re.escape(name)}\s*(?::[^=;]*)?=")


@dataclasses.dataclass(slots=True)
class CargoCommand:
    """One cargo command under construction, and the evidence it carries."""

    start: int
    form: str
    words: list[str | None] = dataclasses.field(default_factory=list)
    environment: list[str] = dataclasses.field(default_factory=list)
    # The latest `CARGO_NET_OFFLINE` assignment: True for a value cargo reads as
    # offline, False for one it does not, None for a value the reader cannot
    # see. `has_offline_override` separates "the last assignment said online"
    # from "nothing has assigned the variable at all".
    offline_override: bool | None = None
    has_offline_override: bool = False
    is_uncertain: bool = False

    def add_arguments(self, text: str) -> None:
        """Record `.arg(..)`/`.args(..)` text; an unreadable source is unknown."""
        words = _string_literals(text)
        if words is None:
            self.words.append(None)
            return
        self.words.extend(words)

    def add_environment(self, text: str) -> None:
        """Record `.env(..)`/`.envs(..)` text and act on `CARGO_NET_OFFLINE`.

        The arguments are read positionally so that an unreadable value does not
        taint an unrelated name. `.env("CARGO_TARGET_DIR", directory)` says
        nothing about the network and must not make the command `unresolved`,
        while `.env(name, "true")` genuinely does hide whether the offline
        variable is set.

        A visible `CARGO_NET_OFFLINE` assignment replaces the last one, because
        that is what `Command::env` does with a repeated name.
        """
        pairs = _environment_pairs(text)
        if pairs is None:
            self.is_uncertain = True
            return
        for key, value in pairs:
            if key is None:
                # The name is what decides whether this pair matters, so an
                # unreadable one leaves the offline question open.
                self.is_uncertain = True
                continue
            self.environment.append(key)
            if key != OFFLINE_VARIABLE:
                continue
            # The last assignment is the one cargo reads, so each visible pair
            # replaces the previous answer rather than adding to it.
            self.offline_override = value in _OFFLINE_VALUES
            self.has_offline_override = True
            if value is None:
                # The name is visible but the value is not, so whether this
                # pair turns offline mode on is genuinely unknown.
                self.offline_override = None

    def absorb(self, definition: CargoCommand) -> None:
        """Take on the evidence a helper's own body establishes.

        A call site's own assignment runs after the helper returns, so it wins
        over the helper's: the helper's answer is taken only where the site
        says nothing about the variable itself.
        """
        self.words[:0] = definition.words
        self.environment[:0] = definition.environment
        if not self.has_offline_override:
            self.offline_override = definition.offline_override
            self.has_offline_override = definition.has_offline_override
        self.is_uncertain |= definition.is_uncertain


def _string_end(text: str, index: int) -> int:
    """Return the index of the quote closing the string whose body starts here."""
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index
        index += 1
    return len(text)


def first_literal(text: str) -> str | None:
    """Return the first double-quoted string's contents in *text*, or None."""
    found = _FIRST_STRING.search(text)
    return found.group(1) if found is not None else None


def _string_literals(text: str) -> list[str] | None:
    """Return every double-quoted string in *text*, or None if it has another token.

    `["--offline", "--locked"]`, `&["build"]` and `("K", "V")` are all literal
    sources. An identifier, a call or a macro makes the source one the reader
    cannot settle, which is answered with `None` rather than a guess.

    Returns
    -------
    list[str] | None
        The literals in source order, or None when anything else appears.
    """
    words: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            end = _string_end(text, index + 1)
            if end >= len(text):
                return None
            words.append(text[index + 1 : end])
            index = end + 1
            continue
        if char in _PUNCTUATION:
            index += 1
            continue
        return None
    return words


def _environment_pairs(text: str) -> list[tuple[str | None, str | None]] | None:
    """Return a `.env`/`.envs` argument list as name-value pairs, or None.

    Each slot is read on its own. A name the reader can see is reported even
    when the paired value is opaque, because only the name decides whether the
    pair matters: `.env("CARGO_TARGET_DIR", computed)` cannot make a command
    reach the network, while `.env(key, computed)` leaves the question open.

    Returns
    -------
    list[tuple[str | None, str | None]] | None
        One pair per variable, or None when the argument list is not a readable
        list of pairs at all.
    """
    groups = _top_level_groups(text)
    if groups is None:
        return None
    pairs: list[tuple[str | None, str | None]] = []
    for group in groups:
        slots = split_top_level(_unwrap(group))
        # rustfmt adds a trailing comma when it breaks a call over several
        # lines, so `.env(\n    "K",\n    "V",\n)` arrives as three slots with
        # a blank last one. Counting that as a third argument rejects a pair
        # the reader can see perfectly well, which both hides real offline
        # evidence and reports the command `unresolved`.
        if slots and not slots[-1].strip():
            slots.pop()
        if len(slots) > 2:
            return None
        name = _string_literals(slots[0]) if slots else None
        value = _string_literals(slots[1]) if len(slots) == 2 else None
        pairs.append((name[0] if name else None, value[0] if value else None))
    return pairs


def _unwrap(text: str) -> str:
    """Return *text* with one enclosing bracket pair and any `&` removed.

    `("K", "V")`, `["K", "V"]` and `&("K", "V")` all describe the same pair.
    Anything else is returned unchanged.

    Returns
    -------
    str
        The text inside the brackets, or *text* itself.
    """
    stripped = text.strip().removeprefix("&").strip()
    if not stripped or stripped[0] not in "([":
        return stripped
    close = scan_balanced(stripped, 0)
    if close is None or stripped[close - 1] != ("]" if stripped[0] == "[" else ")"):
        return stripped
    if stripped[close:].strip():
        return stripped
    return stripped[1 : close - 1]


def _top_level_groups(text: str) -> list[str] | None:
    """Split an argument list into its outermost name-value groups.

    `"K", "V"` is one group. `[("K", "V"), ("J", "W")]` is two. A group may
    still turn out to be unreadable, which the caller decides slot by slot.

    Returns
    -------
    list[str] | None
        The groups, or None when the bracket structure does not balance.
    """
    stripped = text.strip().removeprefix("&").strip()
    if not stripped:
        return []
    if stripped[0] != "[":
        return [stripped]
    close = scan_balanced(stripped, 0)
    if close is None:
        return None
    return [
        group for group in split_top_level(stripped[1 : close - 1]) if group.strip()
    ]


def _record(command: CargoCommand, method: str, text: str) -> None:
    """Record one collected call's argument text against *command*."""
    if method in ARGUMENT_METHODS:
        command.add_arguments(text)
    elif method in ENVIRONMENT_METHODS:
        command.add_environment(text)


def follow_chain(source: RustSource, command: CargoCommand, index: int) -> int:
    """Collect `.arg`/`.args`/`.env`/`.envs` calls from *index*, returning the end.

    The call shapes are found in the masked text, where a literal cannot look
    like a call, and their arguments are read from the original text, where the
    literal is still there to be read.

    Returns
    -------
    int
        The index after the last chained call the reader consumed.
    """
    masked = source.masked
    while True:
        index = skip_trivia(masked, index)
        if index >= len(masked) or masked[index] != ".":
            return index
        name = _IDENTIFIER.match(masked, skip_trivia(masked, index + 1))
        if name is None:
            return index
        after = skip_trivia(masked, name.end())
        if after >= len(masked) or masked[after] != "(":
            index = name.end()
            continue
        close = scan_balanced(masked, after)
        if close is None:
            return index
        _record(command, name.group(), source.text[after + 1 : close - 1])
        index = close


def _cargo_form(masked_argument: str, original_argument: str) -> str | None:
    """Return the spawn form the `Command::new` argument names, or None.

    Returns
    -------
    str | None
        `command_new`, `env_cargo`, or None when the program is not cargo.
    """
    stripped = masked_argument.strip()
    if _string_literals(original_argument) == ["cargo"]:
        return "command_new"
    if _ENV_MACRO.match(stripped) and first_literal(original_argument) == "CARGO":
        return "env_cargo"
    if _ENV_LOOKUP.match(stripped) and first_literal(original_argument) == "CARGO":
        return "env_cargo"
    return None


def _argument_span(masked: str, match: re.Match[str]) -> tuple[int, int]:
    """Return the span of a `Command::new` call's argument text.

    Returns
    -------
    tuple[int, int]
        The start and end offsets of the text inside the argument list.
    """
    open_paren = match.end() - 1
    close = scan_balanced(masked, open_paren)
    if close is None:
        return open_paren + 1, match.end()
    return open_paren + 1, close - 1


def bound_name(masked: str, offset: int) -> str | None:
    """Return the variable a construction at *offset* initialises, if any."""
    match = _LET_BINDING.search(masked, max(0, offset - 300), offset)
    return match.group("name") if match is not None else None


def collect_variable(
    source: RustSource, command: CargoCommand, name: str, start: int, end: int
) -> None:
    """Collect the calls a bound variable receives before it is rebound.

    A second `let` of the same name starts a new value, so a call after it
    belongs to that value rather than this one. Reading past the rebinding
    would credit this command with a flag set on another one — here, an
    offline flag on a command that is not cargo at all.
    """
    rebinding = _rebinding(name).search(source.masked, start, end)
    if rebinding is not None:
        end = rebinding.start()
    pattern = re.compile(rf"\b{re.escape(name)}\s*\.")
    for match in pattern.finditer(source.masked, start, end):
        follow_chain(source, command, match.end() - 1)


def construction(
    source: RustSource, match: re.Match[str], pairs: list[tuple[int, int]]
) -> CargoCommand | None:
    """Return the command one `Command::new` call builds, or None if not cargo.

    Returns
    -------
    CargoCommand | None
        The command with its chain and bound-variable calls collected.
    """
    start, end = _argument_span(source.masked, match)
    form = _cargo_form(source.masked[start:end], source.text[start:end])
    if form is None:
        return None
    command = CargoCommand(start=match.start(), form=form)
    after = follow_chain(source, command, end + 1)
    block = enclosing_block(pairs, match.start())
    name = bound_name(source.masked, match.start())
    if block is not None and name is not None:
        collect_variable(source, command, name, after, block[1])
    return command


def has_cargo_construction(source: RustSource, start: int, end: int) -> bool:
    """Report whether a span holds a cargo `Command::new` call."""
    for match in COMMAND_NEW.finditer(source.masked, start, end):
        left, right = _argument_span(source.masked, match)
        if _cargo_form(source.masked[left:right], source.text[left:right]) is not None:
            return True
    return False


def helper_body(masked: str, match: re.Match[str]) -> tuple[str, int, int] | None:
    """Return a `fn ... -> Command` declaration's name and body span, if it is one.

    Returns
    -------
    tuple[str, int, int] | None
        The function name and the body's `(open, end)` span, or None.
    """
    close = scan_balanced(masked, match.end() - 1)
    if close is None:
        return None
    after = skip_trivia(masked, close)
    if not masked.startswith("->", after):
        return None
    returned = _RETURN_COMMAND.match(masked, after)
    if returned is None:
        return None
    start = skip_trivia(masked, returned.end())
    if start >= len(masked) or masked[start] != "{":
        return None
    end = scan_balanced(masked, start)
    if end is None:
        return None
    return match.group("name"), start, end


def unreached_helper(definition: CargoCommand) -> CargoCommand:
    """Return a helper definition marked as a spawn with an unreadable call site.

    A helper's call sites are looked up in the file that defines it, so a helper
    another file calls has none the reader can read. Whatever that call adds —
    an offline flag, an offline environment, neither — is invisible, and
    guessing either way would report a state the reader cannot establish.

    Returns
    -------
    CargoCommand
        The definition, recorded as a `helper` spawn with an unreadable
        argument list so that `command_state` answers `unresolved`.
    """
    definition.form = "helper"
    definition.words.append(None)
    return definition


def command_state(command: CargoCommand) -> str | None:
    """Return the evidence state of *command*, or None when it cannot resolve.

    Offline evidence is only read from the words that precede any argument the
    reader could not see. An unreadable word may be `--`, which ends cargo's own
    flag list and hands every later word to the program it runs, so
    `cargo .args(extra).arg("--frozen")` is not proven offline by that
    `--frozen`. The reverse order is sound: cargo has already parsed a flag
    that comes before the unknown word.

    Returns
    -------
    str | None
        `offline`, `online`, `unresolved`, or None for a version/help probe.
    """
    known = [word for word in command.words if isinstance(word, str)]
    if len(known) == len(command.words) and _is_probe(known):
        return None
    # The flags are independent of the environment: nothing the command can set
    # later revokes `--frozen`, so they prove offline mode on their own.
    if _offline_flags(_literal_prefix(command.words)):
        return "offline"
    if command.has_offline_override and command.offline_override is True:
        return "offline"
    if (
        command.is_uncertain
        or (command.has_offline_override and command.offline_override is None)
        or len(known) != len(command.words)
    ):
        return "unresolved"
    return "online"


def _literal_prefix(words: list[str | None]) -> list[str]:
    """Return the words that occur before the first unreadable argument.

    Returns
    -------
    list[str]
        The literal prefix, empty when the first word is already unreadable.
    """
    prefix: list[str] = []
    for word in words:
        if word is None:
            break
        prefix.append(word)
    return prefix


def _is_probe(words: list[str]) -> bool:
    """Report whether a fully literal argument list only asks for version or help.

    Cargo prints its own version and exits for `cargo --version`. A subcommand
    it does not own is dispatched to a `cargo-<name>` plugin, which prints its
    own version for `cargo <name> --version`; theoremc probes for cargo-kani
    exactly that way. A built-in subcommand given `--version` rejects the flag
    and exits. None of these reads a manifest, a lock file or the network, so
    the probe is not a spawn the rule can judge.

    A version flag that carries a value is the exception: `cargo install
    --version 1.2.3` and `cargo add --version 1.2.3` are real work that reaches
    the registry, and excluding them would hide exactly the spawns this audit
    exists to find.

    `help` counts only as the first word. `cargo help` is cargo's own help,
    but `cargo run help` hands `help` to the binary under test and builds it
    first.

    Returns
    -------
    bool
        True when the command only asks cargo or a plugin who it is.
    """
    if words[:1] == ["help"]:
        return True
    # `help` anywhere else is an argument to the program cargo was asked to
    # run, so only the flags are worth scanning. A probe flag carries a value
    # only as the last word of its own option, so a following word that is not
    # another flag is that value and the command is real work.
    flags = words[: words.index("--")] if "--" in words else words
    return any(
        word in _PROBE_FLAGS
        and (index + 1 == len(flags) or flags[index + 1].startswith("-"))
        for index, word in enumerate(flags)
    )


def _offline_flags(known: list[str]) -> bool:
    """Report whether a command's literal flags prove it cannot reach the network.

    Words after `--` belong to the program cargo runs, not to cargo, so
    `cargo run -- --offline --locked` is not offline evidence: those flags
    reach the binary under test while cargo itself may still resolve crates.

    The caller passes only the words before the first unreadable argument;
    see `command_state` for why a later flag cannot be trusted.

    Parameters
    ----------
    known:
        The literal words of the argument list, in source order, ending at the
        first argument the reader could not read.

    Returns
    -------
    bool
        True when cargo's own flags rule the network out.
    """
    flags = known[: known.index("--")] if "--" in known else known
    return "--frozen" in flags or ("--offline" in flags and "--locked" in flags)


def split_top_level(text: str) -> list[str]:
    """Split on commas outside nested parentheses and string literals.

    A comma inside a quoted value is data, not a separator. Splitting on it
    turns `.env("RUST_LOG", "info,foo=debug")` — one name-value pair — into
    three slots, which cannot be read as pairs at all. The command is then
    reported `unresolved` and the NC-007 finding its real flags earned is
    hidden, so the quote is tracked here rather than left to the caller.

    Returns
    -------
    list[str]
        The comma-separated parts, in source order.
    """
    parts: list[str] = []
    depth, start = 0, 0
    index = 0
    is_quoted = False
    while index < len(text):
        char = text[index]
        if is_quoted:
            # A backslash escapes the next character, so the quote after it
            # does not close the literal: `"a\"b"` is one value.
            if char == "\\":
                index += 2
                continue
            if char == '"':
                is_quoted = False
        elif char == '"':
            is_quoted = True
        else:
            depth += (char == "(") - (char == ")")
            if char == "," and depth == 0:
                parts.append(text[start:index])
                start = index + 1
        index += 1
    parts.append(text[start:])
    return parts
