"""Recorded exceptions to the Rust build standard (BD-010).

A repository may keep the standard's fast flags out of Cargo's auto-discovered
configuration on purpose: cuprum builds published wheels in manylinux
containers that carry no `mold`, so the linker, the parallel frontend and the
Cranelift backend live in an explicit `tools/dev-fast/config.toml` that only
the `make dev-*` targets pass to Cargo (ADR-012). The rule cannot otherwise
tell that design from a repository that never adopted the standard.

The exception is structured, not prose. BD-004's Cranelift exception is a
heading the rule reads reliably, but the sentences beneath it cannot be
checked, so a waiver of several clauses is declared as data in `.concordat`:

```yaml
language:
  rust:
    build_defaults:
      exceptions:
        - clauses: [BD-002, BD-004, BD-008]
          route: tools/dev-fast/config.toml
          adr: docs/adr-012-linux-dev-fast-routing.md
          rationale: Wheels build in manylinux containers without mold.
          review_trigger: Revisit when the wheel images carry mold.
```

This module reads the declaration and the facts the policy checks it against:
whether the route is a Cargo configuration that does carry the parallel
frontend and, on Linux, the linker (so the fast path still meets the
standard), and whether the decision record exists. The policy decides.
"""

from __future__ import annotations

import pathlib
import tomllib
import typing as typ

from concordat.errors import OperationalRuleError

from .cargo_config import (
    RustflagsSource,
    TomlDocument,
    normalise_flags,
    read_cargo_config,
    rustflags_cargo_would_refuse,
    rustflags_sources,
)
from .fs_probe import regular_file_exists
from .rust_surfaces import CONCORDAT_FILENAME, rust_language_mapping

if typ.TYPE_CHECKING:
    import collections.abc as cabc

OPERATION_READ_EXCEPTIONS: typ.Final = "read-build-defaults-exceptions"
DEFAULT_THREADS_FLAG: typ.Final = "-Zthreads=8"
DEFAULT_LINKER_FLAG: typ.Final = "-Clink-arg=-fuse-ld=mold"

ROUTE_OK: typ.Final = "ok"
ROUTE_ABSENT: typ.Final = "absent"
ROUTE_UNREADABLE: typ.Final = "unreadable"


class StandardException(typ.TypedDict):
    """One declared exception and the facts the policy checks it against."""

    clauses: list[str]
    route: str
    adr: str
    rationale: str
    review_trigger: str
    route_state: str
    route_threads: bool
    route_linker: bool
    adr_present: bool


def _error(message: str, checkout: pathlib.Path) -> OperationalRuleError:
    """Create a declaration error with a stable operation boundary."""
    return OperationalRuleError(
        message,
        operation=OPERATION_READ_EXCEPTIONS,
        resource=checkout / CONCORDAT_FILENAME,
    )


def _declared_entries(checkout: pathlib.Path) -> list[object]:
    """Return the declared exception entries, or an empty list when none."""
    rust = rust_language_mapping(checkout)
    if rust is None or "build_defaults" not in rust:
        return []
    build_defaults = rust["build_defaults"]
    if not isinstance(build_defaults, dict):
        message = "language.rust.build_defaults must be a mapping"
        raise _error(message, checkout)
    entries = typ.cast("dict[str, object]", build_defaults).get("exceptions")
    if entries is None:
        return []
    if not isinstance(entries, list):
        message = "language.rust.build_defaults.exceptions must be a list"
        raise _error(message, checkout)
    return typ.cast("list[object]", entries)


def _text(mapping: dict[str, object], key: str, checkout: pathlib.Path) -> str:
    """Return a declared string field, or fail naming the field."""
    value = mapping.get(key)
    if not isinstance(value, str):
        message = f"a build_defaults exception needs a text {key}: {value!r}"
        raise _error(message, checkout)
    return value


def _is_text_list(value: object) -> typ.TypeGuard[list[str]]:
    """Return whether *value* is a list holding only strings."""
    return isinstance(value, list) and all(
        isinstance(item, str) for item in typ.cast("list[object]", value)
    )


def _clauses(mapping: dict[str, object], checkout: pathlib.Path) -> list[str]:
    """Return the declared clause identifiers."""
    value = mapping.get("clauses")
    if not _is_text_list(value):
        message = f"a build_defaults exception needs a list of clauses: {value!r}"
        raise _error(message, checkout)
    return list(value)


def _has_control_character(path: str) -> bool:
    """Return whether *path* holds an ASCII control character."""
    return any(ord(character) < 32 for character in path)


def _safe_relative(path: str) -> bool:
    """Return whether *path* names a file within the checkout."""
    posix = pathlib.PurePosixPath(path)
    is_inside = not posix.is_absolute() and ".." not in posix.parts
    return bool(path) and is_inside and not _has_control_character(path)


def _within(checkout: pathlib.Path, relative: str) -> pathlib.Path | None:
    """Return the checkout file for *relative*, or ``None`` when it escapes."""
    if not _safe_relative(relative):
        return None
    candidate = checkout.joinpath(*pathlib.PurePosixPath(relative).parts)
    if candidate.resolve().is_relative_to(checkout.resolve()):
        return candidate
    return None


class _RouteFacts(typ.NamedTuple):
    """What the route configuration carries."""

    state: str
    has_threads: bool
    has_linker: bool


_ABSENT_ROUTE: typ.Final = _RouteFacts(
    state=ROUTE_ABSENT, has_threads=False, has_linker=False
)
_UNREADABLE_ROUTE: typ.Final = _RouteFacts(
    state=ROUTE_UNREADABLE, has_threads=False, has_linker=False
)


def _route_document(path: pathlib.Path) -> TomlDocument | None:
    """Return the parsed route configuration, or ``None`` when unreadable."""
    try:
        return read_cargo_config(path)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _carries(source: RustflagsSource, wanted: list[str]) -> bool:
    """Return whether *source* names every flag in *wanted*."""
    return all(flag in source["flags"] for flag in wanted)


def _route_facts(
    path: pathlib.Path | None, threads_flag: str, linker_flag: str
) -> _RouteFacts:
    """Read the explicit route configuration the exception relies on."""
    if path is None or not regular_file_exists(
        path, operation=OPERATION_READ_EXCEPTIONS
    ):
        return _ABSENT_ROUTE
    document = _route_document(path)
    # A configuration Cargo refuses to load cannot be a working fast route, so it
    # is unreadable: reading the rest of it would waive clauses for a build that
    # fails.
    if document is None or rustflags_cargo_would_refuse(document) is not None:
        return _UNREADABLE_ROUTE
    sources = rustflags_sources(document)
    wanted_threads = normalise_flags([threads_flag])
    wanted_linker = normalise_flags([linker_flag])
    return _RouteFacts(
        state=ROUTE_OK,
        has_threads=bool(sources)
        and all(_carries(source, wanted_threads) for source in sources),
        has_linker=_linker_is_linux_only(sources, wanted_linker),
    )


def _linker_is_linux_only(sources: list[RustflagsSource], wanted: list[str]) -> bool:
    """Return whether a Linux-only source carries the linker and no other does.

    A linker that ships for Linux alone breaks the build wherever else a source
    reaches (`cfg(unix)` also matches macOS), so any source that names it must
    apply to Linux and nowhere else, and at least one must carry all of it.

    Returns
    -------
    bool
        ``True`` when the linker is named only in Linux-only sources and one of
        them carries it whole.
    """
    naming = [source for source in sources if any(f in source["flags"] for f in wanted)]
    return any(_carries(source, wanted) for source in naming) and all(
        source["linux_only"] for source in naming
    )


def _exception(
    entry: object,
    checkout: pathlib.Path,
    flags: tuple[str, str],
) -> StandardException:
    """Validate one declaration and read the facts behind it."""
    if not isinstance(entry, dict):
        message = f"a build_defaults exception must be a mapping: {entry!r}"
        raise _error(message, checkout)
    mapping = typ.cast("dict[str, object]", entry)
    route = _text(mapping, "route", checkout)
    adr = _text(mapping, "adr", checkout)
    adr_path = _within(checkout, adr)
    facts = _route_facts(_within(checkout, route), *flags)
    return {
        "clauses": _clauses(mapping, checkout),
        "route": route,
        "adr": adr,
        "rationale": _text(mapping, "rationale", checkout),
        "review_trigger": _text(mapping, "review_trigger", checkout),
        "route_state": facts.state,
        "route_threads": facts.has_threads,
        "route_linker": facts.has_linker,
        "adr_present": adr_path is not None
        and regular_file_exists(adr_path, operation=OPERATION_READ_EXCEPTIONS),
    }


def inspect_standard_exceptions(
    checkout: pathlib.Path,
    parameters: cabc.Mapping[str, object] | None = None,
) -> list[StandardException]:
    """Return the recorded build-defaults exceptions declared by *checkout*.

    Parameters
    ----------
    checkout:
        Repository root containing an optional `.concordat` declaration.
    parameters:
        The rule's parameters. Only `threads_flag` and `linker_flag` are read,
        so the route is held to the same flags the standard names.

    Returns
    -------
    list[StandardException]
        One entry per declaration, in the order declared; empty when the
        checkout declares none.

    A malformed declaration raises `OperationalRuleError` under the
    `read-build-defaults-exceptions` operation: a declaration the rule cannot
    read is an operational failure, never a waiver. The raise happens in the
    helpers, so it is described here rather than in a `Raises` section, which
    ruff's `DOC502` rejects for an exception the function does not raise itself.
    """
    resolved = parameters if parameters is not None else {}
    threads = resolved.get("threads_flag")
    linker = resolved.get("linker_flag")
    flags = (
        threads if isinstance(threads, str) else DEFAULT_THREADS_FLAG,
        linker if isinstance(linker, str) else DEFAULT_LINKER_FLAG,
    )
    return [_exception(entry, checkout, flags) for entry in _declared_entries(checkout)]
