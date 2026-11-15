"""Find settings outside source that lower the process-environment lint.

TE-004: a `[lints.clippy]` or `[lints.rust]` table in a `Cargo.toml` (or the
workspace's `[workspace.lints]`) that sets the lint, a group that contains it, or
`warnings` to `allow`, and an `-A` or `--allow` flag for them in
`.cargo/config.toml` (or the legacy `.cargo/config`), in `build.rustflags`,
`target.*.rustflags`, or `[env]` `RUSTFLAGS` and `CLIPPY_ARGS`. A `[env]`
`CLIPPY_CONF_DIR` is reported as unreadable instead, because it moves Clippy's
configuration somewhere the audit cannot know.

`lints.workspace = true` inheritance is not modelled: a lints table anywhere in
the checkout is read as applying.
"""

from __future__ import annotations

import typing as typ

from .test_environment_types import ConfigSuppressionFact, strings, table

if typ.TYPE_CHECKING:
    from .test_environment_types import Reader

__all__ = ["config_suppressions"]

CARGO_CONFIG_NAMES: typ.Final = (".cargo/config.toml", ".cargo/config")
FLAG_ENVIRONMENT: typ.Final = ("RUSTFLAGS", "CLIPPY_ARGS")
ALLOW_LEVEL: typ.Final = "allow"


def _level_of(spec: object) -> str:
    """Return the level a lints-table entry sets, in either spelling."""
    return str(table(spec).get("level")) if isinstance(spec, dict) else str(spec)


def _lint_tables(manifest: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """Return each lints table with the prefix its keys are named under."""
    workspace_lints = table(table(manifest.get("workspace")).get("lints"))
    package_lints = table(manifest.get("lints"))
    return [
        (prefix, table(scope.get(tool)))
        for scope in (package_lints, workspace_lints)
        for tool, prefix in (("clippy", "clippy::"), ("rust", ""))
    ]


def _manifest_suppressions(
    path: str, manifest: dict[str, object], silencing: frozenset[str]
) -> list[ConfigSuppressionFact]:
    """Return the lints-table entries that allow the lint or a group."""
    return [
        {"path": path, "detail": f'{prefix}{lint} = "allow" in a lints table'}
        for prefix, entries in _lint_tables(manifest)
        for lint, spec in entries.items()
        if f"{prefix}{lint}" in silencing and _level_of(spec) == ALLOW_LEVEL
    ]


def _flag_words(value: object) -> list[str]:
    """Return the words of a `rustflags` string or list, split on spaces."""
    items = [value] if isinstance(value, str) else strings(value)
    return [word for item in items for word in item.split()]


def _allowed_lints(words: list[str]) -> set[str]:
    """Return the lints a flag list allows: `-A x`, `-Ax`, `--allow x`, `--allow=x`."""
    allowed: set[str] = set()
    for index, word in enumerate(words):
        following = words[index + 1 : index + 2]
        if word in {"-A", "--allow"}:
            allowed.update(following)
        elif word.startswith("--allow="):
            allowed.add(word.removeprefix("--allow="))
        elif word.startswith("-A"):
            allowed.add(word.removeprefix("-A"))
    return allowed


def _flag_lists(config: dict[str, object]) -> list[list[str]]:
    """Return every flag list a Cargo configuration sets."""
    environment = table(config.get("env"))
    values = [
        table(config.get("build")).get("rustflags"),
        *(table(t).get("rustflags") for t in table(config.get("target")).values()),
        *(environment.get(name) for name in FLAG_ENVIRONMENT),
    ]
    return [
        _flag_words(table(value).get("value") if isinstance(value, dict) else value)
        for value in values
    ]


def _config_flag_suppressions(
    path: str, config: dict[str, object], silencing: frozenset[str]
) -> list[ConfigSuppressionFact]:
    """Return the flags in a Cargo configuration that allow the lint."""
    return [
        {"path": path, "detail": f"a flag allows {lint}"}
        for words in _flag_lists(config)
        for lint in sorted(_allowed_lints(words) & silencing)
    ]


def _conf_dir_unknowable(path: str, config: dict[str, object]) -> list[str]:
    """Return the reason, if `CLIPPY_CONF_DIR` makes the configuration unknowable."""
    if "CLIPPY_CONF_DIR" not in table(config.get("env")):
        return []
    reason = (
        f"{path}: [env] CLIPPY_CONF_DIR is set, so the Clippy configuration in "
        "force is unknown"
    )
    return [reason]


def config_suppressions(
    reader: Reader,
    manifests: dict[str, dict[str, object]],
    silencing: frozenset[str],
) -> list[ConfigSuppressionFact]:
    """Return every configuration-level suppression in the checkout.

    Reasons the Clippy configuration cannot be known are appended to the
    reader's `unreadable` list.

    Returns
    -------
    list[ConfigSuppressionFact]
        The lints-table entries and flags that lower the lint.
    """
    found = [
        fact
        for path, manifest in sorted(manifests.items())
        for fact in _manifest_suppressions(path, manifest, silencing)
    ]
    for name in CARGO_CONFIG_NAMES:
        config = reader.toml(reader.checkout / name)
        if config is None:
            continue
        found += _config_flag_suppressions(name, config, silencing)
        reader.unreadable += _conf_dir_unknowable(name, config)
    return found
