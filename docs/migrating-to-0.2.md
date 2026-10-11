# Migrating to 0.2

Release 0.2 adds the `rust-toolchain-policy` rule package (RT-022 to RT-027).
The audit is read-only and no existing rule changes, so nothing needs migrating
for a repository that does not run it. This note is for a repository adopting
the rule.

## What the rule asks for

Run `concordat artefact rule run rust-toolchain-policy --repo .` and bring the
findings to zero:

- **RT-022**: pin `rust-toolchain.toml` to a release (`1.93.1`) or a dated
  nightly (`nightly-2026-09-20`), not `stable` or `nightly`.
- **RT-023**: keep a pinned nightly within 56 days. Let Dependabot move it
  rather than bumping by hand. The window is the rule's `window_days` parameter
  (56 by default); a repository that needs another value sets it in the rule's
  manifest parameters, together with the accepted Dependabot intervals
  (`dependabot_intervals`, `daily` and `weekly` by default).
- **RT-024**: add a `rust-toolchain` entry to `.github/dependabot.yml`, daily
  or weekly, in the same minor-and-patch group shape as the cargo entry.
- **RT-025**: declare `rust-version` on each publishable library crate, or mark
  an application crate `publish = false`.
- **RT-026**: exercise each declared MSRV in CI with one
  `cargo check --lib --locked`, not a second test run.
- **RT-027**: name no dated nightly in a workflow other than the pinned one.

## A nightly that cannot move

A repository whose nightly is coupled to compiler internals records a section in
`docs/developers-guide.md` whose heading contains "Toolchain policy" and which
names the pinned channel. That exempts RT-023, RT-024 and RT-025 for as long as
the section names the current pin, so each bump must update it.

## Applicability

A repository with no governed Rust surface is outside the rule. Workspace
members are judged where `.concordat` declares them under
`language.rust.surfaces`. RT-006 (a nightly within a year) remains until RT-023
is live across the estate; a later release retires it.
