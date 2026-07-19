"""Shared exception types for the concordat CLI."""

from __future__ import annotations


class ConcordatError(RuntimeError):
    """Base error for concordat CLI operations."""


class OperationalRuleError(ConcordatError):
    """Rule evaluation could not run at all (missing tool, unreadable input).

    Distinct from a policy finding: findings exit 1, operational failures
    exit 2 so automation can tell "noncompliant" from "could not audit".
    """
