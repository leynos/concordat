"""Read the words one `.arg(..)` call gives a command, in source order.

The nested-cargo scan asks a command's arguments two questions -- whether a
`cargo` command names compiling work, and whether a `rustc` command only asks
the compiler who it is -- and both start here, with the literal text of each
word. A word is the text its literal has at run time, so an escape is decoded
before the word is compared with `--version` or `build`: otherwise a word
written with an escape would read as one the scan does not know, and a
compiling subcommand could hide behind it. Byte strings, raw strings and
continued lines are not read, because their run-time text is not the source
text and guessing would misclassify the word.

A cargo command's subcommand is the first word that is neither an option nor an
option's value, and a word after a `--` belongs to the program the command runs
rather than to cargo. The words are kept in the type they were read with, so a
plain path stays plain while a run that reads an argument only on some paths
keeps that argument marked as conditional. Such a word is passed over rather
than counted, both as the subcommand and as an option's value, because on the
path that skips its block neither position is its.

The reading is textual: comments are blanked and literal contents kept by the
caller, and the argument calls are found in the masked source so a `;` or a `{`
in a string does not move a statement boundary. Which calls belong to the
command is decided in :mod:`concordat.rules.nested_cargo_receivers`; this
module only reads what those calls say.

:mod:`concordat.rules.nested_cargo_arguments` uses these readers to decide what
the words mean for a whole run.
"""

from __future__ import annotations

import re
import typing as typ

from .nested_cargo_receivers import owns_call
from .nested_cargo_text import argument_items
from .rust_source import scan_balanced

# The `--print` requests that read a table or a path and exit without compiling
# an input. `link-args` and `native-static-libs` are deliberately absent: they
# build the crate first.
QUERY_PRINTS: typ.Final = (
    "sysroot|target-libdir|host-tuple|cfg|target-list|target-cpus|target-features"
    "|code-models|relocation-models|tls-models|file-names"
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
# A `\u{..}` escape's braces and digits, read after the `u`. Underscores may
# separate the digits, so they are removed before the number is counted.
_UNICODE_ESCAPE: typ.Final = re.compile(r"\{([0-9A-Fa-f_]+)\}")
# Options cargo reads one value for before the subcommand.
_VALUE_OPTIONS: typ.Final = frozenset({
    "--color",
    "--manifest-path",
    "--config",
    "-C",
    "-Z",
})


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
            digits = found[1].replace("_", "")
            # Rust allows at most six hexadecimal digits, however many
            # separators are written between them.
            if not digits or len(digits) > 6:
                return None
            code = int(digits, 16)
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
    read as the subcommand, and it cannot fill an option's value slot either:
    on the path that skips its block the next word is the value
    (`--color`, a conditional `never`, `always`, `build` gives `--color
    always` and the subcommand `build`).

    Returns
    -------
    str | None
        The word's text, an empty string when the command gives no positional
        word, or None when no word before one can be read.
    """
    skip_value = False
    for word in words:
        if isinstance(word, ConditionalWord):
            continue
        if skip_value:
            skip_value = False
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


def _print_kind(word: str) -> str | None:
    """Return the query kind a `--print=..` word names, or None.

    A kind may be followed by an output path (`--print=cfg=out.txt`), which
    writes the answer to that file; the query is the kind before the path, and
    it still compiles nothing.

    Returns
    -------
    str | None
        The kind, or None when the word is not a `--print=` query.
    """
    if not word.startswith("--print="):
        return None
    return _query_kind(word[len("--print=") :])


def _query_kind(word: str | None) -> str | None:
    """Return the query kind a `--print` value names, before any output path.

    The two-word form takes the same output path (`--print cfg=out.txt`), so
    the value is read up to its first `=`, as the joined form is.

    Returns
    -------
    str | None
        The kind, or None when there is no value to read.
    """
    if word is None:
        return None
    return word.split("=", 1)[0]


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
        if _print_kind(word) in _QUERY_SET:
            return True
        following = words[index + 1] if index + 1 < len(words) else None
        if word == "--print" and _query_kind(following) in _QUERY_SET:
            return True
    return False


def _is_option(word: str | None) -> bool:
    """Report whether *word* is written as an option rather than a subcommand.

    A conditional option in a block of its own cannot change a choice's
    subcommand, so it does not stop that choice being read path by path. A
    word the reader cannot see might be one.

    Returns
    -------
    bool
        True when the word starts with `-` or `+`.
    """
    return word is not None and word.startswith(("-", "+"))
