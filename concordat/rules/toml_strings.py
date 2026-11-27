"""Find the lines of a TOML document that start inside a multi-line string.

A text scan for headers, keys and comments must not read string content as
structure: a multi-line string can quote a `[table]`, an `excluded_paths` key or
a `# comment`, and none of them is the document's own. This scanner walks the
text once, tracking comments, one-line strings (basic and literal) and
multi-line strings (basic and literal, with escapes in the basic kind), and
reports the 0-based index of every line that begins inside a multi-line string.
It decides nothing else; the TOML parser stays the authority on values.
"""

from __future__ import annotations

import dataclasses

__all__ = ["string_interior_lines"]

_MULTILINE = ('"""', "'''")


@dataclasses.dataclass(slots=True)
class _Scan:
    """The position and state of the string scanner."""

    text: str
    index: int = 0
    line: int = 0
    state: str | None = None
    inside: set[int] = dataclasses.field(default_factory=set)

    def newline(self) -> None:
        """Count a line break, noting a line that starts inside a string."""
        self.line += 1
        if self.state in _MULTILINE:
            self.inside.add(self.line)

    def step_outside(self) -> None:
        """Advance one step with no string open."""
        char = self.text[self.index]
        opener = next(
            (o for o in _MULTILINE if self.text.startswith(o, self.index)), None
        )
        if char == "#":
            end = self.text.find("\n", self.index)
            self.index = len(self.text) if end < 0 else end
            return
        if opener is not None:
            self.state, self.index = opener, self.index + 3
            return
        if char in "\"'":
            self.state = char
        elif char == "\n":
            self.newline()
        self.index += 1

    def escape(self) -> None:
        """Skip a backslash and the character it takes, which may be a line break."""
        if self.text[self.index + 1 : self.index + 2] == "\n":
            self.newline()
        self.index += 2

    def step_multiline(self) -> None:
        """Advance one step inside a multi-line string."""
        char = self.text[self.index]
        if self.state is not None and self.text.startswith(self.state, self.index):
            self.state, self.index = None, self.index + 3
        elif char == "\\" and self.state == '"""':
            self.escape()
        else:
            if char == "\n":
                self.newline()
            self.index += 1

    def step_single_line(self) -> None:
        """Advance one step inside a one-line string, which a line break ends."""
        char = self.text[self.index]
        if char == self.state:
            self.state, self.index = None, self.index + 1
        elif char == "\\" and self.state == '"':
            self.escape()
        elif char == "\n":
            self.newline()
            self.state, self.index = None, self.index + 1
        else:
            self.index += 1

    def step(self) -> None:
        """Advance one step, whatever string is or is not open."""
        if self.state is None:
            self.step_outside()
        elif self.state in _MULTILINE:
            self.step_multiline()
        else:
            self.step_single_line()


def string_interior_lines(text: str) -> set[int]:
    """Return the 0-based indexes of the lines that start inside a multi-line string.

    Parameters
    ----------
    text:
        The TOML source.

    Returns
    -------
    set[int]
        The indexes of lines whose first character is string content.
    """
    scan = _Scan(text)
    while scan.index < len(text):
        scan.step()
    return scan.inside
