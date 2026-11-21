# rust-toolchain-policy

Audits the Rust toolchain a repository builds with. The audit is read-only. It
evaluates an envelope built by `concordat artefact rule run`: the pin, the
Cargo packages, the Dependabot `rust-toolchain` entries, the toolchains the
workflows name, the dated nightlies the workflows and root `Makefile` spell
out, a recorded exception, and the date the audit runs.

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
binary-only crate makes none. `publish.workspace = true` takes the workspace's
value. A `src/lib.rs` the audit cannot examine (an occupied path, a dangling
link, a refusal) makes the crate indeterminate rather than library-free.

## RT-026

Each declared MSRV is exercised by CI. It is when any of these holds: a
workflow names a numeric toolchain with the same `major.minor`; the pin is that
version; a job id, a job name or a step command is about the MSRV (`msrv`,
`cargo-msrv`); or a step that selects a toolchain reads `rust-version`. A
`${{ matrix.KEY }}` reference counts every value the job's matrix gives that
key, `include` legs too; quote a version in YAML (`"1.80"`), because an unquoted
`1.80` decodes as the number 1.8. A workflow that cannot be decoded makes the
clause indeterminate. The estate's cost rule for the MSRV job is
`cargo check --all-targets` on the MSRV toolchain, never a second test run.

## RT-027

No workflow names a dated nightly other than the pinned one. Numeric toolchains
are not compared: they are usually a host tool's toolchain. A date a workflow
reaches through a `${{ env.NAME }}` entry is deliberately separate, as RT-028
treats it, so it is not compared with the pin; a date written inline still is.

## RT-028

No workflow or root `Makefile` restates a dated nightly literal. A date spelled
out in the file drifts the moment Dependabot moves the pin, and in a
nightly-pinned repository it moves daily, so the rule reports every restated
date — including one that is correct today, because that is what makes the
drift silent tomorrow. `ci.yml` adding the Cranelift component to
`nightly-2026-10-30` while the pin was `nightly-2026-11-05`, and `release.yml`
naming the nightly twice, are the two shapes this clause exists for.

The reader parses rather than matching lines: a workflow is walked as a decoded
document, so a nightly in a comment or a step's name is not CI using it, and a
reformat does not change the verdict. Only the places a workflow selects a
toolchain are read: a step's `run` command, a toolchain action's `toolchain`
input, and the action's own ref. A `run` script is split into shell commands,
so text an `echo` prints or a here-document feeds to `cat` is not a selection,
while an `export RUSTUP_TOOLCHAIN=...` is one and a `$(cargo ...)` or
backquoted substitution runs its own command even inside double quotes, as a
parenthesized group does. A `${{ matrix.NAME }}` toolchain is resolved to every
leg the job's matrix declares, `include` legs included. The `Makefile` is read
from `makeutil`'s report, with each recipe read as written; a command that is
itself a variable is expanded and tokenized so its selections are read.

A literal is declared as deliberately separate by binding it to a name: a
Makefile variable assigned the date itself
(`DEV_FAST_TOOLCHAIN ?= nightly-2026-08-23`, used as `$(DEV_FAST_TOOLCHAIN)`),
or a workflow `env` entry a command reaches as `${{ env.DEV_FAST_TOOLCHAIN }}`.
Such a reference draws no finding, which is how cuprum keeps a fast toolchain
beside a `rust/` directory pinned to stable. A date reached through one
variable from another is reported, because it is not declared where it is used;
so is a literal written inline, even when an `env` entry carries the same date
— that second copy is the one that drifts. A recipe reference to a variable the
Makefile never assigns, where no included file could, leaves the toolchain
unknown rather than dated, so it is indeterminate.

Severity is `literal_severity` (`warning` by default, promoting to `error` once
the estate has been surveyed). A workflow or `Makefile` that cannot be read is
indeterminate.

## Exceptions

A repository whose nightly cannot move (whitaker, coupled to `ra_ap_syntax` and
a vendored `clippy_utils`) records a section in `docs/developers-guide.md`
whose heading contains "Toolchain policy" and which names the pinned channel.
That exempts RT-023, RT-024, RT-025 and RT-028 for as long as the section names
the current pin, so each move of the pin re-justifies it: a nightly coupled to
compiler internals cannot build on a stable release, so there is no stable MSRV
to declare, and the toolchain moves with something other than the pin. RT-022,
RT-026 and RT-027 still apply.

## What the rule declines to judge

- Workspace members the `.concordat` file does not declare as Rust surfaces.
- Toolchain files below the root, and local actions under `.github/actions`.
- Makefiles below the root. RT-028 reads the root `Makefile` only, which is the
  one the repository's own recipes run from.
- Whether a Dependabot bump pull request has merged: the audit reads the
  repository, not the pull request list.
