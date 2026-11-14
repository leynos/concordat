"""Read the prover tooling exceptions a repository declares in `.concordat`.

An exception is data, as `rust-build-defaults` records BD-010's:

```yaml
language:
  rust:
    prover_tools:
      exceptions:
        - clauses: [PT-001, PT-002]
          pin: 9c4a27a8
          record: docs/adrs/adr-0011-....md
          rationale: Why the repository stays on this revision.
          review_trigger: The event that sends it back for review.
```

This module reads and validates the declaration and the one fact the policy
cannot read for itself: whether the decision record exists. The policy decides
whether the exception is in force.
"""

from __future__ import annotations

import pathlib
import typing as typ

from concordat.errors import OperationalRuleError

from .fs_probe import regular_file_exists
from .rust_surfaces import CONCORDAT_FILENAME, rust_language_mapping

if typ.TYPE_CHECKING:
    from .prover_tooling_types import ProverException

OPERATION: typ.Final = "read-prover-tooling-facts"


def _error(message: str, checkout: pathlib.Path) -> OperationalRuleError:
    """Create a declaration error with a stable operation boundary."""
    return OperationalRuleError(
        message, operation=OPERATION, resource=checkout / CONCORDAT_FILENAME
    )


def _text(entry: dict[str, object], key: str, checkout: pathlib.Path) -> str:
    """Return a declared string field, or fail naming the field."""
    value = entry.get(key)
    if isinstance(value, str):
        return value
    message = f"a prover_tools exception needs a text {key}: {value!r}"
    raise _error(message, checkout)


def _clauses(entry: dict[str, object], checkout: pathlib.Path) -> list[str]:
    """Return the declared clause identifiers, or fail."""
    value = entry.get("clauses")
    if isinstance(value, list) and all(isinstance(c, str) for c in value):
        return typ.cast("list[str]", value)
    message = f"a prover_tools exception needs a list of clauses: {value!r}"
    raise _error(message, checkout)


def _is_contained(record: str) -> bool:
    """Return whether *record* names a path that stays inside a checkout."""
    posix = pathlib.PurePosixPath(record)
    return bool(record) and not posix.is_absolute() and ".." not in posix.parts


def _record_present(checkout: pathlib.Path, record: str) -> bool:
    """Return whether *record* is a regular file inside the checkout."""
    if not _is_contained(record):
        return False
    candidate = checkout.joinpath(*pathlib.PurePosixPath(record).parts)
    if not candidate.resolve().is_relative_to(checkout.resolve()):
        return False
    return regular_file_exists(candidate, operation=OPERATION)


def _exception(entry: object, checkout: pathlib.Path) -> ProverException:
    """Validate one declaration and read the facts behind it."""
    if not isinstance(entry, dict):
        message = f"a prover_tools exception must be a mapping: {entry!r}"
        raise _error(message, checkout)
    mapping = typ.cast("dict[str, object]", entry)
    record = _text(mapping, "record", checkout)
    return {
        "clauses": _clauses(mapping, checkout),
        "pin": _text(mapping, "pin", checkout),
        "record": record,
        "rationale": _text(mapping, "rationale", checkout),
        "review_trigger": _text(mapping, "review_trigger", checkout),
        "record_present": _record_present(checkout, record),
    }


def _entries(checkout: pathlib.Path) -> list[object]:
    """Return the declared exception entries, or an empty list when none."""
    rust = rust_language_mapping(checkout)
    block = None if rust is None else rust.get("prover_tools")
    if block is None:
        return []
    if not isinstance(block, dict):
        message = "language.rust.prover_tools must be a mapping"
        raise _error(message, checkout)
    entries = typ.cast("dict[str, object]", block).get("exceptions")
    if entries is None:
        return []
    if not isinstance(entries, list):
        message = "language.rust.prover_tools.exceptions must be a list"
        raise _error(message, checkout)
    return typ.cast("list[object]", entries)


def declared_exceptions(checkout: pathlib.Path) -> list[ProverException]:
    """Return the exceptions `.concordat` declares for prover tools.

    Returns
    -------
    list[ProverException]
        Each declared exception with whether its decision record exists. A
        malformed declaration raises `OperationalRuleError`.
    """
    return [_exception(entry, checkout) for entry in _entries(checkout)]
