"""Extract build-standard facts from a checkout's `.cargo/config.toml`.

Cargo auto-discovers this file, which is what makes the estate's build
standard a default rather than something a Make target opts into. The policy
needs three readings of it, and none of them survives a textual search: which
`rustflags` sources exist and what each one carries, which of those sources
apply on Linux, and whether any of the three routes to a codegen backend is
taken.

Normalisation happens here rather than in Rego because it is a property of
Cargo's own reading. Cargo accepts `rustflags` as an array or as one
space-separated string, and accepts `-C` and its value as either one token or
two; the estate writes the same linker flag three of those ways today. A
reader that recognised only netsuke's spelling would fail axinite, which is
the one other repository already carrying mold.
"""

from __future__ import annotations

import tomllib
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

# Cargo reads this name first and falls back to the extensionless spelling, so
# both are candidates and the first that exists is the file Cargo would use.
CONFIG_RELATIVE_PATHS: typ.Final = (".cargo/config.toml", ".cargo/config")

# rustc's single-letter flags that take a separate value. Only these are
# rejoined with the token that follows: joining an arbitrary flag would
# manufacture a flag the configuration never contained.
VALUE_TAKING_FLAGS: typ.Final = frozenset({"-A", "-C", "-D", "-F", "-L", "-W", "-Z"})

BACKEND_KEY: typ.Final = "codegen-backend"
BACKEND_FLAG_PREFIX: typ.Final = "-Zcodegen-backend="

type TomlDocument = dict[str, object]


class RustflagsSource(typ.TypedDict):
    """One `rustflags` table Cargo would consult, with its normalised flags.

    ``linux`` says the source applies when building for Linux; ``classified``
    says the reader could decide that at all. An unclassified key is neither
    Linux nor not-Linux, and the policy reports it rather than assuming.
    """

    name: str
    kind: str
    key: str | None
    linux: bool
    classified: bool
    flags: list[str]


class CodegenBackendSelection(typ.TypedDict):
    """One codegen backend selection, and the route that selected it."""

    source: str
    profile: str | None
    backend: str


class CargoConfigFacts(typ.TypedDict):
    """The `.cargo/config.toml` readings the build-defaults policy consumes."""

    path: str
    sources: list[RustflagsSource]
    backends: list[CodegenBackendSelection]
    unstable_codegen_backend: bool | None
    parse_error: str | None


def normalise_flags(value: object) -> list[str]:
    """Return *value* as the flag list Cargo would read from it.

    A string is split on whitespace, an array is taken as written, and a
    value-taking flag standing alone is rejoined with the token that follows
    it. Anything Cargo does not read as flags yields no flags.

    Returns
    -------
    list[str]
        The normalised flags, in the order the configuration gave them.
    """
    tokens = _flag_tokens(value)
    joined: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        follower = tokens[index + 1] if index + 1 < len(tokens) else None
        if token in VALUE_TAKING_FLAGS and follower is not None:
            joined.append(token + follower)
            index += 2
            continue
        joined.append(token)
        index += 1
    return joined


def _flag_tokens(value: object) -> list[str]:
    """Return the raw flag tokens Cargo would read from *value*.

    Returns
    -------
    list[str]
        The tokens, before any value-taking flag is rejoined with its value.
    """
    match value:
        case str():
            return value.split()
        case list():
            return [
                item
                for item in typ.cast("list[object]", value)
                if isinstance(item, str)
            ]
        case _:
            return []


def read_cargo_config(path: pathlib.Path) -> TomlDocument:
    """Return the parsed TOML document at *path*.

    A read or decode failure propagates to the caller, which decides whether
    an unreadable configuration is an absence or a fail-closed finding.

    Returns
    -------
    TomlDocument
        The parsed configuration document.
    """
    with path.open("rb") as handle:
        return tomllib.load(handle)


def locate_cargo_config(checkout: pathlib.Path) -> pathlib.Path | None:
    """Return the Cargo configuration file Cargo would discover, if any.

    Returns
    -------
    pathlib.Path | None
        The first candidate that is a regular file, or ``None``.
    """
    for relative in CONFIG_RELATIVE_PATHS:
        candidate = checkout / relative
        if candidate.is_file():
            return candidate
    return None


def inspect_cargo_config(checkout: pathlib.Path) -> CargoConfigFacts | None:
    """Return the build-standard facts for *checkout*, or ``None`` if absent.

    A file that exists but cannot be read or parsed still returns facts, with
    ``parse_error`` set and no sources: the policy must be able to tell an
    unreadable configuration (indeterminate) from a missing one (a
    configuration that cannot possibly carry the standard).

    Returns
    -------
    CargoConfigFacts | None
        The extracted facts, or ``None`` when no configuration file exists.
    """
    path = locate_cargo_config(checkout)
    if path is None:
        return None
    relative = path.relative_to(checkout).as_posix()
    try:
        document = read_cargo_config(path)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return {
            "path": relative,
            "sources": [],
            "backends": [],
            "unstable_codegen_backend": None,
            "parse_error": str(error),
        }
    sources = rustflags_sources(document)
    return {
        "path": relative,
        "sources": sources,
        "backends": codegen_backends(document, sources),
        "unstable_codegen_backend": _unstable_codegen_backend(document),
        "parse_error": None,
    }


def rustflags_sources(document: TomlDocument) -> list[RustflagsSource]:
    """Return every `rustflags` source Cargo would consult in *document*.

    Returns
    -------
    list[RustflagsSource]
        The `[build]` source, when present, followed by each target table.
    """
    sources: list[RustflagsSource] = []
    build = document.get("build")
    if isinstance(build, dict) and "rustflags" in build:
        sources.append({
            "name": "build",
            "kind": "build",
            "key": None,
            # `[build] rustflags` applies wherever no target table matches, so
            # it is not a Linux source: on Linux a matching target table
            # replaces it outright.
            "linux": False,
            "classified": True,
            "flags": normalise_flags(typ.cast("dict[str, object]", build)["rustflags"]),
        })
    target = document.get("target")
    if not isinstance(target, dict):
        return sources
    for key, table in sorted(typ.cast("dict[str, object]", target).items()):
        # Direct children only. A `rustflags` key beneath `[target.<triple>.
        # <links>]` is a build-script override, which Cargo does not read as
        # target flags.
        if not isinstance(table, dict) or "rustflags" not in table:
            continue
        classification = classify_target_key(key)
        sources.append({
            "name": f"target.{key}",
            "kind": "target",
            "key": key,
            "linux": classification.is_linux,
            "classified": classification.is_classified,
            "flags": normalise_flags(typ.cast("dict[str, object]", table)["rustflags"]),
        })
    return sources


class TargetClassification(typ.NamedTuple):
    """Whether a target key applies on Linux, and whether that was decidable."""

    is_linux: bool
    is_classified: bool


def classify_target_key(key: str) -> TargetClassification:
    """Decide whether the target table named by *key* applies on Linux.

    Two spellings are decidable and both appear in the estate: an explicit
    target triple, whose operating-system field is part of the name, and a
    `cfg` expression naming `target_os` or a family that includes Linux.
    Anything else — a `target_env` predicate, a custom JSON target — is left
    unclassified so the policy can fail closed rather than guess.

    Returns
    -------
    TargetClassification
        The Linux verdict and whether the key could be classified at all.
    """
    expression = _cfg_expression(key)
    if expression is None:
        # An explicit triple: `<arch>-<vendor>-<os>[-<env>]`.
        return TargetClassification(is_linux="-linux" in key, is_classified=True)
    collapsed = "".join(expression.split())
    if 'target_os="linux"' in collapsed:
        return TargetClassification(is_linux=True, is_classified=True)
    if "unix" in collapsed:
        # `cfg(unix)` covers Linux and macOS alike. It is a Linux source, and
        # the policy's "only on Linux" reading has to account for that.
        return TargetClassification(is_linux=True, is_classified=True)
    if _names_another_os(collapsed):
        return TargetClassification(is_linux=False, is_classified=True)
    return TargetClassification(is_linux=False, is_classified=False)


def _cfg_expression(key: str) -> str | None:
    """Return the body of a `cfg(...)` key, or ``None`` for a triple."""
    stripped = key.strip()
    if stripped.startswith("cfg(") and stripped.endswith(")"):
        return stripped[len("cfg(") : -1]
    return None


_OTHER_OS_MARKERS: typ.Final = ("windows", "macos", "darwin", "ios", "wasm", "android")


def _names_another_os(collapsed: str) -> bool:
    """Report whether a collapsed `cfg` body names an operating system Linux is not."""
    return any(marker in collapsed for marker in _OTHER_OS_MARKERS)


def codegen_backends(
    document: TomlDocument,
    sources: list[RustflagsSource],
) -> list[CodegenBackendSelection]:
    """Return every codegen backend selection in *document*.

    Three routes, because closing one alone leaves the others open: a
    `codegen-backend` key on a profile, the same key on a package override
    beneath one, and `-Zcodegen-backend=` inside any `rustflags` source. The
    last would change the backend without mentioning a profile at all.

    Returns
    -------
    list[CodegenBackendSelection]
        Profile selections first, then those carried by flags.
    """
    selections = list(_profile_backends(document))
    selections.extend(_rustflags_backends(sources))
    return selections


def _profile_backends(
    document: TomlDocument,
) -> typ.Iterator[CodegenBackendSelection]:
    """Yield the backend selections made by profile keys."""
    profiles = document.get("profile")
    if not isinstance(profiles, dict):
        return
    for name, table in sorted(typ.cast("dict[str, object]", profiles).items()):
        if not isinstance(table, dict):
            continue
        profile = typ.cast("dict[str, object]", table)
        direct = profile.get(BACKEND_KEY)
        if isinstance(direct, str):
            yield {
                "source": f"profile.{name}",
                "profile": name,
                "backend": direct,
            }
        yield from _package_override_backends(name, profile)


def _package_override_backends(
    name: str,
    profile: dict[str, object],
) -> typ.Iterator[CodegenBackendSelection]:
    """Yield the backend selections made by package overrides beneath a profile."""
    overrides = profile.get("package")
    if not isinstance(overrides, dict):
        return
    for spec, table in sorted(typ.cast("dict[str, object]", overrides).items()):
        if not isinstance(table, dict):
            continue
        backend = typ.cast("dict[str, object]", table).get(BACKEND_KEY)
        if isinstance(backend, str):
            yield {
                "source": f"profile.{name}.package.{spec}",
                "profile": name,
                "backend": backend,
            }


def _rustflags_backends(
    sources: list[RustflagsSource],
) -> typ.Iterator[CodegenBackendSelection]:
    """Yield the backend selections carried by `rustflags` tokens."""
    for source in sources:
        for flag in source["flags"]:
            if flag.startswith(BACKEND_FLAG_PREFIX):
                yield {
                    "source": source["name"],
                    "profile": None,
                    "backend": flag[len(BACKEND_FLAG_PREFIX) :],
                }


def _unstable_codegen_backend(document: TomlDocument) -> bool | None:
    """Return the `[unstable] codegen-backend` gate, or ``None`` when absent."""
    unstable = document.get("unstable")
    if not isinstance(unstable, dict):
        return None
    value = typ.cast("dict[str, object]", unstable).get(BACKEND_KEY)
    return value if isinstance(value, bool) else None
