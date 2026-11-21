"""Split a shell command text into its commands and substitutions.

RT-028 needs to know which commands a `run` script or recipe runs, not which
characters it contains. This module reads the text: it splits at unquoted
separators, keeps quoted text inside one word, drops a comment, skips a
here-document body while keeping the substitutions an unquoted delimiter lets
the shell expand, and records `$(...)` and backquoted substitutions so the
command each runs can be read in turn.

The selection routes are read in `toolchain_literal_shell`, which uses the
commands this module returns.
"""

from __future__ import annotations

import dataclasses
import typing as typ

_SHELL_BREAKS: typ.Final = frozenset(";|&\n")


def _opens_substitution(text: str, index: int) -> bool:
    """Return whether `$(` at *index* starts a command substitution.

    `$((` is arithmetic rather than a command, so it opens no substitution.

    Returns
    -------
    bool
        Whether *index* starts a command substitution.
    """
    return text.startswith("$(", index) and not text.startswith("$((", index)


def _opens_heredoc(text: str, index: int) -> bool:
    """Return whether `<<` at *index* opens a here-document.

    `<<<` is a here string, whose operand is one word rather than a body, so
    the second and third `<` of one open nothing.

    Returns
    -------
    bool
        Whether *index* opens a here-document.
    """
    return (
        text.startswith("<<", index)
        and not text.startswith("<<<", index)
        and text[index - 1 : index] != "<"
    )


def _past_quoted(text: str, index: int, quote: str) -> tuple[int, str | None]:
    """Return the index after one character of quoted text, and the quote state.

    Returns
    -------
    tuple[int, str | None]
        The index to resume at, and the quote still open (None when the
        character closed it).
    """
    char = text[index]
    if char == "\\" and quote == '"':
        return index + 2, quote
    if char == quote:
        return index + 1, None
    return index + 1, quote


def _substitution_end(text: str, index: int) -> int:
    """Return the index just past the `)` that closes the substitution at *index*.

    The parentheses are balanced across nested substitutions, and quoted text
    inside the substitution does not close it, so `$(echo ")")` is whole.

    Returns
    -------
    int
        The index after the closing `)`, or the end of the text when the
        substitution is never closed.
    """
    depth = 1
    scan = index + 2
    quote: str | None = None
    while scan < len(text):
        char = text[scan]
        if quote is not None:
            scan, quote = _past_quoted(text, scan, quote)
            continue
        if _closes_group(char, depth):
            return scan + 1
        if char in "()":
            depth += 1 if char == "(" else -1
        elif char == "\\":
            scan += 1
        elif char in "'\"":
            quote = char
        scan += 1
    return len(text)


def _closes_group(char: str, depth: int) -> bool:
    """Return whether *char* is the `)` that closes the outermost group.

    Returns
    -------
    bool
        Whether the substitution that opened at *depth* ends here.
    """
    if char != ")":
        return False
    return depth == 1


def _substitution_text(text: str, index: int, end: int) -> str:
    """Return the command text a substitution at *index* running to *end* runs."""
    closed = text[end - 1 : end] == ")"
    return text[index + 2 : end - 1 if closed else end]


def _backquote_end(text: str, index: int) -> int:
    """Return the index just past the backtick that closes the one at *index*."""
    end = index + 1
    while end < len(text) and text[end] != "`":
        end += 2 if text[end] == "\\" else 1
    return end


def _backquote_text(text: str, index: int, end: int) -> str:
    """Return the command text a backquoted substitution runs."""
    return text[index + 1 : end]


def _heredoc_body(
    text: str, index: int, delimiter: str, *, strip_tabs: bool
) -> tuple[int, str]:
    """Return the index past a here-document, and the body it delimits.

    *index* is where the operator stood. The body starts on the next line and
    runs to a line that is the delimiter alone, which is how a shell finds its
    end; `<<-` also accepts leading tabs before the delimiter.

    Returns
    -------
    tuple[int, str]
        The index of the first character after the terminator line, or the
        end of the text when no terminator is written, and the body itself.
    """
    start = text.find("\n", index)
    if start == -1:
        return len(text), ""
    start += 1
    line_start = start
    while True:
        line_end = text.find("\n", line_start)
        line = text[line_start : line_end if line_end != -1 else len(text)]
        if (line.lstrip("\t") if strip_tabs else line) == delimiter:
            return len(text) if line_end == -1 else line_end + 1, text[start:line_start]
        if line_end == -1:
            return len(text), text[start:]
        line_start = line_end + 1


def _body_substitutions(body: str) -> list[str]:
    """Return the command substitutions an unquoted here-document body runs.

    A body is input rather than commands, but `$(...)` and backticks in it
    still execute, because the shell expands an unquoted delimiter's body.

    Returns
    -------
    list[str]
        The text of each substitution the body runs.
    """
    found: list[str] = []
    index = 0
    while index < len(body):
        index, text = _body_step(body, index)
        if text is not None:
            found.append(text)
    return found


def _body_step(body: str, index: int) -> tuple[int, str | None]:
    """Read one character of a here-document body.

    Returns
    -------
    tuple[int, str | None]
        The index to resume at, and the command text of any substitution that
        starts here.
    """
    char = body[index]
    if char == "\\":
        # In a body a backslash escapes `$` and the backtick, so neither runs.
        return index + 2, None
    if _opens_substitution(body, index):
        end = _substitution_end(body, index)
        return end, _substitution_text(body, index, end)
    if char == "`":
        end = _backquote_end(body, index)
        return end + 1, _backquote_text(body, index, end)
    return index + 1, None


@dataclasses.dataclass(slots=True)
class _Scan:
    """The commands read so far, and the word being read.

    The scanner's state lives here so the character loop stays a loop: a word
    ends at a separator, a command ends at a shell break, a here-document body
    is skipped between the two, and a command substitution's text is kept so
    the command it runs can be read separately.
    """

    commands: list[list[str]] = dataclasses.field(default_factory=list)
    substitutions: list[str] = dataclasses.field(default_factory=list)
    words: list[str] = dataclasses.field(default_factory=list)
    current: list[str] = dataclasses.field(default_factory=list)
    delimiter: str = ""
    needs_delimiter: bool = False
    strip_tabs: bool = False
    delimiter_quoted: bool = False

    def end_word(self) -> None:
        """Complete the word being read, using it as a delimiter when due."""
        word = "".join(self.current)
        self.words.append(word)
        self.current = []
        if self.needs_delimiter:
            self.needs_delimiter = False
            self.delimiter = word

    def end_command(self) -> None:
        """Close the command the words read so far make, if there is one."""
        if self.words:
            self.commands.append(self.words)
            self.words = []

    def separate(self, text: str, char: str, index: int) -> int:
        """End the word and command at *char*, skipping a here-document body.

        Returns
        -------
        int
            The index to resume reading from.
        """
        if self.current:
            self.end_word()
        if char in _SHELL_BREAKS:
            self.end_command()
        if self.delimiter and char == "\n":
            # A command after `;` on the declaration line still runs; the body
            # it feeds starts on the next line.
            delimiter, self.delimiter = self.delimiter, ""
            quoted, self.delimiter_quoted = self.delimiter_quoted, False
            end, body = _heredoc_body(
                text, index, delimiter, strip_tabs=self.strip_tabs
            )
            if not quoted:
                self.substitutions.extend(_body_substitutions(body))
            return end
        return index + 1

    def open_heredoc(self, text: str, index: int) -> int:
        """Start a here-document at its operator.

        Returns
        -------
        int
            The index after the `<<` or `<<-` operator.
        """
        if self.current:
            # `cat<<EOF` writes the operator against the command.
            self.end_word()
        self.strip_tabs = text.startswith("<<-", index)
        self.needs_delimiter = True
        self.delimiter_quoted = False
        return index + (3 if self.strip_tabs else 2)

    def read_substitution(self, text: str, index: int) -> int:
        """Record the command a `$(...)` substitution runs and keep its text.

        Returns
        -------
        int
            The index after the substitution.
        """
        end = _substitution_end(text, index)
        self.substitutions.append(_substitution_text(text, index, end))
        self.current.append(text[index:end])
        return end

    def read_backquote(self, text: str, index: int) -> int:
        """Record the command a backquoted substitution runs and keep its text.

        Returns
        -------
        int
            The index after the closing backtick.
        """
        end = _backquote_end(text, index)
        closed = text[end : end + 1] == "`"
        self.substitutions.append(_backquote_text(text, index, end))
        self.current.append(text[index : end + 1 if closed else end])
        return end + 1 if closed else end


def _read_quoted(
    text: str, index: int, quote: str, scan: _Scan
) -> tuple[str | None, int]:
    r"""Read one character of a quoted word.

    In double quotes a backslash protects the next character, so `\"` does not
    close the word, and a command substitution still runs even though its text
    is quoted. In single quotes neither is true.

    Returns
    -------
    tuple[str | None, int]
        The quote still open, if any, and the index to resume reading from.
    """
    char = text[index]
    if char == quote:
        return None, index + 1
    if quote != '"':
        scan.current.append(char)
        return quote, index + 1
    if char == "\\" and index + 1 < len(text):
        # A backslash-newline is removed outright rather than ending the word.
        if text[index + 1] != "\n":
            scan.current.append(text[index + 1])
        return quote, index + 2
    if _opens_substitution(text, index):
        return quote, scan.read_substitution(text, index)
    if char == "`":
        return quote, scan.read_backquote(text, index)
    scan.current.append(char)
    return quote, index + 1


def _comment_end(text: str, index: int) -> int:
    """Return the index of the newline that ends the comment at *index*."""
    end = text.find("\n", index)
    return len(text) if end == -1 else end


def _scan(text: str) -> _Scan:
    """Read *text* into its commands and the substitutions that run in them."""
    scan = _Scan()
    quote: str | None = None
    index = 0
    while index < len(text):
        if quote is not None:
            quote, index = _read_quoted(text, index, quote, scan)
        else:
            quote, index = _read_bare(text, index, scan)
    if scan.current:
        scan.end_word()
    scan.end_command()
    return scan


def _read_bare(text: str, index: int, scan: _Scan) -> tuple[str | None, int]:
    """Read one unquoted character of *text*.

    Returns
    -------
    tuple[str | None, int]
        The quote the character opens, if any, and the index to resume at.
    """
    char = text[index]
    quote = _opened_quote(scan, char)
    if quote is not None:
        return quote, index + 1
    if char == "\\":
        return None, _read_escape(text, index, scan)
    end = _read_construct(text, index, scan)
    if end is not None:
        return None, end
    return None, _read_literal(text, index, scan, char)


def _opened_quote(scan: _Scan, char: str) -> str | None:
    """Return the quote *char* opens, recording a quoted here-document delimiter."""
    if char not in "'\"":
        return None
    # A quoted delimiter suppresses the expansion of its here-document body.
    if scan.needs_delimiter:
        scan.delimiter_quoted = True
    return char


def _read_escape(text: str, index: int, scan: _Scan) -> int:
    """Append the character a backslash escapes, dropping a line continuation."""
    if index + 1 >= len(text):
        scan.current.append("\\")
        return index + 1
    if text[index + 1] != "\n":
        scan.current.append(text[index + 1])
    return index + 2


def _read_construct(text: str, index: int, scan: _Scan) -> int | None:
    """Read the multi-character construct at *index*.

    Returns
    -------
    int | None
        The index to resume at, or None when *index* starts no construct.
    """
    if _opens_substitution(text, index):
        return scan.read_substitution(text, index)
    if _opens_heredoc(text, index):
        return scan.open_heredoc(text, index)
    char = text[index]
    if char == "`":
        return scan.read_backquote(text, index)
    if char not in "()":
        return None
    # A parenthesized group runs its contents: `(cargo +nightly-...)`.
    if scan.current:
        scan.end_word()
    return index + 1


def _read_literal(text: str, index: int, scan: _Scan, char: str) -> int:
    """Read a separator, comment or ordinary character, returning the next index."""
    if char == "#" and not scan.current:
        return _comment_end(text, index)
    if char.isspace() or char in _SHELL_BREAKS:
        return scan.separate(text, char, index)
    scan.current.append(char)
    return index + 1


def shell_commands(text: str) -> list[list[str]]:
    """Return each command's words, split at unquoted shell separators.

    Quoted text stays inside one word and the quotes are dropped, so
    `echo "cargo +nightly-2026-10-30"` is an argument to `echo` rather than a
    selection, and `RUSTUP_TOOLCHAIN="$(PIN)"` is still the reference. An
    unquoted `#` at a word boundary starts a comment. A backslash-newline is
    removed without ending the word, as it is in a recipe. A here-document
    body is command input rather than commands, so it is skipped: a date a
    script feeds to `cat` is not a date it selects. The operator need not be
    spaced from the command, so `cat<<EOF` opens one too. A parenthesized
    group and a backquoted substitution each run their own contents.

    Returns
    -------
    list[list[str]]
        One word list per command, in the order written.
    """
    return _scan(text).commands


def scan_text(text: str) -> tuple[list[list[str]], list[str]]:
    """Return *text*'s commands and the command substitutions that run in them.

    Returns
    -------
    tuple[list[list[str]], list[str]]
        One word list per command, in the order written, and the text of each
        command substitution the text runs.
    """
    scan = _scan(text)
    return scan.commands, scan.substitutions
