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
the unconditional `build` still compiling, and it cannot fill an option's value
slot either: the path that skips its block gives the next word as that value.
A run with no unconditional subcommand at all is compiled when any path names
one: a `metadata` in one block beside a `build` in another builds when only the
build's block runs, and two conditional words before a topic are each read as a
possible subcommand. An arm left as the only path after the other arm returns
is read as given on every path. A word the reader cannot see leaves the
subcommand open, and then any compiling word before the launch is enough.

`-h` or `--help` makes the command compile nothing at all when it is given on
every path, and each arm of a fully paired choice is read for its own
subcommand and its own help flag, so one arm's `metadata` does not hide the
other arm's `build`. A help flag given in the block that also holds the word
it excuses covers that word as well, so the path that gives a conditional
subcommand gives the flag with it -- unless a short circuit may skip the flag
(`query && command.arg("--help")`), which leaves the word without help on the
path that takes the left side. Each path is read from the words it gives and
not from one flattened list of every word, so a `build` in a block that a path
skips is not that path's subcommand, and a later `metadata` is still read on
its own. A `--` given on only some path is still a separator on that path, so
a help flag written after it is passed to the program the command runs and
does not excuse the build, whether the separator is unconditional or given in
a conditional block beside its flag. A `--print` query asks
the compiler what it is only when its two halves are adjacent: an unreadable
or conditional word between `--print` and `cfg` may be `link-args`, which
builds the crate, so the query is not read as a probe. A kind may name an
output file after a second `=`, in the joined form (`--print=cfg=out.txt`) or
the two-word one (`--print`, `cfg=out.txt`), which writes the answer there; the
query is the kind before the
path, and the tables that need no compile include `file-names`. A
probe flag in a block counts only when every block open at the flag is also
open at the launch; a flag in a sibling block that has already closed does not.
A choice whose arms are fully paired can still cover every path, whether it is
written across statements or on one line: a `-vV` in the `if` arm and a `-V` in
its `else` arm reach the shared launch as a probe either way. An arm is read
with the words given before the branch, so a `--print` in each arm is completed
by a `cfg` after the choice. A short circuit inside an arm
(`query && command.arg("-V").get_args().next().is_some()`) can still skip that
arm's word, so the arm does not cover its path. An option given in a block of
its own (`if verbose { command.arg("-v") }`) cannot change the subcommand, so
it does not stop a paired choice beside it being read path by path. An arm
that holds only options is still a path when the choice is fully paired: it
gives the word a help flag rather than a subcommand, and dropping it would
leave the sibling arm's subcommand looking unconditional.

A word is the text its literal has at run time, so an escape cannot hide a
compiling subcommand from the read. A Unicode escape's digits may be separated
by underscores, which are not part of the number, and an escape longer than the
six digits Rust allows is not decoded. A literal that ends at an escaped quote
is still one literal, so an argument before a later `build` keeps its place as
the subcommand.

A shape the scan cannot attribute -- a helper defined twice, a call reached
through a path or method, a name a `use` item could import (a glob import names
none but could bring any in), a receiver that is a field or a call, a parameter
or binding reassigned or shadowed -- is declined rather than guessed, because a
false positive would demand configuration from a repository that needs none. A
run that never reaches a launch in the read starts no child process, so its
words are no evidence of compiler work; a command launched beyond the read is
declined for the same reason rather than guessed to compile. A probe is decided
by the launched runs alone, so a `rustc` command with no launch at all is not a
probe.
The reading is textual: comments are blanked and literal contents kept, and
braces come from the masked source, so a `;` or a `{` in a string does not move
a statement boundary.

:mod:`concordat.rules.nested_cargo_words` reads the literal each call gives,
:mod:`concordat.rules.nested_cargo_statements` decides which statements belong
to the command and which paths they run on, and
:mod:`concordat.rules.nested_cargo_receivers` decides which calls belong to it;
this module decides what the words mean.
"""

from __future__ import annotations

import typing as typ

from .nested_cargo_handoffs import (
    SHORT_CIRCUIT,
    Window,
    _bound_name,
    command_window,
)
from .nested_cargo_receivers import owns_call
from .nested_cargo_runs import (
    ArmChoices,
    Run,
    _track,
)
from .nested_cargo_statements import own_statements
from .nested_cargo_text import argument_items
from .nested_cargo_words import (
    _ARGUMENT_CALL,
    _HELP_FLAGS,
    _QUERY_SET,
    _VERSION_FLAGS,
    ConditionalWord,
    _cargo_options,
    _first_positional,
    _is_option,
    _literal_word,
    _plain_asks,
    _print_kind,
    _query_kind,
)
from .rust_source import scan_balanced

if typ.TYPE_CHECKING:
    from .nested_cargo_shadows import FileIndex

SUBCOMMANDS: typ.Final = (
    "build|check|test|run|package|clippy|doc|rustc|install|publish|bench|miri"
    "|nextest|llvm-cov|rustdoc"
)
_COMPILING: typ.Final = frozenset(SUBCOMMANDS.split("|"))


def _without_brace(blocks: tuple[int, ...], brace: int | None) -> tuple[int, ...]:
    """Return *blocks* without *brace*, or unchanged when it is not there.

    Returns
    -------
    tuple[int, ...]
        The blocks, with the surviving arm's brace left out.
    """
    if brace is None or brace not in blocks:
        return blocks
    return tuple(block for block in blocks if block != brace)


def _launch_of(run: Run) -> tuple[int, ...] | None:
    """Return the blocks open at the run's launch, or None when it never launches.

    The surviving arm is left out of the launch's blocks, as it is left out of
    the words', so a launch inside that arm reads as no further choice.

    Returns
    -------
    tuple[int, ...] | None
        The launch's blocks, identified by their opening braces.
    """
    for statement in run.statements:
        if statement.launch_blocks is not None:
            return _without_brace(statement.launch_blocks, run.survivor)
    return None


def _probe_words(
    run: Run, name: str | None, launch: tuple[int, ...] | None
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
    for statement in run.statements:
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
            # The surviving arm is on every path that launches, so its brace
            # is not a choice: a word inside it is given whenever the command
            # runs, exactly as one written above the choice.
            active = _without_brace(tuple(open_blocks), run.survivor)
            before = statement.masked[: found.start()]
            conditional = (
                SHORT_CIRCUIT.search(before) is not None
                or active != launch[: len(active)]
            )
            # A short circuit that sits inside the word's own block can skip
            # it on the path through that block, so the block does not
            # certainly give the word.
            inner = 0
            if active and active[-1] >= statement.start:
                inner = active[-1] - statement.start + 1
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
                yield from (ConditionalWord(active, word, guarded) for word in words)
            else:
                yield from words


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
    that arm's. A guarded word, a word the reader cannot see, or one nested
    deeper leaves a path's words uncertain, so no path is returned and the
    caller falls back to reading the unconditional sequence. An option given
    in a block of its own (`if verbose { command.arg("-v") }`) is the
    exception: it cannot change the subcommand, so the choice beside it is
    still read path by path.

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
            # A word that can hide the subcommand leaves a path uncertain, and
            # so does a separator: a `--` given on one path sends every later
            # word to the program, so the arms' help flags no longer excuse the
            # run on that path. Only an option of a block of its own can be set
            # aside here.
            if word.guarded or not _is_option(word.word) or word.word == "--":
                return None
            continue
        by_arm.setdefault(word.blocks[depth], []).append(word.word)
    order = _arm_chain(by_arm, arms)
    if order is None:
        # A block that holds only options cannot change the subcommand, so it
        # may sit beside the choice (`if verbose { arg("-v") }`) without being
        # one of its arms. A block paired with another is still an arm, even
        # when it holds only options: `if quiet { arg("--help") } else {
        # arg("-h") }` is the choice, and setting its arms aside would drop
        # the help that excuses both paths. A separator is not set aside
        # either: it sends every word after it to the program.
        if any("--" in arm_words for arm_words in by_arm.values()):
            return None
        paired = set(arms.next_arm) | set(arms.next_arm.values())
        arms_only = {
            arm: arm_words
            for arm, arm_words in by_arm.items()
            if arm in paired or not all(_is_option(word) for word in arm_words)
        }
        if len(arms_only) == len(by_arm):
            return None
        order = _arm_chain(arms_only, arms)
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
        if _print_kind(word) in _QUERY_SET:
            return True
        following = words[index + 1] if index + 1 < len(words) else None
        if (
            word == "--print"
            and _query_kind(following if isinstance(following, str) else None)
            in _QUERY_SET
        ):
            return True
    return _every_arm_asks(words, launch, arms)


def is_probe(masked: str, start: int, window: Window) -> bool:
    """Report whether a `rustc` command only asks a question and compiles nothing.

    Only arguments added to this command count, so a `"--version"` in a comment,
    in an assertion after it or on another command is not its argument. Every
    launched run of the command must ask its question: a version flag added on
    the path that skips a compiling run says nothing about the run that
    compiles. A run that never launches starts no process and so asks nothing;
    a path that returns before it runs is no evidence either way, and ignoring
    it is what lets the launched runs decide (`--version`, then `if skip {
    return; }`, then the launch is still a probe).

    Returns
    -------
    bool
        True when the command launches and every launched run is given a
        version or query flag.
    """
    name = _bound_name(masked, start)
    read = own_statements(masked, start, window)
    launched = False
    for run in read.runs:
        launch = _launch_of(run)
        if launch is None:
            continue
        launched = True
        if not _asks_a_question(
            list(_probe_words(run, name, launch)), launch, read.arms
        ):
            return False
    return launched


def _before_a_separator(
    words: list[str | ConditionalWord | None],
) -> list[str | ConditionalWord | None]:
    """Return the words that are cargo's however the conditional paths fall.

    A `--` that only some path gives is still a separator on that path
    (`if forward { command.arg("--") }`), so a word after it belongs to the
    program the command runs when the separator is written -- `cargo test --
    --help` passes `--help` to the test binary and compiles the tests. Such a
    word is not certainly cargo's, so it cannot excuse compiling work, and the
    words after any separator are left out of the reads that need a word on
    every path.

    Returns
    -------
    list[str | ConditionalWord | None]
        The words before the first separator, conditional ones included.
    """
    kept: list[str | ConditionalWord | None] = []
    for word in words:
        text = word.word if isinstance(word, ConditionalWord) else word
        if text == "--":
            break
        kept.append(word)
    return kept


def _a_conditional_word_compiles(
    words: list[str | ConditionalWord | None],
) -> bool:
    """Report whether a conditional word can be the subcommand on some path.

    A conditional word is absent on the path that skips its block, so the words
    before it that every path gives are read on their own: if the word is the
    first positional there, the path that gives it has that subcommand. Two
    blocks in sequence are read apart this way even when neither is a paired
    choice (`if a { arg("search") } if b { arg("install") } arg("ripgrep")`
    gives `install` when only the second block runs). A word after an
    unconditional subcommand is not read, because the subcommand is fixed
    before it (`cargo help build` prints help rather than building).

    A path that is also given a help flag compiles nothing, so the flag is
    kept beside the word it travels with: a conditional `build` and a
    conditional `--help` written in the same block reach cargo together, and
    the block's path prints help rather than building. The flag may sit in a
    block that encloses the word's (`if a { arg("--help"); if b { arg("build") } }`),
    because that block runs whenever the word's does.

    Returns
    -------
    bool
        True when some conditional word is a compiling subcommand on a path.
    """
    helping = _helping_blocks(words)
    earlier: list[str | None] = []
    for word in words:
        if isinstance(word, ConditionalWord):
            if (
                word.word in _COMPILING
                and _first_positional([*earlier, word.word]) == word.word
                and not _a_path_asks(helping, word)
            ):
                return True
            continue
        earlier.append(word)
    return False


def _helping_blocks(
    words: typ.Iterable[str | ConditionalWord | None],
) -> list[tuple[int, ...]]:
    """Return the blocks a conditional help flag is guaranteed to be given in.

    A help flag is guaranteed only when the path that runs its block certainly
    gives it to cargo. A guarded flag (`query && command.arg("--help")`) is
    absent when the short circuit skips it, and a flag written after a `--`
    that the same path gives belongs to the program the command runs
    (`cargo test -- --help` passes the flag to the test binary). A flag in a
    nested block is still cargo's when the separator sits in a block that
    encloses it, because that block runs whenever the flag's does; a separator
    in a sibling block is not on the flag's path.

    Returns
    -------
    list[tuple[int, ...]]
        The blocks open at each conditional help flag that really reaches
        cargo, in order.
    """
    separators: list[tuple[int, ...]] = []
    helping: list[tuple[int, ...]] = []
    for word in words:
        if not isinstance(word, ConditionalWord):
            continue
        if word.word == "--":
            separators.append(word.blocks)
        elif (
            word.word in _HELP_FLAGS
            and not word.guarded
            and not any(word.blocks[: len(blocks)] == blocks for blocks in separators)
        ):
            helping.append(word.blocks)
    return helping


def _a_path_asks(helping: list[tuple[int, ...]], word: ConditionalWord) -> bool:
    """Report whether the path giving *word* is also given a help flag.

    A help flag in a block that encloses the word's is given whenever the
    word's block runs, so the path asks for help rather than compiling.

    Returns
    -------
    bool
        True when a conditional help flag is on the word's path.
    """
    return any(word.blocks[: len(blocks)] == blocks for blocks in helping)


def _run_compiles(run: Run, name: str | None, arms: ArmChoices) -> bool:
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
    compiling word before the launch is enough. So does a run with no
    unconditional subcommand at all: a `build` in a block of its own beside a
    `metadata` in another is read as compiling, because the path that runs only
    the first block builds.

    When the conditional words cover one fully paired choice, each path is
    read for its own subcommand and its own help flag: a `metadata` in one arm
    does not hide the `build` in the other, and a `--help` in the `if` arm
    excuses that arm alone, not the `build` its `else` arm runs.

    A run that never reaches a launch starts no compiler, so it compiles
    nothing whatever its arguments say. A command launched beyond the read
    (stored and run later, or handed to something the scan declines) is
    declined here for the same reason: the arguments alone are no evidence
    that compiler work happens.

    Returns
    -------
    bool
        True when the run starts compiling work.
    """
    launch = _launch_of(run)
    if launch is None:
        return False
    plain = list(_probe_words(run, name, launch))
    paths = _arm_paths(plain, launch, arms)
    if paths is not None:
        return any(
            _path_compiles(path) and not _asks_for_help(path) for path in paths.values()
        )
    unconditional = _cargo_options(plain)
    # A help flag after a separator is not certainly cargo's: on the path that
    # writes the `--`, cargo passes it to the program the command runs.
    if any(
        isinstance(word, str) and word in _HELP_FLAGS
        for word in _cargo_options(_before_a_separator(plain))
    ):
        return False
    settled = _first_positional(unconditional)
    if settled:
        # A conditional word before it may still be the subcommand on its own
        # path (`if a { arg("metadata") } arg("build")` builds when `a` is
        # false), so the conditional words are read on their own paths rather
        # than in a flattened sequence: a `build` written beside its own
        # `--help` prints help and does not compile. A choice is not needed for
        # this: each conditional word is read with the words before it that
        # every path gives, so two blocks in sequence are read apart
        # (`if a { arg("search") } if b { arg("install") } arg("ripgrep")` runs
        # `cargo install ripgrep` when only the second block does).
        return settled in _COMPILING or _a_conditional_word_compiles(unconditional)
    # No unconditional subcommand decides the run, so a compiling word on any
    # path may be the one that runs: a conditional `build` beside a conditional
    # `metadata` still builds when only the build's block runs. An unreadable
    # word leaves the same question open. The probe words are read rather than
    # the plain sequence, because a conditional word keeps the blocks it was
    # given in: a `--help` beside a `build` in the same block is on that
    # build's path.
    return _may_compile(plain)


def _may_compile(
    words: typ.Iterable[str | ConditionalWord | None],
) -> bool:
    """Report whether any word on any path names a compiling subcommand.

    Returns
    -------
    bool
        True when a plain or conditional word is a compiling subcommand.
    """
    listed = list(words)
    helping = _helping_blocks(listed)
    return any(
        (word.word if isinstance(word, ConditionalWord) else word) in _COMPILING
        and not (isinstance(word, ConditionalWord) and _a_path_asks(helping, word))
        for word in listed
    )


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
    the command has run belongs to a reuse, which compiles nothing, and a run
    that never reaches a launch starts no child process at all. The calls
    include a helper's body, spliced in at the call site, so a launch in either
    place ends the arguments.

    Returns
    -------
    bool
        True when any run of the command starts compiling work.
    """
    window = command_window(text, masked, closers, start, index)
    name = _bound_name(masked, start)
    read = own_statements(masked, start, window)
    return any(_run_compiles(run, name, read.arms) for run in read.runs)


def launches(masked: str, start: int, window: Window) -> bool:
    """Report whether the command is launched inside its read.

    A command that is built and given words but never launched starts no child
    process, so its arguments are no evidence of compiler work. A command
    launched beyond the read -- stored and run later, or handed to something the
    scan declines -- is declined for the same reason rather than guessed at.

    Returns
    -------
    bool
        True when some run of the command reaches a launch.
    """
    return any(
        _launch_of(run) is not None
        for run in own_statements(masked, start, window).runs
    )
