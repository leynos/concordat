"""Turn the canon's Rust lint baseline into the JSON the policies read.

The baseline is kept as paste-ready TOML (`baseline.toml`), which a repository
copies into its `Cargo.toml`, plus the things a paste cannot say
(`extras.toml`: the cognitive-complexity alternative and the PyO3 divergences).
Rego reads JSON, so this module derives one document from the two files. The
derivation lives in one function so that the generator script and the test that
holds the committed JSON to the TOML run the same code.

Each lint becomes `{"level": ...}` (and `"priority"` for a group such as
`pedantic`). A `check-cfg` list is dropped: the rule asks for the level only,
because the cfgs a repository declares are its own.
"""

from __future__ import annotations

import tomllib
import typing as typ

__all__ = ["BASELINE_TABLES", "baseline_document"]

BASELINE_TABLES: typ.Final = ("clippy", "rust", "rustdoc")


def _entry(value: object) -> dict[str, object]:
    """Return one lint's `{level, priority}` facts from its TOML value."""
    match value:
        case str():
            return {"level": value}
        case {"level": str() as level, "priority": priority}:
            return {"level": level, "priority": priority}
        case {"level": str() as level}:
            return {"level": level}
        case _:
            message = (
                f"a baseline lint must be a level or a table with one, got {value!r}"
            )
            raise ValueError(message)


def baseline_document(baseline_toml: str, extras_toml: str) -> dict[str, object]:
    """Return the `{"baseline": ...}` document derived from the two TOML files.

    Parameters
    ----------
    baseline_toml:
        The text of `baseline.toml`.
    extras_toml:
        The text of `extras.toml`.

    Returns
    -------
    dict[str, object]
        A document with the three lint tables, the `cognitive` table and the
        `pyo3_divergences` list under a single `baseline` key, ready to be
        loaded as Conftest data.

    Raises
    ------
    ValueError
        If a lint is not a level or a table with a `level`, or a section the
        baseline needs is missing.
    """
    lints = tomllib.loads(baseline_toml).get("workspace", {}).get("lints", {})
    extras = tomllib.loads(extras_toml)
    document: dict[str, object] = {}
    for name in BASELINE_TABLES:
        table = lints.get(name)
        if not isinstance(table, dict) or not table:
            message = f"baseline.toml has no [workspace.lints.{name}] table"
            raise ValueError(message)
        document[name] = {lint: _entry(value) for lint, value in table.items()}
    document["cognitive"] = extras["cognitive"]
    document["pyo3_divergences"] = extras["pyo3_divergences"]
    return {"baseline": document}
