"""Interactive user prompts for terminal-based workflows.

This module provides utilities for prompting users for yes/no confirmations
and detecting whether interactive input is available.
"""

from __future__ import annotations

import sys
import typing as typ


def prompt_yes_no(message: str, output: typ.IO[str] | None = None) -> bool:
    """Prompt on a TTY and return True for yes-like responses.

    Writes the prompt message to the output stream and reads a response from
    stdin. Accepts 'y' or 'yes' (case-insensitive) as affirmative responses.

    Args:
        message: The prompt message to display.
        output: Output stream for the prompt (defaults to stderr).

    Returns:
        True if the user responded with 'y' or 'yes', False otherwise.

    """
    stream = output or sys.stderr
    stream.write(message)
    stream.flush()

    response = sys.stdin.readline()
    if not response:
        return False
    return response.strip().lower() in {"y", "yes"}


def can_prompt() -> bool:
    """Check if interactive prompting is available.

    Returns:
        True if stdin is connected to a TTY, False otherwise.

    """
    stream = sys.stdin
    return bool(stream and stream.isatty())
