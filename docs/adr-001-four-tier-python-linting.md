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
2. The spelling-policy generator refreshes the reviewed shared policy.
3. Pinned `typos` enforces the resulting en-GB spelling policy.
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
