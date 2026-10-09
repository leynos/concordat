# ADR-003: rust-build-defaults reads the builds that replace its defaults

**Date:** 2026-09-28

**Status:** Accepted

## Context

The `rust-build-defaults` rule package audits the estate's build standard: the
parallel `rustc` frontend, the `mold` linker and the Cranelift codegen backend,
configured as defaults in the `.cargo/config.toml` that Cargo auto-discovers.
It was shipped reading only the files Cargo and rustup discover. The design
document and developers' guide recorded that it deliberately carried no
Makefile facts. A repository whose flags live behind an opt-in Make target has
no configuration and fails on that alone, and reading the Makefile would have
made the rule unrunnable against any checkout the pinned `makeutil` could not
parse.

Two kinds of build replace those defaults after the configuration is in place:

- An assigned `RUSTFLAGS` replaces every `rustflags` source in the
  configuration. The estate's Makefile template assigns
  `RUSTFLAGS="$(RUST_FLAGS)"` with `-D warnings` alone in every gate recipe.
  shared-actions `setup-rust` exports `RUSTFLAGS='-D warnings'` by default to
  every later CI step. Either turns the standard off for the build without
  touching the configuration.
- `-Cinstrument-coverage` is LLVM-only. A coverage run that inherits a
  Cranelift development default fails: on the pinned `nightly-2026-05-28`,
  rustc refuses it outright. Eight of the thirteen Cranelift repositories had a
  `make coverage` recipe that would.

The configuration can be fully compliant while both kinds of build defeat it,
and the configuration alone cannot show either.

## Decision

The package keeps BD-001 to BD-006 reading only what Cargo and rustup discover,
and adds three clauses that read the builds replacing the defaults:

- BD-007: where Cranelift is the development default, every coverage path and
  dev-profile release build selects LLVM.
- BD-008: a Makefile gate recipe that assigns `RUSTFLAGS` restates the fast
  flags.
- BD-009: a workflow step that runs a cargo gate build directly with
  `RUSTFLAGS` set carries the same flags.

The `policy-input/rust-build-defaults` envelope therefore also carries the root
Makefile's `makeutil` report and each workflow decoded as YAML. The objection
that stood before is met by carrying a Makefile `makeutil` refuses as a fact,
`makefile_error`, rather than raising. Only BD-007 and BD-008 read it, and they
report `indeterminate`; the other clauses run as before. The facts are still
parsed, never searched.

The accepted shapes are recorded in the package README, including the rulings
of 2026-09-28:

- A shared-actions `generate-coverage` step selects LLVM itself, and the rule
  accepts it without a step-level override.
- The release clause covers workflow builds in release- or tag-triggered
  workflows only. The Makefile `release` target goes through a pattern rule a
  static reading cannot follow.

## Consequences

The rule now needs `makeutil` on `PATH`, as `rust-makefile-baseline`,
`markdown-formatting-baseline` and `spelling-config-baseline` already do.

A Makefile the pinned `makeutil` only recovers from is `indeterminate` for
BD-007 and BD-008 even where it is valid, which is the fail-closed reading the
other Makefile rules take. netsuke's Makefile is one such case today.

BD-005 no longer reports LLVM on a profile other than `dev`. A dedicated
coverage profile selecting LLVM is one of BD-007's accepted shapes, and
refusing it as "a backend the estate has not adopted" would have made the two
clauses contradict each other. LLVM on `dev` is still refused.
