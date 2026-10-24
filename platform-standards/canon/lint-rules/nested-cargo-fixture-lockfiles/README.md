# nested-cargo-fixture-lockfiles

Audits the lockfiles of nested-build fixtures. The audit is read-only. It
evaluates a `policy-input/nested-cargo-fixture-lockfiles` envelope built by
`concordat artefact rule run`; it never runs Cargo or reads the network.

## Why

A test that runs `cargo` against a fixture crate compiles whatever that crate's
own lock resolves. If the lock names a registry package the workspace
`Cargo.lock` does not, the nested build compiles units the workspace build
never did, so it starts against a cold cache. In CI that costs runner minutes
and turns a bounded test into one that looks hung. The corpus behind the rule
is rstest-bdd (fixture locks drifting from the workspace lock, fixed in
leynos/rstest-bdd#895) and netsuke (an unlocked fixture with its own workspace,
fixed in leynos/netsuke#913).

## Checks

- **NC-001** (error): a `Cargo.lock` in a fixture directory resolves a registry
  package, by name, version and source, that the root `Cargo.lock` does not.
  Packages with no `source` (path packages) are the fixture's own and never
  count.
- **NC-002** (error): a fixture manifest that declares dependencies has no
  `Cargo.lock` beside it, is outside every workspace's members, and has a
  `[package]`.
- **EN-001** (error, indeterminate): the envelope is not a schema-1
  `policy-input/nested-cargo-fixture-lockfiles` document, the root lock cannot
  be read, or a manifest or lock below the root could not be read or decoded.

## What counts

- A **fixture directory** is one whose root-relative path contains `fixture`,
  `tests/`, `test_support` or `ui/`. Separately resolved products elsewhere are
  not reached.
- A lock or manifest in a **workspace member's** directory is ignored: Cargo
  reads the root's lock only.
- A repository with **no root `Cargo.lock`** has no workspace graph and is
  compliant.
- Hidden directories and `target`, `vendor`, `node_modules`, `build`, `dist`
  and `venv` trees are not entered.

## Exemptions

A fixture that resolves outside the workspace on purpose is listed in the
`exemptions` parameter with its `repository` (`owner/name`, read from the
`origin` remote), its `path` (the directory, relative to the root) and a
`rationale` naming the lane that owns its lock and a `review_trigger` saying
what event sends the exemption back for review (the shape of
`dependencies.family_exceptions`). An exemption does not apply to another
repository. The defaults carry rstest-bdd's three published-GPUI fixtures.

## What the rule declines to judge

It compares lock contents, not what a test then does with the fixture. A
fixture built with a private `CARGO_TARGET_DIR`, or a `cargo` spawned with no
time bound, is outside this package. Path dependencies of a member (or of the
root package) that lie inside the workspace root are members too, as in Cargo,
unless excluded; a `path` entry in `[workspace.dependencies]` that a member
inherits is not followed.
