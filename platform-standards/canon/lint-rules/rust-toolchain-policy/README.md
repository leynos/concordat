# rust-toolchain-policy

Audits the Rust toolchain a repository builds with. The audit is read-only. It
evaluates an envelope built by `concordat artefact rule run`: the pin, the
Cargo packages, the Dependabot `rust-toolchain` entries, the toolchains the
workflows name, a recorded exception, and the date the audit runs.

## RT-022

`rust-toolchain.toml` must pin `channel` exactly: a release (`1.93.1`, `1.93`)
or a dated nightly (`nightly-2026-09-20`). No file, no channel, `stable`,
`nightly` and `beta` are noncompliant, because rustup resolves a floating
channel at install time, so the build changes without a commit and Dependabot
has no value to move. A file that cannot be read is indeterminate.

## RT-023

A dated nightly is at most `window_days` old (56 by default). A numeric release
has no date, so the clause does not apply to it. The window is inclusive: a pin
exactly `window_days` old passes.

## RT-024

`.github/dependabot.yml` has a `rust-toolchain` update entry whose directory is
the pin's (`/`) and whose `schedule.interval` is one of `dependabot_intervals`
(`daily` and `weekly` by default). A missing configuration is noncompliant; one
that cannot be decoded is indeterminate.

## RT-025

Every crate that is publishable (`publish` is not `false` or an empty list) and
builds a library (a `[lib]` table or `src/lib.rs`) declares `rust-version`,
directly or by `rust-version.workspace = true` with a workspace value. A
warning: an MSRV is a promise to downstream users, and an unpublished or
binary-only crate makes none.

## RT-026

Each declared MSRV is exercised by CI. It is when any of these holds: a
workflow names a numeric toolchain with the same `major.minor`; the pin is that
version; a job id, a job name or a step command is about the MSRV (`msrv`,
`cargo-msrv`); or a step that selects a toolchain reads `rust-version`. A
workflow that cannot be decoded makes the clause indeterminate. The estate's
cost rule for the MSRV job is `cargo check --all-targets` on the MSRV
toolchain, never a second test run.

## RT-027

No workflow names a dated nightly other than the pinned one. Numeric toolchains
are not compared: they are usually a host tool's toolchain.

## Exceptions

A repository whose nightly cannot move (whitaker, coupled to `ra_ap_syntax` and
a vendored `clippy_utils`) records a section in `docs/developers-guide.md`
whose heading contains "Toolchain policy" and which names the pinned channel.
That exempts RT-023, RT-024 and RT-025 for as long as the section names the
current pin, so each move of the pin re-justifies it: a nightly coupled to
compiler internals cannot build on a stable release, so there is no stable MSRV
to declare. RT-022, RT-026 and RT-027 still apply.

## What the rule declines to judge

- Workspace members the `.concordat` file does not declare as Rust surfaces.
- Toolchain files below the root, and local actions under `.github/actions`.
- Whether a Dependabot bump pull request has merged: the audit reads the
  repository, not the pull request list.
