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

## Rust policy seed adoption

Repositories adopting the Rust policy seeds should select a merged Concordat
commit containing them. Copy the Clippy configuration into each applicable
Cargo root and add only documented local settings. Retain its seven environment
and current-directory method restrictions, and keep `disallowed_methods` denied
by the binding Clippy gate. Keep a production environment reader and
base-directory seam, inject stubs in tests, and do not replace a lossless
`OsString` adapter with Unicode-only lookup. Adapt the Dependabot seed to the
actual ecosystems and directories, including every Cargo root and local action
directory. Preserve the daily cadence and final minor-and-patch catch-all.

Reconcile label names, descriptions, and colours against the dated label
snapshot, preserving unrelated labels. See the
[users' guide](users-guide.md#syncing-canonical-platform-standards-artefacts)
for adoption details. Repositories that do not adopt these seeds need no action.

## Proposed rust-build-defaults rule-package 0.2.1 update

The proposed 0.2.1 update refines BD-003 for build-wide and target-local
backend flags, BD-007's proof of release-profile LLVM selection, and BD-008 for
direct installer-managed Whitaker commands. This is a rule-package version, not
a Concordat CLI release. Until the owning change is merged and an immutable
0.2.1 package revision is available and adopted, this is a proposed migration
boundary, not a released audit contract. After adoption, re-run the audit: a
target-local backend no longer has to appear in unrelated target sources, while
a build-wide backend must remain in replacing sources. A recognized direct
Whitaker route must have a statically known isolation boundary and no
development-flag contamination; unresolved routes and separate profile backend
selections remain `indeterminate`. Ordinary Cargo builds and test runners
retain the development flag requirement. Valid root-manifest selections are
reported separately from `.cargo/config.toml`; a present but invalid relevant
selection is recorded as an error fact. That error makes Whitaker isolation
indeterminate and prevents the audit from proving LLVM for release-profile
coverage. A root release-profile LLVM selection is direct proof, while
Cranelift is noncompliant; package-level LLVM is not proof of the profile
default. The release-profile guard checks package overrides for the selected
profile in both root `Cargo.toml` and `.cargo/config.toml`; profile-level LLVM
alone cannot prove coverage if a relevant package override is unresolved or
selects Cranelift.
