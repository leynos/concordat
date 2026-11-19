"""Decide what a command's words mean, from the statements that add them.

The nested-cargo scan finds a spawn, then asks two questions of it: whether a
`cargo` command adds compiling work before it runs, and whether a `rustc`
command only asks the compiler who it is. Both answers come from the statements
this module reads, with a helper's body spliced in at its call site and each
call attributed to the receiver it is called on.

A cargo command's subcommand is the first word its arguments give that is
neither an option nor an option's value, so `cargo help build` reads help as
the subcommand and `build` as its topic. A conditional word cannot hide the
subcommand on the path that skips its block, so a `help` inside an `if` leaves
the unconditional `build` still compiling. A word the reader cannot see leaves
the subcommand open, and then any compiling word before the launch is enough.

`-h` or `--help` makes the command compile nothing at all when it is given on
every path, and each arm of a fully paired choice is read for its own
subcommand and its own help flag, so one arm's `metadata` does not hide the
other arm's `build`. A `--print` query asks the compiler what it is only when
its two halves are adjacent: an unreadable or conditional word between
`--print` and `cfg` may be `link-args`, which builds the crate, so the query is
not read as a probe. A probe flag in a block counts only when every block open
at the flag is also open at the launch; a flag in a sibling block that has
already closed does not. A choice whose arms are fully paired can still cover
every path, whether it is written across statements or on one line: a `-vV` in
the `if` arm and a `-V` in its `else` arm reach the shared launch as a probe
either way. An arm is read with the words given before the branch, so a
`--print` in each arm is completed by a `cfg` after the choice. A short circuit
inside an arm (`query && command.arg("-V")`) can still skip that arm's word, so
the arm does not cover its path.

A word is the text its literal has at run time, so an escape cannot hide a
compiling subcommand from the read. A literal that ends at an escaped quote is
still one literal, so an argument before a later `build` keeps its place as the
subcommand.

A shape the scan cannot attribute -- a helper defined twice, a call reached
through a path or method, a name a `use` item could import (a glob import names
none but could bring any in), a receiver that is a field or a call, a parameter
or binding reassigned or shadowed -- is declined rather than guessed, because a
false positive would demand configuration from a repository that needs none.
The reading is textual: comments are blanked and literal contents kept, and
braces come from the masked source, so a `;` or a `{` in a string does not move
a statement boundary.

:mod:`concordat.rules.nested_cargo_statements` decides which statements belong
to the command and which paths they run on, and
:mod:`concordat.rules.nested_cargo_receivers` decides which calls belong to it;
this module decides what the words mean.
"""

from __future__ import annotations

import re
import typing as typ

from .nested_cargo_handoffs import (
    SHORT_CIRCUIT,
    FileIndex,
    Window,
    _bound_name,
    command_window,
)
from .nested_cargo_receivers import owns_call
from .nested_cargo_statements import (
    ArmChoices,
    Statement,
    _track,
    own_statements,
)
from .nested_cargo_text import argument_items
from .rust_source import scan_balanced

# The `--print` requests that read a table or a path and exit without compiling
# an input. `link-args` and `native-static-libs` are deliberately absent: they
# build the crate first.
QUERY_PRINTS: typ.Final = (
    "sysroot|target-libdir|host-tuple|cfg|target-list|target-cpus|target-features"
    "|code-models|relocation-models|tls-models"
)
_QUERY_SET: typ.Final = frozenset(QUERY_PRINTS.split("|"))
# A version flag, given as the word its literal has at run time. Anything
# computed (`.arg(if c { "--version" } ..)`) is not a known word and so is not
# a probe.
_VERSION_FLAGS: typ.Final = frozenset({"--version", "-V", "-vV", "-Vv"})
# Cargo and rustc print help and exit when given either flag, however much the
# subcommand before it looks like compilation (`cargo build --help`).
_HELP_FLAGS: typ.Final = frozenset({"-h", "--help"})
# One `.arg(..)`/`.args(..)` call, whose words are read in source order. Rust
# allows whitespace around the dot, so `command . arg(..)` is the same call.
_ARGUMENT_CALL: typ.Final = re.compile(r"\.\s*args?\s*\(")
# One string literal, including escaped characters, so an escaped quote does
# not end it early.
_LITERAL: typ.Final = r'"(?:[^"\\]|\\.)*"'
_WORD: typ.Final = re.compile(_LITERAL)
# The escapes a plain Rust string decodes to a single character. Byte strings,
# raw strings and continued lines are not read, because their run-time text is
# not the source text and guessing would misclassify the word.
_SHORT_ESCAPES: typ.Final = {
    "\\": "\\",
    '"': '"',
    "'": "'",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "0": "\0",
}
_HEX_ESCAPE: typ.Final = re.compile(r"[0-9A-Fa-f]{2}")
# A `\u{..}` escape's braces and digits, read after the `u`.
_UNICODE_ESCAPE: typ.Final = re.compile(r"\{([0-9A-Fa-f]{1,6})\}")
# Options cargo reads one value for before the subcommand.
_VALUE_OPTIONS: typ.Final = frozenset({
    "--color",
    "--manifest-path",
    "--config",
    "-C",
    "-Z",
})
SUBCOMMANDS: typ.Final = (
    "build|check|test|run|package|clippy|doc|rustc|install|publish|bench|miri"
    "|nextest|llvm-cov|rustdoc"
)
_COMPILING: typ.Final = frozenset(SUBCOMMANDS.split("|"))


class ConditionalWord(typ.NamedTuple):
    """A word given on only some paths, and the blocks open where it is added.

    *blocks* holds the offsets of the opening braces still open at the call,
    so the arm a word belongs to is known even when two arms sit at the same
    depth. *word* is the literal text, or None when it cannot be read.
    *guarded* says a `&&` or `||` between the arm's opening brace and the call
    can skip it, so the arm does not certainly give the word on its path.
    """

    blocks: tuple[int, ...]
    word: str | None
    guarded: bool


def _decode_escapes(literal: str) -> str | None:
    """Return the run-time text of one string literal's contents, or None.

    The word a command is given is the text the literal has at run time, so an
    escape is decoded before the word is compared with `--version` or `build`:
    otherwise a word written with an escape would read as one the scan does not
    know, and a compiling subcommand could hide behind it. Only the escapes
    that name one character are read, because a byte string, a raw string or a
    continued line changes the text in ways this scan does not model, and a
    shape it cannot decode may be any word at all.

    Returns
    -------
    str | None
        The decoded text, or None when an escape is one the scan does not
        decode.
    """
    text: list[str] = []
    index = 0
    while index < len(literal):
        char = literal[index]
        if char != "\\":
            text.append(char)
            index += 1
            continue
        following = literal[index + 1 : index + 2]
        short = _SHORT_ESCAPES.get(following)
        if short is not None:
            text.append(short)
            index += 2
            continue
        if following == "x":
            digits = literal[index + 2 : index + 4]
            if _HEX_ESCAPE.fullmatch(digits) is None:
                return None
            text.append(chr(int(digits, 16)))
            index += 4
            continue
        if following == "u":
            found = _UNICODE_ESCAPE.match(literal[index + 2 :])
            if found is None:
                return None
            code = int(found[1], 16)
            # Rust rejects a surrogate code point, so a literal holding one
            # cannot be the source of a word and is not guessed at.
            if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                return None
            text.append(chr(code))
            index += 2 + found.end()
            continue
        return None
    return "".join(text)


def _literal_word(item: str) -> str | None:
    """Return the word one argument gives, or None when it is not a literal.

    A word is read only when the whole argument is one string literal, so
    `"--version"` is a word and `format!("--{}", name)` is not; the escapes
    inside the literal are decoded, because the command receives their text.

    Returns
    -------
    str | None
        The literal's run-time text, or None when the argument is not one
        readable string literal.
    """
    if _WORD.fullmatch(item) is None:
        return None
    return _decode_escapes(item[1:-1])


def _argument_words(
    statement: str, masked_statement: str, name: str | None
) -> typ.Iterator[str | None]:
    """Yield the words one statement passes to the command, in order.

    Each `.arg(..)`/`.args(..)` call on the command contributes its arguments in
    source order, so the first word of the whole command is the subcommand that
    runs. A call on another receiver is not read, and a word that is not a
    plain literal is yielded as unreadable rather than guessed.

    Yields
    ------
    str | None
        The literal text of each argument, or None where it is not a literal.
    """
    for found in _ARGUMENT_CALL.finditer(masked_statement):
        if not owns_call(masked_statement, found.start(), name):
            continue
        open_at = found.end() - 1
        close = scan_balanced(masked_statement, open_at)
        if close is None:
            yield None
            return
        yield from (
            _literal_word(item)
            for item in argument_items(statement[open_at + 1 : close - 1])
        )


def _first_positional(words: typ.Iterable[str | ConditionalWord | None]) -> str | None:
    """Return the first word that is neither an option nor an option's value.

    An unreadable word may be the subcommand, so it ends the scan with no
    answer at all -- unless it fills the value slot of the known option before
    it (`["--color", color, "build"]`), where its position alone rules it out.
    A conditional word is not on every path, so it is passed over rather than
    read as the subcommand.

    Returns
    -------
    str | None
        The word's text, an empty string when the command gives no positional
        word, or None when no word before one can be read.
    """
    skip_value = False
    for word in words:
        if skip_value:
            skip_value = False
            continue
        if isinstance(word, ConditionalWord):
            continue
        if word is None:
            return None
        if word in _VALUE_OPTIONS:
            skip_value = True
        elif not word.startswith(("-", "+")):
            return word
    return ""


def _cargo_options[Word: str | ConditionalWord | None](
    words: typ.Iterable[Word],
) -> list[Word]:
    """Return the words cargo itself reads, in order.

    A word after a `--` goes to the program the command runs (`cargo test --
    --help` passes `--help` to the test binary), so it is not an option or a
    subcommand of cargo and is not read here. The word type is kept, so a
    plain path stays plain while a run keeps its conditional words.

    Returns
    -------
    list[Word]
        The words before the first `--`, unreadable and conditional ones
        included.
    """
    options: list[Word] = []
    for word in words:
        if word == "--":
            break
        options.append(word)
    return options


def _launch_of(run: list[Statement]) -> tuple[int, ...] | None:
    """Return the blocks open at the run's launch, or None when it never launches.

    Returns
    -------
    tuple[int, ...] | None
        The launch's blocks, identified by their opening braces.
    """
    for statement in run:
        if statement.launch_blocks is not None:
            return statement.launch_blocks
    return None


def _probe_words(
    run: list[Statement], name: str | None, launch: tuple[int, ...] | None
) -> typ.Iterator[str | ConditionalWord | None]:
    """Yield the words a run gives the command, conditional ones marked.

    Every block open at an unconditional word is also open at the launch, so
    the branch that launches contributes its flags while a sibling block that
    closed earlier does not; comparing the opening offsets rather than their
    number is what tells the two apart. A word behind a `&&` or `||` short
    circuit may be skipped on the path that runs the command, so it is
    conditional too, and a short circuit between an arm's opening brace and
    the call leaves the word guarded: the arm itself may not give it.
    Conditional words are yielded with the blocks open at them rather than
    dropped, so a choice between arms can still be read. A word behind an
    unrelated command's call is not this command's. Braces are counted in the
    masked text.

    Yields
    ------
    str | None | ConditionalWord
        The literal text of each argument, None when it is not a literal the
        scan can read, or a ConditionalWord for one given on only some paths.
    """
    if launch is None:
        return
    for statement in run:
        open_blocks = list(statement.blocks)
        seen = 0
        for found in _ARGUMENT_CALL.finditer(statement.masked):
            _track(
                open_blocks,
                statement.masked[seen : found.start()],
                statement.start + seen,
            )
            seen = found.start()
            if not owns_call(statement.masked, found.start(), name):
                continue
            before = statement.masked[: found.start()]
            conditional = (
                SHORT_CIRCUIT.search(before) is not None
                or tuple(open_blocks) != launch[: len(open_blocks)]
            )
            # A short circuit that sits inside the word's own block can skip
            # it on the path through that block, so the block does not
            # certainly give the word.
            inner = 0
            if open_blocks and open_blocks[-1] >= statement.start:
                inner = open_blocks[-1] - statement.start + 1
            guarded = conditional and SHORT_CIRCUIT.search(before, inner) is not None
            open_at = found.end() - 1
            close = scan_balanced(statement.masked, open_at)
            if close is None:
                return
            words = [
                _literal_word(item)
                for item in argument_items(statement.text[open_at + 1 : close - 1])
            ]
            if conditional:
                blocks = tuple(open_blocks)
                yield from (ConditionalWord(blocks, word, guarded) for word in words)
            else:
                yield from words


def _plain_asks(words: list[str | None]) -> bool:
    """Report whether a plain word sequence prints and exits.

    The words are on one path, with no further choice between them, so a
    version flag, a help flag or a `--print` query is enough.

    Returns
    -------
    bool
        True when one of the words asks a question.
    """
    for index, word in enumerate(words):
        if word is None:
            continue
        if word in _VERSION_FLAGS or word in _HELP_FLAGS:
            return True
        if word.startswith("--print=") and word[len("--print=") :] in _QUERY_SET:
            return True
        following = words[index + 1] if index + 1 < len(words) else None
        if word == "--print" and following in _QUERY_SET:
            return True
    return False


def _arm_chain(
    by_arm: dict[int, list[str | None]], arms: ArmChoices
) -> list[int] | None:
    """Return a choice's arms in order, or None when they form no single choice.

    The arms must be exactly one `if`/`else` chain that ends in an `else` with
    no condition: only then is every path to the launch accounted for, so a
    probe in each arm covers them all.

    Returns
    -------
    list[int] | None
        The arm blocks in source order, or None when the words do not cover
        one exhaustive choice.
    """
    if len(by_arm) < 2:
        return None
    starts = [arm for arm in by_arm if arm not in arms.next_arm.values()]
    if len(starts) != 1:
        return None
    order = [starts[0]]
    while order[-1] in arms.next_arm:
        following = arms.next_arm[order[-1]]
        if following in order or following not in by_arm:
            return None
        order.append(following)
    if set(order) != set(by_arm) or order[-2] not in arms.exhaustive:
        return None
    return order


def _plain_path(
    words: list[str | ConditionalWord | None], arm: int
) -> list[str | None]:
    """Return the words on the path that enters *arm*.

    The unconditional words are on every path, and the conditional words of
    the other arms are not; the arm's own words keep their place in the
    sequence, so the positional read sees the order cargo would.

    Returns
    -------
    list[str | None]
        The unconditional words in order, with this arm's conditional words in
        the place they were written; the other arms' words are omitted.
    """
    path: list[str | None] = []
    for word in words:
        if isinstance(word, ConditionalWord):
            if word.blocks[-1] == arm:
                path.append(word.word)
        else:
            path.append(word)
    return path


def _arm_paths(
    words: list[str | ConditionalWord | None],
    launch: tuple[int, ...],
    arms: ArmChoices,
) -> dict[int, list[str | None]] | None:
    """Return the cargo words on each path through one exhaustive choice.

    The paths are known only when every conditional word sits in an arm one
    block below the launch and those arms are the whole of one fully paired
    `if`/`else` chain: then each path's words are the unconditional ones plus
    that arm's. A guarded word, or one nested deeper, leaves a path's words
    uncertain, so no path is returned and the caller falls back to reading the
    unconditional sequence.

    Returns
    -------
    dict[int, list[str | None]] | None
        Each arm's opening brace with the words cargo reads on that path, or
        None when the words do not cover one exhaustive choice.
    """
    depth = len(launch)
    by_arm: dict[int, list[str | None]] = {}
    for word in words:
        if not isinstance(word, ConditionalWord):
            continue
        if (
            word.guarded
            or len(word.blocks) != depth + 1
            or word.blocks[:depth] != launch
        ):
            return None
        by_arm.setdefault(word.blocks[depth], []).append(word.word)
    order = _arm_chain(by_arm, arms)
    if order is None:
        return None
    return {arm: _cargo_options(_plain_path(words, arm)) for arm in order}


def _path_compiles(path: list[str | None]) -> bool:
    """Report whether one path's words start compiling work.

    The subcommand is the first word that is neither an option nor an option's
    value. A word the reader cannot see may be the subcommand, and then any
    compiling word on the path is enough, as it is for a whole run.

    Returns
    -------
    bool
        True when the path's subcommand compiles the crate.
    """
    first = _first_positional(path)
    if first is None:
        return any(word in _COMPILING for word in path if word is not None)
    return first in _COMPILING


def _asks_for_help(path: list[str | None]) -> bool:
    """Report whether one path is given a help flag."""
    return any(word in _HELP_FLAGS for word in path if word is not None)


def _every_arm_asks(
    words: list[str | ConditionalWord | None],
    launch: tuple[int, ...],
    arms: ArmChoices,
) -> bool:
    """Report whether one exhaustive choice gives a probe on every path.

    Every conditional word must sit in an arm one block below the launch, and
    the arms must be the whole of one fully paired `if`/`else` chain. Each
    path is then that arm's words in their place among the unconditional ones,
    so a `--print` in every arm is completed by a `cfg` given after the choice.
    A word outside such a choice -- behind a short circuit, or nested in a
    block inside an arm -- leaves a path with no answer, so the run is not a
    probe. So does a guarded word: the arm gives it only when the short
    circuit beside it does not skip it.

    Returns
    -------
    bool
        True when each arm of one exhaustive choice asks a question.
    """
    depth = len(launch)
    by_arm: dict[int, list[str | None]] = {}
    for word in words:
        if not isinstance(word, ConditionalWord):
            continue
        if (
            word.guarded
            or len(word.blocks) != depth + 1
            or word.blocks[:depth] != launch
        ):
            return False
        by_arm.setdefault(word.blocks[depth], []).append(word.word)
    order = _arm_chain(by_arm, arms)
    return order is not None and all(
        _plain_asks(_plain_path(words, arm)) for arm in order
    )


def _asks_a_question(
    words: list[str | ConditionalWord | None],
    launch: tuple[int, ...],
    arms: ArmChoices,
) -> bool:
    """Report whether an argument sequence makes the compiler print and exit.

    A `--print` query may be one word (`--print=cfg`) or two (`--print cfg`),
    and the two forms ask the same question however they are split across
    calls. `--print` with any other word after it still compiles, and so does a
    query whose two halves are separated by a word that is not plainly the
    query (`--print`, an unreadable word, `cfg`): the intervening word may be
    `link-args`, which builds the crate. A help flag makes the compiler print
    its help and exit, so it asks nothing either. A probe flag given in a
    conditional block still counts when a fully paired choice covers every
    path with one.

    Returns
    -------
    bool
        True when a version flag, a help flag or a `--print` query is among the
        words.
    """
    for index, word in enumerate(words):
        if isinstance(word, ConditionalWord) or word is None:
            continue
        if word in _VERSION_FLAGS or word in _HELP_FLAGS:
            return True
        if word.startswith("--print=") and word[len("--print=") :] in _QUERY_SET:
            return True
        following = words[index + 1] if index + 1 < len(words) else None
        if word == "--print" and isinstance(following, str) and following in _QUERY_SET:
            return True
    return _every_arm_asks(words, launch, arms)


def is_probe(text: str, start: int, window: Window) -> bool:
    """Report whether a `rustc` command only asks a question and compiles nothing.

    Only arguments added to this command count, so a `"--version"` in a comment,
    in an assertion after it or on another command is not its argument. Every
    run of the command must ask its question: a version flag added on the path
    that skips a compiling run says nothing about the run that compiles.

    Returns
    -------
    bool
        True when every run of the command is given a version or query flag.
    """
    name = _bound_name(text, start)
    read = own_statements(text, start, window)
    runs = read.runs
    if not runs:
        return False
    for run in runs:
        launch = _launch_of(run)
        if launch is None or not _asks_a_question(
            list(_probe_words(run, name, launch)), launch, read.arms
        ):
            return False
    return True


def _run_compiles(run: list[Statement], name: str | None, arms: ArmChoices) -> bool:
    """Report whether one run of the command adds compiling work before it launches.

    A help flag makes cargo print its help and exit, so a run that is given one
    compiles nothing whatever its subcommand says. Only a help flag given on
    every path counts: one added inside a block is absent when that block does
    not run, and the run then compiles. Otherwise the subcommand, the first
    word that is neither an option nor an option's value, decides. A
    conditional word may change the subcommand on the path that runs its
    block, so the unconditional subcommand is read as well: a conditional
    `help` does not hide the `build` that runs when the block is skipped. A
    word the reader cannot see leaves the subcommand open, and then any
    compiling word before the launch is enough.

    When the conditional words cover one fully paired choice, each path is
    read for its own subcommand and its own help flag: a `metadata` in one arm
    does not hide the `build` in the other, and a `--help` in the `if` arm
    excuses that arm alone, not the `build` its `else` arm runs.

    Returns
    -------
    bool
        True when the run starts compiling work.
    """
    words = _cargo_options(
        word
        for statement in run
        for word in _argument_words(statement.text, statement.masked, name)
    )
    launch = _launch_of(run)
    plain = list(_probe_words(run, name, launch))
    if launch is not None:
        paths = _arm_paths(plain, launch, arms)
        if paths is not None:
            return any(
                _path_compiles(path) and not _asks_for_help(path)
                for path in paths.values()
            )
    unconditional = _cargo_options(plain)
    if any(isinstance(word, str) and word in _HELP_FLAGS for word in unconditional):
        return False
    first = _first_positional(words)
    if first is not None:
        return first in _COMPILING or _first_positional(unconditional) in _COMPILING
    return any(word in _COMPILING for word in words)


def compiles(
    text: str,
    masked: str,
    closers: dict[int, int],
    start: int,
    index: FileIndex,
) -> bool:
    """Report whether a cargo command adds compiling work before it runs.

    Each run of the command is read on its own, because a launch on one path
    restarts the argument list for the next: a probe on the if-path and a
    compile on the else-path are two answers, and either compiling one is
    enough. Within a run the subcommand is the first word that is neither an
    option nor an option's value, so `cargo help build` prints help rather than
    building (`build` is the topic help is asked about), while `-h` or
    `--help` makes cargo print help whatever the subcommand is. Only calls on
    the command count, and only up to its first launch: an argument added after
    the command has run belongs to a reuse, which compiles nothing. The calls
    include a helper's body, spliced in at the call site, so a launch in either
    place ends the arguments.

    Returns
    -------
    bool
        True when any run of the command starts compiling work.
    """
    window = command_window(text, masked, closers, start, index)
    name = _bound_name(text, start)
    read = own_statements(text, start, window)
    return any(_run_compiles(run, name, read.arms) for run in read.runs)
