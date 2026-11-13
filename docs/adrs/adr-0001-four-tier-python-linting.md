# ADR-001: Four-tier Python linting

**Date:** 2026-08-24

**Status:** Accepted

## Context

Python quality checks must give contributors the same result locally and in
continuous integration. A style-only lint gate cannot identify production code
that is no longer reachable, while a dead-code tool must not let test-only
references keep production symbols live.

## Decision

`make lint` runs four sequential source-lint tiers:

1. Ruff checks Python source rules.
2. The shared `typos-config-builder` gate regenerates `typos.toml` from the
   live shared dictionary and the `typos.local.toml` overlay.
3. The same gate enforces the resulting en-GB spelling policy.
4. Skylos performs strict production dead-code detection.

Skylos 4.33.2 scans `concordat` and `scripts`, excludes `tests`, and blocks the
gate on findings. It runs in an isolated `uv tool run` environment pinned to
Python 3.14. Skylos parses source through that interpreter's AST, so the pin
prevents phantom results when project syntax is newer than an older runtime can
parse. Scan-only options stay in `$(SKYLOS)`; `$(SKYLOS_CLI)` remains a bare
command so `skylos whitelist <symbol> --reason <reason>` can dispatch its
subcommand before scan options.

Investigate every report. Remove genuine dead code. For an implicit runtime
caller, prefer a typed `[tool.skylos.dead_code]` entry-point rule with its full
symbol name and caller-specific reason. Add a documented allow-list entry only
when that type-based model cannot represent a verified boundary. The helper
requires `SYMBOL` and `REASON`; it deliberately does not use `NAME`, which WSL
sets to the host name. Both helper values must include a non-whitespace
character, preventing empty-looking allow-list entries.

## Consequences

Skylos is a blocking part of local and CI linting without adding it to the
application dependency set. The Makefile contract is parsed by pinned Makeutil,
and every isolated CI job that runs the full pytest suite installs the same
Makeutil revision, nightly toolchain, and Polonius flag independently.

## Addendum (2026-09-25): Makeutil installed from a verified release

The consequence above, that each suite job installs the same Makeutil revision,
nightly toolchain and Polonius flag, is superseded. Makeutil now publishes
static binaries, and every CI job that runs the full pytest suite installs the
same v0.1.0 `x86_64-unknown-linux-musl` release asset through
`scripts/install_release_binary.py`. The script checks the asset against the
SHA-256 digest pinned in the workflow, not the release's own checksum file, and
installs nothing on a mismatch. The jobs no longer compile Makeutil, which took
35 to 75 seconds per run and needed a nightly toolchain. The shared release,
digest and install command are what now keep the jobs identical, and
`tests/unit/test_skylos_lint_contract.py` holds them to that.
