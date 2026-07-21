"""Shared exception types for the concordat CLI."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    import pathlib


class ConcordatError(RuntimeError):
    """Base error for concordat CLI operations."""


class OperationalRuleError(ConcordatError):
    """Rule evaluation could not run at all (missing tool, unreadable input).

    Distinct from a policy finding: findings exit 1, operational failures
    exit 2 so automation can tell "noncompliant" from "could not audit".

    Attributes
    ----------
    operation:
        Stable identifier for the action that failed, such as
        ``"parse-makefile"`` or ``"clone-repository"``.
    tool:
        External program whose invocation failed (``"makeutil"``,
        ``"conftest"``, ``"git"``), or ``None`` when no external tool was
        involved.
    resource:
        The affected input — a checkout, manifest, Makefile, Cargo TOML, or
        repository identifier — or ``None`` when none applies.

    """

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        tool: str | None = None,
        resource: pathlib.Path | str | None = None,
    ) -> None:
        """Initialise with the human-readable message plus failure context."""
        super().__init__(message)
        self.operation = operation
        self.tool = tool
        self.resource = resource
